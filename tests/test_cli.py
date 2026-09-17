import _bootstrap  # noqa: F401

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from support import FAST_POLICY, FakeServer, Route, autoindex, fixture_bytes
from test_listing import PORTAL_ROUTES

from anac_data import cli, manifest
from anac_data.paths import OPTIONAL_KEYS, REQUIRED_KEYS

VRA_BASE = "/Voos e operações aéreas/Voo Regular Ativo (VRA)/"
AERO_DIR = "/Aerodromos/Aeródromos Públicos/Lista de aeródromos públicos/"
STATS_DIR = "/Voos e operações aéreas/Dados Estatísticos do Transporte Aéreo/"
STATS_FILE = STATS_DIR + "Dados_Estatisticos.csv"
AERO_FILE = AERO_DIR + "AerodromosPublicos.csv"


def portal_with_files(**overrides):
    """The captured portal tree plus downloadable file bodies."""
    routes = PORTAL_ROUTES()
    vra_body = fixture_bytes("csv_head_vra.bin")
    aero_body = fixture_bytes("csv_head_aerodromos.bin")

    # The real portal sends both validators on every file.
    modified = "Fri, 28 Aug 2026 07:09:14 GMT"

    for month, name in ((7, "Julho"), (6, "Junho"), (5, "Maio"), (4, "Abril")):
        path = f"{VRA_BASE}2026/{month:02d} - {name}/VRA_2026{month}.csv"
        routes[path] = Route(
            body=vra_body,
            headers={"ETag": f'"vra-{month}"', "Last-Modified": modified},
            support_ranges=True,
        )
    routes[AERO_FILE] = Route(
        body=aero_body,
        headers={"ETag": '"aero-1"', "Last-Modified": modified},
        support_ranges=True,
    )
    routes[STATS_FILE] = Route(
        body=b"Atualizado em: 2026-09-17\r\nANO;MES\r\n2026;7\r\n",
        headers={"ETag": '"stats-1"', "Last-Modified": modified},
        support_ranges=True,
    )
    routes.update(overrides)
    return routes


class FetchTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "raw" / "anac"
        self.addCleanup(self._tmp.cleanup)
        self.stream = io.StringIO()

    def options(self, server, **kwargs):
        kwargs.setdefault("datasets", REQUIRED_KEYS)
        kwargs.setdefault("vra_months", 3)
        return cli.Options(
            out=self.out,
            base_url=server.url("/"),
            policy=FAST_POLICY,
            stream=self.stream,
            **kwargs,
        )

    @property
    def output(self):
        return self.stream.getvalue()


class DefaultRunTest(FetchTestCase):
    def test_downloads_the_required_datasets_into_their_directories(self):
        with FakeServer(portal_with_files()) as server:
            report = cli.run_fetch(self.options(server))

        self.assertEqual(0, report.exit_code)
        self.assertEqual([], report.failed)
        vra = sorted(p.name for p in (self.out / "vra").iterdir())
        self.assertEqual(
            ["VRA_20265.csv", "VRA_20266.csv", "VRA_20267.csv"], vra
        )
        self.assertTrue((self.out / "aerodromos" / "AerodromosPublicos.csv").is_file())
        self.assertEqual(4, len(report.downloaded))

    def test_does_not_touch_the_optional_dataset(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server))
            touched = [
                r["path"] for r in server.requests if "Estat" in r["path"]
            ]

        self.assertEqual([], touched)
        self.assertFalse((self.out / "estatisticos").exists())

    def test_leaves_no_part_files(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server))
        self.assertEqual([], list(self.out.rglob("*.part")))

    def test_summary_lists_the_downloaded_files(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server))

        self.assertIn("-- summary --", self.output)
        self.assertIn("downloaded: 4", self.output)
        self.assertIn("+ VRA_20267.csv", self.output)


