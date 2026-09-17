"""Exercise scripts/rebuild.sh as a real subprocess.

The script is a deliverable in its own right - someone with a clone and no
Python runs it - so it is tested by running it, not by reimplementing it.
"""

import _bootstrap  # noqa: F401

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anac_data import packaging

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "rebuild.sh"
SH = shutil.which("sh") or "/bin/sh"

#: What the script actually shells out to. `gunzip` is itself a wrapper that
#: execs `gzip`, so both have to be reachable.
BASE_TOOLS = (
    "sh", "gzip", "gunzip", "cat", "sed", "cut", "ls", "sort", "mkdir",
    "mv", "rm", "find", "wc", "tr", "head", "basename", "dirname",
)

PAYLOAD = b"ANO;MES;PAX\r\n" + os.urandom(30_000)


def _has(tool):
    return shutil.which(tool) is not None


@unittest.skipUnless(SCRIPT.is_file(), "scripts/rebuild.sh not present")
@unittest.skipUnless(
    _has("sha256sum") or _has("shasum"), "no sha256 tool available"
)
@unittest.skipUnless(_has("gunzip"), "gunzip not available")
class RebuildShellTestCase(unittest.TestCase):
    """Each test gets a throwaway tree shaped like the repository."""

    chunk_size = 4096

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.raw_dir = self.root / "data" / "raw" / "anac" / "estatisticos"
        self.raw_dir.mkdir(parents=True)
        self.source = self.raw_dir / "Dados_Estatisticos.csv"
        self.source.write_bytes(PAYLOAD)
        self.expected_sha = hashlib.sha256(PAYLOAD).hexdigest()

        self.chunk_dir = packaging.chunk_dir_for(
            self.root / "data" / "dist", "estatisticos", self.source.name
        )
        self.result = packaging.package_file(
            self.source,
            self.chunk_dir,
            dataset="estatisticos",
            chunk_size=self.chunk_size,
        )
        self.assertGreater(len(self.result.chunks), 1, "need a multi-chunk set")

    def run_script(self, *args, cwd=None, env=None):
        environment = dict(os.environ)
        if env:
            environment.update(env)
        return subprocess.run(
            [SH, str(SCRIPT), *args],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            env=environment,
        )

    def _path_with(self, *extra, name="fakebin", include_base=True):
        """A PATH directory holding only the base tools plus ``extra``."""
        bin_dir = self.root / name
        bin_dir.mkdir(exist_ok=True)
        wanted = (BASE_TOOLS if include_base else ()) + extra
        for tool in wanted:
            real = shutil.which(tool)
            if real is None:
                if tool in extra:
                    self.skipTest(f"{tool} not available to link")
                continue
            link = bin_dir / tool
            if not link.exists():
                link.symlink_to(real)
        return str(bin_dir)

    def digest(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SyntaxTest(unittest.TestCase):
    def test_parses_as_posix_sh(self):
        done = subprocess.run(
            [SH, "-n", str(SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(0, done.returncode, done.stderr)

    def test_has_no_bashisms_that_dash_rejects(self):
        if not _has("dash"):
            self.skipTest("dash not installed")
        done = subprocess.run(
            ["dash", "-n", str(SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(0, done.returncode, done.stderr)

    def test_shellcheck_is_clean_when_available(self):
        if not _has("shellcheck"):
            self.skipTest("shellcheck not installed")
        done = subprocess.run(
            ["shellcheck", "-s", "sh", str(SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_help_exits_zero(self):
        done = subprocess.run(
            [SH, str(SCRIPT), "--help"], capture_output=True, text=True
        )
        self.assertEqual(0, done.returncode)
        self.assertIn("Usage", done.stdout)


class HappyPathTest(RebuildShellTestCase):
    def test_rebuilds_a_deleted_original_byte_for_byte(self):
        self.source.unlink()
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(self.source.is_file())
        self.assertEqual(PAYLOAD, self.source.read_bytes())
        self.assertEqual(self.expected_sha, self.digest(self.source))
        self.assertIn("rebuilt and verified", done.stdout)

    def test_rebuilds_a_named_chunk_directory(self):
        self.source.unlink()
        done = self.run_script(str(self.chunk_dir))

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))

    def test_honours_an_explicit_output_directory(self):
        elsewhere = self.root / "somewhere" / "else"
        done = self.run_script(str(self.chunk_dir), "-o", str(elsewhere))

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        rebuilt = elsewhere / self.source.name
        self.assertEqual(self.expected_sha, self.digest(rebuilt))

    def test_leaves_no_temporary_file(self):
        self.source.unlink()
        self.run_script()
        self.assertEqual([], list(self.raw_dir.glob("*.rebuilding")))

    def test_an_already_correct_file_is_not_rewritten(self):
        before = self.source.stat().st_mtime_ns
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("already correct", done.stdout)
        self.assertEqual(before, self.source.stat().st_mtime_ns)

    def test_a_wrong_existing_file_is_replaced(self):
        self.source.write_bytes(b"stale contents")
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))
        self.assertIn("does not match", done.stdout)


class ToolDetectionTest(RebuildShellTestCase):
    """Linux has sha256sum, macOS has shasum. Both paths must work."""

    def test_works_with_only_sha256sum(self):
        if not _has("sha256sum"):
            self.skipTest("sha256sum not available")
        path = self._path_with("sha256sum", name="only-sha256sum")
        self.source.unlink()
        done = self.run_script(env={"PATH": path})

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))

    def test_works_with_only_shasum(self):
        if not _has("shasum"):
            self.skipTest("shasum not available")
        path = self._path_with("shasum", "perl", name="only-shasum")
        self.source.unlink()
        done = self.run_script(env={"PATH": path})

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))

    def test_fails_clearly_with_neither(self):
        path = self._path_with(name="no-sha-tool")
        done = self.run_script(env={"PATH": path})

        self.assertNotEqual(0, done.returncode)
        self.assertIn("sha256sum or shasum", done.stderr)


class FailureTest(RebuildShellTestCase):
    def test_a_corrupt_chunk_is_named_and_nothing_is_written(self):
        self.source.unlink()
        victim = self.chunk_dir / self.result.chunks[0].name
        victim.write_bytes(b"\x00" * victim.stat().st_size)

        done = self.run_script()

        self.assertNotEqual(0, done.returncode)
        combined = done.stdout + done.stderr
        self.assertIn("failed verification", combined)
        self.assertFalse(self.source.exists(), "a bad rebuild left a file")
        self.assertEqual([], list(self.raw_dir.glob("*.rebuilding")))

    def test_a_missing_chunk_is_named(self):
        self.source.unlink()
        gone = self.result.chunks[1].name
        (self.chunk_dir / gone).unlink()

        done = self.run_script()

        self.assertNotEqual(0, done.returncode)
        combined = done.stdout + done.stderr
        self.assertIn("missing", combined)
        self.assertIn(gone, combined)
        self.assertFalse(self.source.exists())

    def test_a_final_digest_mismatch_is_distinct_from_a_chunk_failure(self):
        """Tamper with the recorded original digest, leaving chunks valid."""
        self.source.unlink()
        listing = self.chunk_dir / "checksums.sha256"
        text = listing.read_text(encoding="utf-8").replace(
            f"# original-sha256: {self.expected_sha}",
            "# original-sha256: " + "0" * 64,
        )
        listing.write_text(text, encoding="utf-8")

        done = self.run_script()

        self.assertNotEqual(0, done.returncode)
        combined = done.stdout + done.stderr
        self.assertIn("does not match the recorded digest", combined)
        # Distinguishable from the corrupt-chunk message.
        self.assertNotIn("failed verification", combined)
        self.assertFalse(self.source.exists())

    def test_a_directory_without_a_listing_is_rejected(self):
        empty = self.root / "not-a-chunk-dir"
        empty.mkdir()
        done = self.run_script(str(empty))

        self.assertNotEqual(0, done.returncode)
        self.assertIn("checksums.sha256", done.stderr)

    def test_a_missing_directory_is_rejected(self):
        done = self.run_script(str(self.root / "nope"))
        self.assertNotEqual(0, done.returncode)
        self.assertIn("not a directory", done.stderr)

    def test_no_chunk_sets_at_all_is_reported(self):
        shutil.rmtree(self.root / "data" / "dist")
        done = self.run_script()
        self.assertNotEqual(0, done.returncode)
        self.assertIn("data/dist", done.stderr)

    def test_an_unknown_option_is_rejected(self):
        done = self.run_script("--wat")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("unknown option", done.stderr)


class StandaloneTest(RebuildShellTestCase):
    def test_works_copied_outside_any_repository(self):
        """The whole point: a chunk directory plus this script is enough."""
        away = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, away, True)
        shutil.copy2(SCRIPT, away / "rebuild.sh")
        shutil.copytree(self.chunk_dir, away / "chunks")

        done = subprocess.run(
            [SH, "./rebuild.sh", "chunks", "-o", "out"],
            cwd=away,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        rebuilt = away / "out" / self.source.name
        self.assertEqual(self.expected_sha, self.digest(rebuilt))

    def test_needs_no_python(self):
        """Run with a PATH that has no python at all."""
        bin_dir = self._path_with("sha256sum", name="nopython")
        for name in ("python", "python3"):
            self.assertIsNone(
                shutil.which(name, path=bin_dir),
                f"test PATH unexpectedly has {name}",
            )

        self.source.unlink()
        done = self.run_script(env={"PATH": bin_dir})

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))


class MultipleChunkSetsTest(RebuildShellTestCase):
    def test_rebuilds_every_chunk_set_under_dist(self):
        # A second oversized file in another dataset.
        other_dir = self.root / "data" / "raw" / "anac" / "vra"
        other_dir.mkdir(parents=True)
        other = other_dir / "VRA_20267.csv"
        other_payload = b"ICAO;VOO\r\n" + os.urandom(20_000)
        other.write_bytes(other_payload)
        packaging.package_file(
            other,
            packaging.chunk_dir_for(
                self.root / "data" / "dist", "vra", other.name
            ),
            dataset="vra",
            chunk_size=self.chunk_size,
        )

        self.source.unlink()
        other.unlink()
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(PAYLOAD, self.source.read_bytes())
        self.assertEqual(other_payload, other.read_bytes())
        self.assertIn("rebuilt 2 file(s)", done.stdout)


if __name__ == "__main__":
    unittest.main()
