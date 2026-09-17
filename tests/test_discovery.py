import _bootstrap  # noqa: F401

import unittest

from support import FAST_POLICY, FakeServer, Route, autoindex, fixture_bytes
from test_listing import PORTAL_ROUTES

from anac_data import discovery
from anac_data.paths import DATASETS_BY_KEY

VRA = DATASETS_BY_KEY["vra"]
AERODROMOS = DATASETS_BY_KEY["aerodromos"]
ESTATISTICOS = DATASETS_BY_KEY["estatisticos"]


class MonthNumberTest(unittest.TestCase):
    def test_reads_the_leading_digits(self):
        self.assertEqual(7, discovery._month_number("07 - Julho"))
        # The accent in "Março" is exactly why the name is not parsed.
        self.assertEqual(3, discovery._month_number("03 - Março"))
        self.assertEqual(12, discovery._month_number("12 - Dezembro"))

    def test_rejects_non_months(self):
        self.assertIsNone(discovery._month_number("tmp.txt"))
        self.assertIsNone(discovery._month_number("13 - Nope"))
        self.assertIsNone(discovery._month_number("Janeiro"))


class FilenameFilterTest(unittest.TestCase):
    def test_vra_month_folder_yields_only_the_csv(self):
        """The .json twin and a stray tmp.txt must not be downloaded."""
        entries = [
            e
            for e in _entries(
                autoindex(["VRA_20267.csv", "VRA_20267.json", "tmp.txt"])
            )
        ]
        kept = [e.name for e in discovery.files_in(entries, VRA)]
        self.assertEqual(["VRA_20267.csv"], kept)

    def test_aerodrome_folder_excludes_the_json_and_xls_copies(self):
        entries = _entries(fixture_bytes("listing_lista_aerodromos.html"))
        kept = [e.name for e in discovery.files_in(entries, AERODROMOS)]
        self.assertEqual(["AerodromosPublicos.csv"], kept)

    def test_statistical_folder_takes_the_full_file_not_the_partial(self):
        entries = _entries(fixture_bytes("listing_estatisticos.html"))
        kept = [e.name for e in discovery.files_in(entries, ESTATISTICOS)]
        self.assertEqual(["Dados_Estatisticos.csv"], kept)


def _entries(body: bytes):
    from anac_data import listing

    return listing.parse_listing(body.decode("utf-8"))


def _vra_tree(populated, years, *, base="/vra/"):
    """A VRA folder tree.

    ``populated`` maps ``(year, month)`` to the filenames in that month folder;
    ``years`` lists every year folder, and every month 1-12 exists under each
    year whether or not it holds anything - which is how the real portal looks.
    """
    routes = {base: Route(body=autoindex([f"{y}/" for y in years]))}
    for year in years:
        routes[f"{base}{year}/"] = Route(
            body=autoindex([f"{m:02d} - M{m}/" for m in range(1, 13)])
        )
        for month in range(1, 13):
            routes[f"{base}{year}/{month:02d} - M{month}/"] = Route(
                body=autoindex(populated.get((year, month), []))
            )
    return routes


class VraMonthSelectionTest(unittest.TestCase):
    def test_empty_future_month_folders_are_skipped(self):
        """The real 2026 tree: July holds data, 08-12 are placeholders."""
        with FakeServer(PORTAL_ROUTES()) as server:
            found = discovery.discover(
                VRA, vra_months=3, policy=FAST_POLICY, base_url=server.url("/")
            )

        self.assertEqual(
            [(2026, 7), (2026, 6), (2026, 5)],
            [(f.year, f.month) for f in found],
        )
        self.assertEqual("VRA_20267.csv", found[0].name)

    def test_reports_a_folder_that_exists_but_is_empty(self):
        notes = []
        with FakeServer(PORTAL_ROUTES()) as server:
            discovery.discover(
                VRA,
                vra_months=1,
                policy=FAST_POLICY,
                base_url=server.url("/"),
                report=notes.append,
            )

        self.assertTrue(
            any("exists but holds no matching file" in n for n in notes),
            notes,
        )
        self.assertTrue(any("2026-12" in n for n in notes), notes)

    def test_crosses_into_the_previous_year_when_the_latest_is_short(self):
        populated = {
            (2026, 1): ["VRA_20261.csv"],
            (2026, 2): ["VRA_20262.csv"],
            (2025, 12): ["VRA_202512.csv"],
            (2025, 11): ["VRA_202511.csv"],
        }
        routes = _vra_tree(populated, [2025, 2026])
        with FakeServer(routes) as server:
            found = discovery.select_monthly(
                VRA, server.url("/vra/"), 3, policy=FAST_POLICY
            )

        self.assertEqual(
            [(2026, 2), (2026, 1), (2025, 12)],
            [(f.year, f.month) for f in found],
        )

    def test_requested_count_of_two_selects_exactly_two(self):
        with FakeServer(PORTAL_ROUTES()) as server:
            found = discovery.discover(
                VRA, vra_months=2, policy=FAST_POLICY, base_url=server.url("/")
            )

        self.assertEqual(2, len(found))
        self.assertEqual(
            [(2026, 7), (2026, 6)], [(f.year, f.month) for f in found]
        )

    def test_selection_ignores_a_renamed_file(self):
        """A file whose name breaks every past convention is still taken."""
        populated = {(2026, 4): ["vra-dados-abril-2026.CSV"]}
        routes = _vra_tree(populated, [2026])
        with FakeServer(routes) as server:
            found = discovery.select_monthly(
                VRA, server.url("/vra/"), 1, policy=FAST_POLICY
            )

        self.assertEqual(1, len(found))
        self.assertEqual("vra-dados-abril-2026.CSV", found[0].name)
        self.assertTrue(found[0].url.endswith("vra-dados-abril-2026.CSV"))


class NonMonthlyDiscoveryTest(unittest.TestCase):
    def test_aerodrome_discovery_resolves_one_file(self):
        with FakeServer(PORTAL_ROUTES()) as server:
            found = discovery.discover(
                AERODROMOS,
                vra_months=3,
                policy=FAST_POLICY,
                base_url=server.url("/"),
            )

        self.assertEqual(1, len(found))
        self.assertEqual("AerodromosPublicos.csv", found[0].name)
        self.assertTrue(found[0].required)
        self.assertIsNone(found[0].year)

    def test_statistical_discovery_is_marked_optional(self):
        with FakeServer(PORTAL_ROUTES()) as server:
            found = discovery.discover(
                ESTATISTICOS,
                vra_months=3,
                policy=FAST_POLICY,
                base_url=server.url("/"),
            )

        self.assertEqual(["Dados_Estatisticos.csv"], [f.name for f in found])
        self.assertFalse(found[0].required)


if __name__ == "__main__":
    unittest.main()