class ManifestContentTest(FetchTestCase):
    def test_every_recorded_field_is_populated(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))

        entry = manifest.load(self.out)["files"][
            "aerodromos/AerodromosPublicos.csv"
        ]
        self.assertEqual("aerodromos", entry["dataset"])
        self.assertTrue(entry["source_url"].endswith("AerodromosPublicos.csv"))
        self.assertEqual(4096, entry["size"])
        self.assertTrue(entry["size_verified"])
        self.assertEqual(64, len(entry["sha256"]))
        self.assertEqual('"aero-1"', entry["etag"])
        self.assertIsNotNone(entry["last_modified"])
        # Taken from the file's own first line, not from the HTTP headers.
        self.assertEqual("2026-09-15", entry["atualizado_em"])
        self.assertEqual("latin-1", entry["encoding"])
        self.assertEqual(";", entry["delimiter"])
        self.assertEqual(2, entry["header_line"])
        self.assertIn("downloaded_at", entry)

    def test_the_manifest_is_keyed_by_path_under_the_output_root(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server))

        keys = sorted(manifest.load(self.out)["files"])
        self.assertEqual(
            [
                "aerodromos/AerodromosPublicos.csv",
                "vra/VRA_20265.csv",
                "vra/VRA_20266.csv",
                "vra/VRA_20267.csv",
            ],
            keys,
        )


class IdempotenceTest(FetchTestCase):
    def test_an_unchanged_file_is_skipped_on_a_second_run(self):
        routes = portal_with_files()
        with FakeServer(routes) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))
            first = len(
                [r for r in server.requests if r["path"].endswith(".csv") and r["method"] == "GET"]
            )
            self.stream = io.StringIO()
            report = cli.run_fetch(self.options(server, datasets=("aerodromos",)))
            second = len(
                [r for r in server.requests if r["path"].endswith(".csv") and r["method"] == "GET"]
            )

        self.assertEqual(["AerodromosPublicos.csv"], report.skipped)
        self.assertEqual([], report.downloaded)
        self.assertEqual(0, report.exit_code)
        # No further body transfer.
        self.assertEqual(first, second)
        self.assertIn("up to date", self.output)

    def test_a_republished_file_is_downloaded_again(self):
        routes = portal_with_files()
        with FakeServer(routes) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))
            # ANAC republishes the file: new ETag, new content.
            routes[AERO_FILE] = Route(
                body=b"Atualizado em: 2026-09-20\r\nA;B\r\n1;2\r\n",
                headers={"ETag": '"aero-2"'},
                support_ranges=True,
            )
            report = cli.run_fetch(self.options(server, datasets=("aerodromos",)))

        self.assertEqual(["AerodromosPublicos.csv"], report.downloaded)
        entry = manifest.load(self.out)["files"][
            "aerodromos/AerodromosPublicos.csv"
        ]
        self.assertEqual('"aero-2"', entry["etag"])
        self.assertEqual("2026-09-20", entry["atualizado_em"])

    def test_force_re_downloads_a_matching_file(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))
            report = cli.run_fetch(
                self.options(server, datasets=("aerodromos",), force=True)
            )

        self.assertEqual(["AerodromosPublicos.csv"], report.downloaded)
        self.assertEqual([], report.skipped)

    def test_a_file_on_disk_with_no_manifest_entry_is_re_downloaded(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))
            # The manifest is what records provenance; without it we do not
            # know what the local bytes are.
            manifest.manifest_path(self.out).unlink()
            report = cli.run_fetch(self.options(server, datasets=("aerodromos",)))

        self.assertEqual(["AerodromosPublicos.csv"], report.downloaded)


class PartialFailureTest(FetchTestCase):
    def test_a_success_is_recorded_and_a_failure_is_not(self):
        # VRA's folder is gone from the portal; aerodromos still works.
        routes = portal_with_files()
        del routes["/Voos e operações aéreas/"]
        with FakeServer(routes) as server:
            report = cli.run_fetch(self.options(server))

        self.assertEqual(["AerodromosPublicos.csv"], report.downloaded)
        self.assertEqual(1, len(report.failed))
        self.assertTrue(report.required_failed)
        self.assertEqual(1, report.exit_code)

        files = manifest.load(self.out)["files"]
        self.assertIn("aerodromos/AerodromosPublicos.csv", files)
        # No placeholder entry for what failed.
        self.assertEqual(
            [], [k for k in files if k.startswith("vra/")]
        )

    def test_an_earlier_entry_survives_a_later_failed_run(self):
        routes = portal_with_files()
        with FakeServer(routes) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))
            before = manifest.load(self.out)["files"][
                "aerodromos/AerodromosPublicos.csv"
            ]
            # Now only VRA is requested, and its path has vanished.
            del routes["/Voos e operações aéreas/"]
            report = cli.run_fetch(self.options(server, datasets=("vra",)))

        after = manifest.load(self.out)["files"]
        self.assertEqual(1, report.exit_code)
        self.assertEqual(before, after["aerodromos/AerodromosPublicos.csv"])

    def test_a_renamed_folder_is_reported_with_what_was_there(self):
        """ANAC reorganizes: the folder we want is gone from its parent."""
        routes = portal_with_files()
        routes["/Aerodromos/Aeródromos Públicos/"] = Route(
            body=autoindex(["Listagem de aerodromos/", "Dados Pátio/"])
        )
        with FakeServer(routes) as server:
            report = cli.run_fetch(self.options(server, datasets=("aerodromos",)))

        self.assertEqual(1, report.exit_code)
        output = self.output
        self.assertIn("Lista de aeródromos públicos", output)
        self.assertIn("the portal listed", output)
        # The operator can see what it was renamed to.
        self.assertIn("Listagem de aerodromos/", output)
        # Nothing partial was written for the failed dataset.
        self.assertFalse((self.out / "aerodromos").exists())

    def test_a_404_on_the_folder_is_reported_clearly(self):
        routes = portal_with_files()
        del routes[AERO_DIR]
        with FakeServer(routes) as server:
            report = cli.run_fetch(self.options(server, datasets=("aerodromos",)))

        self.assertEqual(1, report.exit_code)
        self.assertIn("Lista de aeródromos públicos", self.output)
        self.assertIn("404", self.output)
        self.assertFalse((self.out / "aerodromos").exists())


