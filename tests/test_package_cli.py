import _bootstrap  # noqa: F401

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import FAST_POLICY, FakeServer, Route, fixture_bytes
from test_cli import AERO_FILE, VRA_BASE, portal_with_files

from anac_data import cli, manifest
from anac_data.paths import CHUNK_LIMIT_BYTES

BIG = (b"EMPRESA;ANO;MES;PASSAGEIROS\r\n" + b"GLO;2026;7;180\r\n" * 5000)
# Random bytes barely compress, so chunk counts are predictable when a test
# needs more than one chunk.
BIG_INCOMPRESSIBLE = b"A;B\r\n" + os.urandom(30_000)
SMALL = b"Atualizado em: 2026-09-15\r\nA;B\r\n1;2\r\n"


class PackageTestCase(unittest.TestCase):
    """Builds a raw tree plus a download manifest, the way `fetch` leaves it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.out = self.tmp / "raw" / "anac"
        self.dist = self.tmp / "dist"
        self.stream = io.StringIO()

    def stage(self, dataset, name, data, *, record=True):
        path = self.out / dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if record:
            catalogue = manifest.load(self.out)
            catalogue.setdefault("files", {})[f"{dataset}/{name}"] = {
                "dataset": dataset,
                "source_url": f"https://example.test/{name}",
                "size": len(data),
                "size_verified": True,
                "sha256": hashlib.sha256(data).hexdigest(),
                "etag": '"x"',
                "last_modified": "Tue, 15 Sep 2026 06:01:28 GMT",
                "atualizado_em": "2026-09-15",
                "encoding": "utf-8",
                "delimiter": ";",
                "header_line": 2,
                "downloaded_at": "2026-09-17T15:00:00+00:00",
            }
            manifest.save(self.out, catalogue)
        return path

    def options(self, **kwargs):
        kwargs.setdefault("datasets", ("estatisticos",))
        kwargs.setdefault("chunk_size", 4096)
        return cli.PackageOptions(
            out=self.out, dist=self.dist, stream=self.stream, **kwargs
        )

    @property
    def output(self):
        return self.stream.getvalue()


class PackageRunTest(PackageTestCase):
    def test_packages_an_oversized_dataset(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        report = cli.run_package(self.options())

        self.assertEqual(0, report.exit_code)
        self.assertEqual(
            ["estatisticos/Dados_Estatisticos.csv"], report.packaged
        )
        dist = self.dist / "estatisticos" / "Dados_Estatisticos.csv"
        self.assertTrue((dist / "MANIFEST.json").is_file())
        self.assertTrue((dist / "checksums.sha256").is_file())
        self.assertGreater(len(list(dist.glob("*.gz.part*"))), 0)
        self.assertGreater(report.chunk_count, 0)

    def test_leaves_an_under_limit_dataset_alone(self):
        self.stage("aerodromos", "AerodromosPublicos.csv", SMALL)
        report = cli.run_package(
            self.options(datasets=("aerodromos",))
        )

        self.assertEqual([], report.packaged)
        self.assertEqual(
            ["aerodromos/AerodromosPublicos.csv"], report.not_needed
        )
        self.assertFalse((self.dist / "aerodromos").exists())
        self.assertIn("under the limit", self.output)

    def test_mixed_run_reports_each_correctly_and_exits_zero(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        self.stage("aerodromos", "AerodromosPublicos.csv", SMALL)
        report = cli.run_package(
            self.options(datasets=("aerodromos", "estatisticos"))
        )

        self.assertEqual(0, report.exit_code)
        self.assertEqual(1, len(report.packaged))
        self.assertEqual(1, len(report.not_needed))
        output = self.output
        self.assertIn("packaged: 1", output)
        self.assertIn("no packaging needed: 1", output)

    def test_packages_every_vra_month_without_clobbering_the_others(self):
        """Identical content per month must not collapse into one chunk set."""
        for month in (5, 6, 7):
            self.stage("vra", f"VRA_2026{month}.csv", BIG)
        report = cli.run_package(self.options(datasets=("vra",)))

        self.assertEqual(3, len(report.packaged))
        for month in (5, 6, 7):
            own = self.dist / "vra" / f"VRA_2026{month}.csv"
            self.assertTrue((own / "MANIFEST.json").is_file(), own)
            parts = list(own.glob("*.gz.part*"))
            self.assertGreater(len(parts), 0, own)
            manifest_data = json.loads((own / "MANIFEST.json").read_text())
            self.assertEqual(
                f"VRA_2026{month}.csv", manifest_data["original"]["name"]
            )

    def test_chunk_size_flag_governs_chunk_size(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG_INCOMPRESSIBLE)
        cli.run_package(self.options(chunk_size=2048))

        sizes = [
            p.stat().st_size
            for p in sorted(
                (self.dist / "estatisticos" / "Dados_Estatisticos.csv").glob(
                    "*.gz.part*"
                )
            )
        ]
        self.assertGreater(len(sizes), 1)
        for size in sizes[:-1]:
            self.assertEqual(2048, size)


class MissingSourceTest(PackageTestCase):
    def test_a_missing_file_fails_with_no_partial_chunk_set(self):
        # Recorded in the manifest but deleted from disk.
        path = self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        path.unlink()

        report = cli.run_package(self.options())

        self.assertEqual(1, report.exit_code)
        self.assertEqual(1, len(report.failed))
        self.assertIn("estatisticos", report.failed[0][0])
        self.assertEqual([], list(self.dist.rglob("*.gz.part*")))
        output = self.output
        self.assertIn("FAILED", output)
        self.assertIn("failed: 1", output)

    def test_nothing_downloaded_at_all_is_reported(self):
        report = cli.run_package(self.options())
        self.assertEqual(1, report.exit_code)
        self.assertIn("fetch", self.output)


class PackageIdempotenceTest(PackageTestCase):
    def test_second_run_reports_unchanged_and_rewrites_nothing(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        cli.run_package(self.options())
        dist = self.dist / "estatisticos" / "Dados_Estatisticos.csv"
        before = {p.name: p.stat().st_mtime_ns for p in sorted(dist.iterdir())}
        time.sleep(1.1)

        self.stream = io.StringIO()
        report = cli.run_package(self.options())

        self.assertEqual(
            ["estatisticos/Dados_Estatisticos.csv"], report.unchanged
        )
        self.assertEqual([], report.packaged)
        self.assertEqual(0, report.exit_code)
        after = {p.name: p.stat().st_mtime_ns for p in sorted(dist.iterdir())}
        self.assertEqual(before, after, "files were rewritten")
        self.assertIn("unchanged", self.output)

    def test_force_repackages(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        cli.run_package(self.options())
        report = cli.run_package(self.options(force=True))
        self.assertEqual(1, len(report.packaged))

    def test_changed_source_is_repackaged(self):
        path = self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        cli.run_package(self.options())
        path.write_bytes(BIG + b"AZU;2026;8;200\r\n")
        # Re-stage so the manifest digest matches the new content.
        self.stage("estatisticos", "Dados_Estatisticos.csv", path.read_bytes())

        report = cli.run_package(self.options())
        self.assertEqual(1, len(report.packaged))


class PackageDryRunTest(PackageTestCase):
    def test_reports_without_writing(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        report = cli.run_package(self.options(dry_run=True))

        self.assertEqual(1, len(report.planned))
        self.assertFalse(self.dist.exists())
        output = self.output
        self.assertIn("would package", output)
        self.assertIn("nothing was written", output)

    def test_dry_run_still_reports_an_under_limit_file(self):
        self.stage("aerodromos", "AerodromosPublicos.csv", SMALL)
        report = cli.run_package(
            self.options(datasets=("aerodromos",), dry_run=True)
        )
        self.assertEqual([], report.planned)
        self.assertEqual(1, len(report.not_needed))

    def test_dry_run_reports_a_missing_file_as_a_failure(self):
        path = self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        path.unlink()
        report = cli.run_package(self.options(dry_run=True))
        self.assertEqual(1, report.exit_code)


class GitIgnoreWarningTest(PackageTestCase):
    """The warning that stops a 342 MiB file reaching `git add -A`."""

    def _git_repo(self, gitignore_text):
        repo = self.tmp / "repo"
        (repo / "data" / "raw" / "anac" / "estatisticos").mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q"], cwd=repo, check=True, capture_output=True
        )
        (repo / ".gitignore").write_text(gitignore_text, encoding="utf-8")
        return repo

    def _run_in(self, repo, gitignore_text):
        self.out = repo / "data" / "raw" / "anac"
        self.dist = repo / "data" / "dist"
        self.stream = io.StringIO()
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        cli.run_package(self.options())
        return self.output

    def test_warns_when_the_packaged_original_is_not_ignored(self):
        repo = self._git_repo("# nothing ignored\n")
        output = self._run_in(repo, "")
        self.assertIn("NOT ignored by git", output)
        self.assertIn("Add this line to .gitignore", output)
        self.assertIn("data/raw/anac/estatisticos/Dados_Estatisticos.csv", output)

    def test_silent_once_the_rule_is_present(self):
        repo = self._git_repo(
            "data/raw/anac/estatisticos/Dados_Estatisticos.csv\n"
        )
        output = self._run_in(repo, "")
        self.assertNotIn("NOT ignored by git", output)

    def test_silent_when_a_directory_rule_covers_it(self):
        repo = self._git_repo("data/raw/anac/estatisticos/\n")
        output = self._run_in(repo, "")
        self.assertNotIn("NOT ignored by git", output)


class PackageMainTest(PackageTestCase):
    def run_main(self, *extra):
        argv = [
            "package",
            "--out",
            str(self.out),
            "--dist",
            str(self.dist),
            "--chunk-size",
            "4096",
            *extra,
        ]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = cli.main(argv)
        return code, captured.getvalue()

    def test_exit_zero_on_success(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        code, output = self.run_main("--datasets", "estatisticos")
        self.assertEqual(0, code)
        self.assertIn("packaged:", output)

    def test_exit_non_zero_when_a_dataset_is_absent(self):
        code, output = self.run_main("--datasets", "estatisticos")
        self.assertEqual(1, code)
        self.assertIn("failed: 1", output)

    def test_default_datasets_cover_the_optional_one(self):
        """`package` has no opt-in gate - the download already happened."""
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        self.stage("aerodromos", "AerodromosPublicos.csv", SMALL)
        for month in (5, 6, 7):
            self.stage("vra", f"VRA_2026{month}.csv", SMALL)

        code, output = self.run_main()
        self.assertEqual(0, code)
        self.assertIn("Dados_Estatisticos.csv", output)

    def test_dry_run_flag_writes_nothing(self):
        self.stage("estatisticos", "Dados_Estatisticos.csv", BIG)
        code, output = self.run_main("--datasets", "estatisticos", "--dry-run")
        self.assertEqual(0, code)
        self.assertFalse(self.dist.exists())

    def test_a_silly_chunk_size_is_rejected(self):
        with self.assertRaises(SystemExit):
            cli.main(["package", "--chunk-size", "10", "--out", str(self.out)])

    def test_an_unknown_dataset_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main("--datasets", "nope")
        self.assertIn("nope", str(caught.exception))


class FetchHintTest(unittest.TestCase):
    """`fetch` should say when it has stored something too big to commit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "raw" / "anac"
        self.addCleanup(self._tmp.cleanup)
        self.stream = io.StringIO()

    def _fetch(self, routes, datasets):
        with FakeServer(routes) as server:
            report = cli.run_fetch(
                cli.Options(
                    datasets=datasets,
                    out=self.out,
                    base_url=server.url("/"),
                    policy=FAST_POLICY,
                    stream=self.stream,
                    vra_months=1,
                )
            )
        return report, self.stream.getvalue()

    def test_no_hint_for_files_under_the_limit(self):
        report, output = self._fetch(portal_with_files(), ("aerodromos",))
        self.assertEqual([], report.oversized)
        self.assertNotIn("cannot be committed", output)
        self.assertNotIn("anac_data package", output)

    def test_hint_appears_for_an_oversized_download(self):
        routes = portal_with_files()
        # An aerodrome file that is implausibly large, to cross the threshold
        # without moving 45 MiB through the test server.
        big = b"A;B\r\n" + b"x" * (CHUNK_LIMIT_BYTES + 1)
        routes[AERO_FILE] = Route(
            body=big, headers={"ETag": '"big"'}, support_ranges=True
        )
        report, output = self._fetch(routes, ("aerodromos",))

        self.assertEqual(["AerodromosPublicos.csv"], report.oversized)
        self.assertIn("cannot be committed", output)
        self.assertIn("anac_data package", output)


if __name__ == "__main__":
    unittest.main()
