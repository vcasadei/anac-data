import _bootstrap  # noqa: F401

import gzip
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from anac_data import packaging
from anac_data.paths import CHUNK_LIMIT_BYTES

# Random bytes barely compress, so a payload's compressed size stays close to
# its original size - which makes chunk-count arithmetic predictable in tests.
INCOMPRESSIBLE = os.urandom(20_000)
# Repetitive text compresses hard, like the real CSVs do.
COMPRESSIBLE = (b"SBGR;SBSP;2026-07-01 10:00;REALIZADO\r\n" * 4000)


class PackagingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.dist = self.tmp / "dist" / "estatisticos"

    def source(self, data: bytes, name="Dados_Estatisticos.csv") -> Path:
        path = self.tmp / "raw" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class DeterministicCompressionTest(PackagingTestCase):
    def test_compressing_the_same_bytes_twice_is_byte_identical(self):
        first = packaging.compress_bytes(COMPRESSIBLE)
        time.sleep(1.1)  # a clock-derived timestamp would differ by now
        second = packaging.compress_bytes(COMPRESSIBLE)
        self.assertEqual(first, second)

    def test_unpinned_gzip_would_not_have_been_deterministic(self):
        """Guards the reason mtime is pinned, so nobody removes it.

        Note `gzip.compress` already pins mtime=0; it is `GzipFile` with a
        default mtime - which is what this module uses, to stream - that takes
        the time from the clock.
        """

        def unpinned(data):
            buffer = io.BytesIO()
            with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6) as h:
                h.write(data)
            return buffer.getvalue()

        a = unpinned(COMPRESSIBLE)
        time.sleep(1.1)
        b = unpinned(COMPRESSIBLE)
        self.assertNotEqual(a, b, "GzipFile stopped embedding a timestamp")

    def test_the_gzip_header_carries_no_filename(self):
        """FNAME would otherwise leak the temporary file's name into the bytes."""
        source = self.source(COMPRESSIBLE)
        out = self.tmp / "whatever-name-this-is.gz"
        packaging.compress_file(source, out)
        flags = out.read_bytes()[3]
        self.assertEqual(0, flags & 0x08, "gzip header has FNAME set")

    def test_compressed_bytes_round_trip(self):
        self.assertEqual(
            COMPRESSIBLE, gzip.decompress(packaging.compress_bytes(COMPRESSIBLE))
        )

    def test_streamed_file_compression_is_deterministic_and_round_trips(self):
        source = self.source(COMPRESSIBLE)
        first, second = self.tmp / "a.gz", self.tmp / "b.gz"
        packaging.compress_file(source, first)
        time.sleep(1.1)
        packaging.compress_file(source, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(COMPRESSIBLE, gzip.decompress(first.read_bytes()))

    def test_streamed_and_in_memory_compression_agree(self):
        source = self.source(COMPRESSIBLE)
        streamed = self.tmp / "s.gz"
        packaging.compress_file(source, streamed)
        self.assertEqual(
            packaging.compress_bytes(COMPRESSIBLE), streamed.read_bytes()
        )


class ChunkLayoutTest(PackagingTestCase):
    def test_chunk_names_are_zero_padded_and_sort_into_order(self):
        names = [packaging.chunk_name("f.csv", i) for i in range(1, 13)]
        self.assertEqual("f.csv.gz.part001", names[0])
        self.assertEqual("f.csv.gz.part012", names[-1])
        # Sorting as text must equal concatenation order.
        self.assertEqual(names, sorted(names))

    def test_every_chunk_but_the_last_is_exactly_the_chunk_size(self):
        source = self.source(INCOMPRESSIBLE)
        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1500
        )

        self.assertEqual("packaged", result.outcome)
        self.assertGreater(len(result.chunks), 12)
        for chunk in result.chunks[:-1]:
            self.assertEqual(1500, chunk.size)
        self.assertGreater(result.chunks[-1].size, 0)
        self.assertLessEqual(result.chunks[-1].size, 1500)

    def test_chunks_sorted_by_name_reassemble_the_original(self):
        source = self.source(INCOMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1500
        )

        parts = sorted(self.dist.glob("*.gz.part*"))
        self.assertGreater(len(parts), 12, "need 10+ to exercise padding")
        rebuilt = gzip.decompress(b"".join(p.read_bytes() for p in parts))
        self.assertEqual(INCOMPRESSIBLE, rebuilt)

    def test_chunks_land_in_the_dataset_directory(self):
        source = self.source(COMPRESSIBLE)
        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        self.assertEqual(self.dist, result.dist_dir)
        for chunk in result.chunks:
            self.assertTrue((self.dist / chunk.name).is_file())

    def test_no_temporary_compressed_file_is_left_behind(self):
        source = self.source(COMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        self.assertEqual([], list(self.dist.glob("*.tmp")))
        self.assertEqual([], list(self.dist.glob("*.gz.tmp")))

    def test_more_chunks_than_the_name_format_allows_fails_loudly(self):
        source = self.source(INCOMPRESSIBLE)
        with self.assertRaises(packaging.PackagingError) as caught:
            packaging.package_file(
                source, self.dist, dataset="estatisticos", chunk_size=8
            )
        message = str(caught.exception)
        self.assertIn(str(packaging.MAX_CHUNKS), message)
        self.assertIn("--chunk-size", message)


class ThresholdTest(PackagingTestCase):
    def test_a_file_at_or_under_the_limit_is_not_chunked(self):
        source = self.source(b"x" * 1000)
        result = packaging.package_file(
            source, self.dist, dataset="vra", chunk_size=1000
        )

        self.assertEqual("not_needed", result.outcome)
        self.assertEqual([], result.chunks)
        self.assertFalse(self.dist.exists())
        # The original is untouched.
        self.assertEqual(b"x" * 1000, source.read_bytes())

    def test_a_file_over_the_limit_is_chunked_and_the_original_kept(self):
        source = self.source(COMPRESSIBLE)
        before = source.read_bytes()
        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )

        self.assertEqual("packaged", result.outcome)
        self.assertEqual(before, source.read_bytes())

    def test_an_overridden_limit_governs_the_decision(self):
        source = self.source(b"y" * 5000)
        under = packaging.package_file(
            source, self.dist, dataset="d", chunk_size=1024, limit=99_999
        )
        self.assertEqual("not_needed", under.outcome)

        over = packaging.package_file(
            source, self.dist, dataset="d", chunk_size=1024, limit=100
        )
        self.assertEqual("packaged", over.outcome)

    def test_needs_packaging_boundary(self):
        self.assertFalse(packaging.needs_packaging(100, 100))
        self.assertTrue(packaging.needs_packaging(101, 100))

    def test_default_limit_is_the_configured_chunk_limit(self):
        self.assertFalse(packaging.needs_packaging(CHUNK_LIMIT_BYTES))
        self.assertTrue(packaging.needs_packaging(CHUNK_LIMIT_BYTES + 1))

    def test_a_missing_source_is_reported_not_guessed(self):
        with self.assertRaises(packaging.PackagingError) as caught:
            packaging.package_file(
                self.tmp / "nope.csv", self.dist, dataset="estatisticos"
            )
        message = str(caught.exception)
        self.assertIn("estatisticos", message)
        self.assertIn("fetch", message)
        self.assertFalse(self.dist.exists())