class DryRunTest(FetchTestCase):
    def test_reports_urls_and_sizes_without_transferring_bodies(self):
        with FakeServer(portal_with_files()) as server:
            report = cli.run_fetch(self.options(server, dry_run=True))
            body_gets = [
                r
                for r in server.requests
                if r["method"] == "GET" and r["path"].endswith(".csv")
            ]

        self.assertEqual(4, len(report.planned))
        self.assertEqual([], body_gets)
        self.assertIn("would fetch VRA_20267.csv", self.output)
        self.assertIn("4.0 KiB", self.output)
        self.assertIn("VRA_20267.csv", self.output)
        self.assertIn("nothing was transferred", self.output)

    def test_writes_nothing_to_disk(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server, dry_run=True))

        self.assertFalse(self.out.exists())

    def test_sizes_come_from_the_portal(self):
        with FakeServer(portal_with_files()) as server:
            report = cli.run_fetch(
                self.options(server, dry_run=True, datasets=("aerodromos",))
            )
        self.assertEqual([("AerodromosPublicos.csv", 4096)], report.planned)


class OptionalDatasetTest(FetchTestCase):
    def test_opting_in_downloads_it_and_announces_its_size_first(self):
        with FakeServer(portal_with_files()) as server:
            report = cli.run_fetch(
                self.options(server, datasets=REQUIRED_KEYS + OPTIONAL_KEYS)
            )

        self.assertIn("Dados_Estatisticos.csv", report.downloaded)
        self.assertTrue(
            (self.out / "estatisticos" / "Dados_Estatisticos.csv").is_file()
        )
        output = self.output
        size_notice = output.index("is optional and weighs")
        saved = output.index("saved Dados_Estatisticos.csv")
        self.assertLess(size_notice, saved, "size must be announced first")

    def test_naming_it_explicitly_fetches_only_it(self):
        with FakeServer(portal_with_files()) as server:
            report = cli.run_fetch(
                self.options(server, datasets=("estatisticos",))
            )

        self.assertEqual(["Dados_Estatisticos.csv"], report.downloaded)
        self.assertFalse((self.out / "vra").exists())

    def test_it_takes_the_full_file_not_the_stale_partial_extract(self):
        with FakeServer(portal_with_files()) as server:
            report = cli.run_fetch(
                self.options(server, datasets=("estatisticos",), dry_run=True)
            )
        self.assertEqual(["Dados_Estatisticos.csv"], [n for n, _ in report.planned])


