import _bootstrap  # noqa: F401

import unittest

from support import FAST_POLICY, FakeServer, Route, fixture_bytes, fixture_text

from anac_data import listing
from anac_data.paths import DATASETS_BY_KEY


class ParseListingTest(unittest.TestCase):
    """Parsed against pages captured from the live portal on 2026-09-17."""

    def test_parses_the_real_portal_root(self):
        entries = listing.parse_listing(fixture_text("listing_root.html"))
        names = [e.name for e in entries]

        self.assertIn("Aerodromos", names)
        self.assertIn("Voos e operações aéreas", names)
        # The parent link must never become an entry.
        self.assertNotIn("..", names)
        self.assertNotIn("", names)

    def test_root_classifies_directories_and_files(self):
        entries = listing.parse_listing(fixture_text("listing_root.html"))
        by_name = {e.name: e for e in entries}

        self.assertTrue(by_name["Aerodromos"].is_dir)
        # The root holds a stray zero-byte "null" file alongside the folders.
        self.assertFalse(by_name["null"].is_dir)

    def test_decodes_accented_names_but_keeps_href_encoded(self):
        entries = listing.parse_listing(fixture_text("listing_aerodromos.html"))
        match = listing.find_entry(entries, "Aeródromos Públicos")

        self.assertIsNotNone(match)
        self.assertEqual("Aeródromos Públicos", match.name)
        self.assertEqual("Aer%C3%B3dromos%20P%C3%BAblicos/", match.href)

    def test_parses_a_real_month_folder(self):
        entries = listing.parse_listing(
            fixture_text("listing_vra_2026_07.html")
        )
        names = sorted(e.name for e in entries)

        self.assertEqual(["VRA_20267.csv", "VRA_20267.json"], names)
        self.assertTrue(all(not e.is_dir for e in entries))

    def test_an_empty_month_folder_yields_no_entries(self):
        entries = listing.parse_listing(
            fixture_text("listing_vra_2026_08_empty.html")
        )
        self.assertEqual([], entries)

    def test_href_joins_onto_the_parent_url(self):
        entries = listing.parse_listing(
            fixture_text("listing_vra_2026_07.html")
        )
        parent = "https://example.test/a%20b/2026/07%20-%20Julho/"
        url = next(e for e in entries if e.name.endswith(".csv")).url(parent)

        self.assertEqual(parent + "VRA_20267.csv", url)


class NormalizeTest(unittest.TestCase):
    def test_folds_case_accents_and_whitespace(self):
        self.assertEqual(
            listing.normalize("Aeródromos Públicos"),
            listing.normalize("  AERODROMOS   PUBLICOS "),
        )

    def test_distinguishes_genuinely_different_names(self):
        self.assertNotEqual(
            listing.normalize("Aeródromos Públicos"),
            listing.normalize("Aeródromos Privados"),
        )


def _portal_routes():
    """The captured portal tree, keyed by decoded request path."""
    base = "/Voos e operações aéreas/Voo Regular Ativo (VRA)/"
    routes = {
        "/": Route(body=fixture_bytes("listing_root.html")),
        "/Aerodromos/": Route(body=fixture_bytes("listing_aerodromos.html")),
        "/Aerodromos/Aeródromos Públicos/": Route(
            body=fixture_bytes("listing_aerodromos_publicos.html")
        ),
        "/Aerodromos/Aeródromos Públicos/Lista de aeródromos públicos/": Route(
            body=fixture_bytes("listing_lista_aerodromos.html")
        ),
        "/Voos e operações aéreas/": Route(
            body=fixture_bytes("listing_voos.html")
        ),
        base: Route(body=fixture_bytes("listing_vra_years.html")),
        base + "2026/": Route(body=fixture_bytes("listing_vra_2026.html")),
        base
        + "2026/07 - Julho/": Route(
            body=fixture_bytes("listing_vra_2026_07.html")
        ),
        "/Voos e operações aéreas/Dados Estatísticos do Transporte Aéreo/": (
            Route(body=fixture_bytes("listing_estatisticos.html"))
        ),
    }
    # 08-12 of 2026 exist on the portal but are empty placeholders.
    empty = fixture_bytes("listing_vra_2026_08_empty.html")
    for month, name in (
        (8, "Agosto"),
        (9, "Setembro"),
        (10, "Outubro"),
        (11, "Novembro"),
        (12, "Dezembro"),
    ):
        routes[base + f"2026/{month:02d} - {name}/"] = Route(body=empty)
    # Earlier months of 2026, mirroring July's shape.
    for month, name in ((6, "Junho"), (5, "Maio"), (4, "Abril")):
        routes[base + f"2026/{month:02d} - {name}/"] = Route(
            body=fixture_bytes("listing_vra_2026_07.html").replace(
                b"20267", f"2026{month}".encode()
            )
        )
    return routes


PORTAL_ROUTES = _portal_routes


class ResolvePathTest(unittest.TestCase):
    def test_resolves_the_accented_aerodrome_path(self):
        dataset = DATASETS_BY_KEY["aerodromos"]
        with FakeServer(_portal_routes()) as server:
            url = listing.resolve_path(
                server.url("/"), dataset.segments, policy=FAST_POLICY
            )
            entries = listing.list_dir(url, policy=FAST_POLICY)

        self.assertTrue(url.endswith("Lista%20de%20aer%C3%B3dromos%20p%C3%BAblicos/"))
        self.assertIn("AerodromosPublicos.csv", [e.name for e in entries])

    def test_missing_segment_reports_what_was_actually_there(self):
        with FakeServer(_portal_routes()) as server:
            with self.assertRaises(listing.PathNotFound) as caught:
                listing.resolve_path(
                    server.url("/"),
                    ("Aerodromos", "Aeroportos Inventados"),
                    policy=FAST_POLICY,
                )

        error = caught.exception
        self.assertEqual("Aeroportos Inventados", error.segment)
        self.assertIn("Aeródromos Públicos/", error.available)
        message = str(error)
        self.assertIn("Aeroportos Inventados", message)
        self.assertIn("Aeródromos Públicos/", message)

    def test_a_404_on_the_parent_is_reported_as_a_missing_segment(self):
        with FakeServer({}) as server:
            with self.assertRaises(listing.PathNotFound) as caught:
                listing.resolve_path(
                    server.url("/"), ("Aerodromos",), policy=FAST_POLICY
                )
        self.assertEqual("Aerodromos", caught.exception.segment)


if __name__ == "__main__":
    unittest.main()