class MetadataTest(PackagingTestCase):
    def setUp(self):
        super().setUp()
        self.source_path = self.source(COMPRESSIBLE)
        self.result = packaging.package_file(
            self.source_path, self.dist, dataset="estatisticos", chunk_size=1024
        )

    def test_manifest_records_every_field(self):
        manifest = json.loads((self.dist / "MANIFEST.json").read_text())

        self.assertEqual(packaging.FORMAT_VERSION, manifest["format_version"])
        self.assertEqual("estatisticos", manifest["dataset"])
        self.assertEqual("Dados_Estatisticos.csv", manifest["original"]["name"])
        self.assertEqual(len(COMPRESSIBLE), manifest["original"]["size"])
        self.assertEqual(
            hashlib.sha256(COMPRESSIBLE).hexdigest(),
            manifest["original"]["sha256"],
        )
        self.assertEqual(1024, manifest["chunk_size"])
        self.assertGreater(manifest["compressed"]["size"], 0)
        self.assertEqual(64, len(manifest["compressed"]["sha256"]))
        self.assertEqual(len(self.result.chunks), len(manifest["chunks"]))
        for entry in manifest["chunks"]:
            self.assertEqual(64, len(entry["sha256"]))
            self.assertGreater(entry["size"], 0)
            self.assertTrue((self.dist / entry["name"]).is_file())

    def test_compressed_digest_matches_the_concatenated_chunks(self):
        manifest = json.loads((self.dist / "MANIFEST.json").read_text())
        joined = b"".join(
            (self.dist / c["name"]).read_bytes() for c in manifest["chunks"]
        )
        self.assertEqual(
            manifest["compressed"]["sha256"], hashlib.sha256(joined).hexdigest()
        )
        self.assertEqual(manifest["compressed"]["size"], len(joined))

    def test_checksum_listing_carries_the_scalars_as_comments(self):
        text = (self.dist / "checksums.sha256").read_text(encoding="utf-8")

        self.assertIn("# original-name: Dados_Estatisticos.csv", text)
        self.assertIn(
            f"# original-sha256: {hashlib.sha256(COMPRESSIBLE).hexdigest()}", text
        )
        self.assertIn(f"# original-size: {len(COMPRESSIBLE)}", text)
        self.assertIn("# chunk-size: 1024", text)

    def test_sha256sum_verifies_the_listing_with_no_parsing(self):
        """The POSIX script's whole verification step is this one command."""
        tool = _checksum_tool()
        if tool is None:
            self.skipTest("neither sha256sum nor shasum is available")
        done = subprocess.run(
            tool + ["-c", "checksums.sha256"],
            cwd=self.dist,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(len(self.result.chunks), done.stdout.count(": OK"))

    def test_sha256sum_detects_a_corrupt_chunk(self):
        tool = _checksum_tool()
        if tool is None:
            self.skipTest("neither sha256sum nor shasum is available")
        victim = self.dist / self.result.chunks[0].name
        victim.write_bytes(b"\x00" * victim.stat().st_size)

        done = subprocess.run(
            tool + ["-c", "checksums.sha256"],
            cwd=self.dist,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, done.returncode)
        self.assertIn(self.result.chunks[0].name, done.stdout + done.stderr)

    def test_no_stray_temporary_metadata_files(self):
        self.assertEqual([], list(self.dist.glob("*.tmp")))


class IdempotenceTest(PackagingTestCase):
    def test_repackaging_unchanged_data_reports_unchanged_and_rewrites_nothing(self):
        source = self.source(COMPRESSIBLE)
        first = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        digests = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(self.dist.iterdir())
        }
        mtimes = {p.name: p.stat().st_mtime_ns for p in sorted(self.dist.iterdir())}
        time.sleep(1.1)

        second = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )

        self.assertEqual("unchanged", second.outcome)
        after = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(self.dist.iterdir())
        }
        self.assertEqual(digests, after)
        self.assertEqual(
            mtimes, {p.name: p.stat().st_mtime_ns for p in sorted(self.dist.iterdir())}
        )
        self.assertEqual(
            [c.sha256 for c in first.chunks], [c.sha256 for c in second.chunks]
        )

    def test_force_repackages_even_when_unchanged(self):
        source = self.source(COMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024, force=True
        )
        self.assertEqual("packaged", result.outcome)

    def test_changed_content_is_repackaged(self):
        source = self.source(COMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        source.write_bytes(COMPRESSIBLE + b"SBSV;SBRF;2026-07-31 23:00;CANCELADO\r\n")

        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        self.assertEqual("packaged", result.outcome)
        manifest = json.loads((self.dist / "MANIFEST.json").read_text())
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            manifest["original"]["sha256"],
        )

    def test_a_changed_chunk_size_forces_a_repackage(self):
        source = self.source(COMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=2048
        )
        self.assertEqual("packaged", result.outcome)

    def test_a_missing_chunk_makes_the_set_not_current(self):
        """A half-deleted chunk set must be rebuilt, not trusted."""
        source = self.source(COMPRESSIBLE)
        first = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        (self.dist / first.chunks[0].name).unlink()

        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        self.assertEqual("packaged", result.outcome)

    def test_a_corrupt_manifest_makes_the_set_not_current(self):
        source = self.source(COMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        (self.dist / "MANIFEST.json").write_text("{not json", encoding="utf-8")

        result = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=1024
        )
        self.assertEqual("packaged", result.outcome)