class VerboseTest(FetchTestCase):
    def test_reports_a_month_folder_that_exists_but_is_empty(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(
                self.options(server, datasets=("vra",), verbose=True, dry_run=True)
            )

        self.assertIn("exists but holds no matching file", self.output)
        self.assertIn("12 - Dezembro", self.output)

    def test_quiet_by_default(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(
                self.options(server, datasets=("vra",), dry_run=True)
            )
        self.assertNotIn("exists but holds no matching file", self.output)


class ResolveDatasetsTest(unittest.TestCase):
    def test_default_is_the_required_datasets_only(self):
        self.assertEqual(
            REQUIRED_KEYS, cli.resolve_datasets(None, include_optional=False)
        )

    def test_the_flag_adds_the_optional_ones(self):
        self.assertEqual(
            REQUIRED_KEYS + OPTIONAL_KEYS,
            cli.resolve_datasets(None, include_optional=True),
        )

    def test_an_explicit_selection_wins(self):
        self.assertEqual(
            ("vra",), cli.resolve_datasets(("vra",), include_optional=True)
        )

    def test_an_unknown_dataset_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            cli.resolve_datasets(("vra", "nope"), include_optional=False)
        self.assertIn("nope", str(caught.exception))


class HumanSizeTest(unittest.TestCase):
    def test_formats_the_sizes_this_tool_deals_in(self):
        self.assertEqual("512 B", cli.human_size(512))
        self.assertEqual("4.0 KiB", cli.human_size(4096))
        self.assertEqual("12.1 MiB", cli.human_size(12_691_900))
        self.assertEqual("342.3 MiB", cli.human_size(358_893_852))
        self.assertEqual("size unknown", cli.human_size(None))


class MainTest(FetchTestCase):
    """Drives the real argv path. main() prints to stdout, so it is captured."""

    def run_main(self, routes, *extra):
        with FakeServer(routes) as server:
            argv = [
                "fetch",
                "--out",
                str(self.out),
                "--base-url",
                server.url("/"),
                "--attempts",
                "1",
                *extra,
            ]
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = cli.main(argv)
        return code, captured.getvalue()

    def test_exit_code_zero_when_everything_is_obtained(self):
        code, output = self.run_main(
            portal_with_files(), "--datasets", "aerodromos"
        )
        self.assertEqual(0, code)
        self.assertIn("saved AerodromosPublicos.csv", output)

    def test_exit_code_non_zero_when_a_required_dataset_fails(self):
        routes = portal_with_files()
        del routes[AERO_DIR]
        code, output = self.run_main(routes, "--datasets", "aerodromos")
        self.assertEqual(1, code)
        self.assertIn("a required dataset could not be obtained", output)

    def test_include_optional_flag_widens_the_default_set(self):
        code, output = self.run_main(portal_with_files(), "--include-optional")
        self.assertEqual(0, code)
        self.assertIn("Dados_Estatisticos.csv", output)

    def test_dry_run_flag_transfers_nothing(self):
        code, output = self.run_main(portal_with_files(), "--dry-run")
        self.assertEqual(0, code)
        self.assertIn("nothing was transferred", output)
        self.assertFalse(self.out.exists())

    def test_vra_months_flag_is_honoured(self):
        code, output = self.run_main(
            portal_with_files(), "--datasets", "vra", "--vra-months", "1", "--dry-run"
        )
        self.assertEqual(0, code)
        self.assertEqual(1, output.count("would fetch"))

    def test_vra_months_must_be_positive(self):
        with self.assertRaises(SystemExit):
            cli.main(["fetch", "--vra-months", "0", "--out", str(self.out)])


class InspectTest(FetchTestCase):
    def test_describes_the_downloaded_files(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server))

        out = io.StringIO()
        code = cli.run_inspect([], out=self.out, stream=out)
        text = out.getvalue()

        self.assertEqual(0, code)
        self.assertIn("AerodromosPublicos.csv", text)
        self.assertIn("VRA_20267.csv", text)
        self.assertIn("Latin-1", text)
        self.assertIn("UTF-8 with BOM", text)
        self.assertIn("line 2", text)

    def test_accepts_explicit_paths(self):
        with FakeServer(portal_with_files()) as server:
            cli.run_fetch(self.options(server, datasets=("aerodromos",)))

        target = self.out / "aerodromos" / "AerodromosPublicos.csv"
        out = io.StringIO()
        code = cli.run_inspect([target], out=self.out, stream=out)

        self.assertEqual(0, code)
        self.assertIn("Latin-1", out.getvalue())

    def test_reports_when_there_is_nothing_to_inspect(self):
        out = io.StringIO()
        code = cli.run_inspect([], out=self.out, stream=out)
        self.assertEqual(1, code)
        self.assertIn("no CSV files found", out.getvalue())

    def test_reports_a_path_that_does_not_exist(self):
        out = io.StringIO()
        code = cli.run_inspect([self.out / "nope.csv"], out=self.out, stream=out)
        self.assertEqual(1, code)
        self.assertIn("not found", out.getvalue())


if __name__ == "__main__":
    unittest.main()