class StaleChunkTest(PackagingTestCase):
    def test_shrinking_content_leaves_only_the_current_chunks(self):
        big = INCOMPRESSIBLE  # ~20 KB, barely compressible -> many chunks
        source = self.source(big)
        first = packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=4096
        )
        self.assertGreaterEqual(len(first.chunks), 5)

        # Now a much smaller payload, needing fewer chunks.
        source.write_bytes(INCOMPRESSIBLE[:9000])
        notes = []
        second = packaging.package_file(
            source,
            self.dist,
            dataset="estatisticos",
            chunk_size=4096,
            progress=notes.append,
        )

        self.assertLess(len(second.chunks), len(first.chunks))
        on_disk = sorted(p.name for p in self.dist.glob("*.gz.part*"))
        self.assertEqual(sorted(c.name for c in second.chunks), on_disk)
        manifest = json.loads((self.dist / "MANIFEST.json").read_text())
        self.assertEqual(on_disk, sorted(c["name"] for c in manifest["chunks"]))
        self.assertTrue(any("stale chunk" in n for n in notes), notes)

    def test_a_stale_chunk_cannot_survive_to_corrupt_a_rebuild(self):
        source = self.source(INCOMPRESSIBLE)
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=4096
        )
        source.write_bytes(INCOMPRESSIBLE[:9000])
        packaging.package_file(
            source, self.dist, dataset="estatisticos", chunk_size=4096
        )

        # Concatenating everything present must still be exactly the stream.
        joined = b"".join(
            p.read_bytes() for p in sorted(self.dist.glob("*.gz.part*"))
        )
        self.assertEqual(INCOMPRESSIBLE[:9000], gzip.decompress(joined))


def _checksum_tool():
    import shutil

    if shutil.which("sha256sum"):
        return ["sha256sum"]
    if shutil.which("shasum"):
        return ["shasum", "-a", "256"]
    return None


if __name__ == "__main__":
    unittest.main()
