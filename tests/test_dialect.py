import _bootstrap  # noqa: F401

import codecs
import unittest

from support import fixture_bytes

from anac_data import dialect


class RealFileTest(unittest.TestCase):
    """The two required datasets genuinely disagree about encoding."""

    def test_vra_is_utf8_with_a_bom(self):
        found = dialect.detect_bytes(fixture_bytes("csv_head_vra.bin"))

        self.assertEqual("utf-8", found.encoding)
        self.assertTrue(found.has_bom)
        self.assertEqual("UTF-8 with BOM", found.encoding_label)
        self.assertEqual(";", found.delimiter)
        self.assertEqual(2, found.header_line)
        self.assertEqual("ICAO Empresa Aérea", found.columns[0])
        self.assertEqual(12, len(found.columns))

    def test_aerodrome_list_is_latin1(self):
        found = dialect.detect_bytes(fixture_bytes("csv_head_aerodromos.bin"))

        self.assertEqual("latin-1", found.encoding)
        self.assertFalse(found.has_bom)
        self.assertIn("Latin-1", found.encoding_label)
        self.assertEqual(";", found.delimiter)
        self.assertEqual(2, found.header_line)
        # Would be mojibake if the file had been read as UTF-8.
        self.assertEqual("Código OACI", found.columns[0])
        self.assertEqual("Município", found.columns[3])

    def test_publication_date_comes_from_the_aerodrome_file_itself(self):
        found = dialect.detect_bytes(fixture_bytes("csv_head_aerodromos.bin"))
        self.assertEqual("2026-09-15", found.publication_date)

    def test_publication_date_comes_from_the_vra_file_itself(self):
        found = dialect.detect_bytes(fixture_bytes("csv_head_vra.bin"))
        self.assertEqual("2026-08-28", found.publication_date)


class HeaderDetectionTest(unittest.TestCase):
    def test_metadata_line_pushes_the_header_to_line_two(self):
        raw = "Atualizado em: 2026-09-15\r\nA;B;C\r\n1;2;3\r\n".encode()
        found = dialect.detect_bytes(raw)

        self.assertEqual(2, found.header_line)
        self.assertEqual(("A", "B", "C"), found.columns)
        self.assertEqual("2026-09-15", found.publication_date)

    def test_header_on_line_one_is_not_pushed_down(self):
        """No metadata line is invented when the file does not have one."""
        raw = "A;B;C\r\n1;2;3\r\n4;5;6\r\n".encode()
        found = dialect.detect_bytes(raw)

        self.assertEqual(1, found.header_line)
        self.assertEqual(("A", "B", "C"), found.columns)
        self.assertIsNone(found.publication_date)

    def test_quoted_header_fields_are_unquoted(self):
        raw = b'Atualizado em: 2026-01-01\r\n"ICAO";"Voo"\r\n"GLO";"1015"\r\n'
        found = dialect.detect_bytes(raw)
        self.assertEqual(("ICAO", "Voo"), found.columns)


class DelimiterTest(unittest.TestCase):
    def test_picks_comma_when_the_file_uses_commas(self):
        raw = b"a,b,c\r\n1,2,3\r\n"
        self.assertEqual(",", dialect.detect_bytes(raw).delimiter)

    def test_picks_tab_when_the_file_uses_tabs(self):
        raw = b"a\tb\tc\r\n1\t2\t3\r\n"
        self.assertEqual("\t", dialect.detect_bytes(raw).delimiter)

    def test_single_column_file_has_no_delimiter(self):
        raw = b"onlyonecolumn\r\nvalue\r\n"
        found = dialect.detect_bytes(raw)
        self.assertIsNone(found.delimiter)
        self.assertIsNone(found.header_line)


class UndeterminedTest(unittest.TestCase):
    def test_binary_content_is_reported_as_undetermined(self):
        # A PNG header plus compressed noise: valid in no text encoding.
        raw = b"\x89PNG\r\n\x1a\n" + bytes(range(8)) * 500
        found = dialect.detect_bytes(raw)

        self.assertEqual(dialect.UNDETERMINED, found.encoding)
        self.assertEqual(dialect.UNDETERMINED, found.encoding_label)
        self.assertIsNone(found.delimiter)
        self.assertIsNone(found.header_line)

    def test_latin1_is_not_used_to_rubber_stamp_utf8(self):
        """Latin-1 decodes anything, so UTF-8 must be tried first."""
        self.assertEqual(
            ("utf-8", "latin-1"), dialect.ENCODING_CANDIDATES
        )
        raw = "Atualizado em: 2026-01-01\r\nA;Ação\r\n".encode("utf-8")
        self.assertEqual("utf-8", dialect.detect_bytes(raw).encoding)


class TruncatedSampleTest(unittest.TestCase):
    def test_a_cut_multibyte_character_does_not_look_like_bad_utf8(self):
        """The sample boundary can land inside a character; that is not corruption."""
        text = "Atualizado em: 2026-01-01\r\nA;B\r\n" + "çãé;x\r\n" * 2000
        raw = codecs.BOM_UTF8 + text.encode("utf-8")

        # Cut well into the data, one byte inside a two-byte character - what
        # a fixed-size read does to a file full of accents.
        cut_at = next(
            i for i in range(20_000, len(raw)) if 0x80 <= raw[i] <= 0xBF
        )
        cut = raw[:cut_at]
        with self.assertRaises(UnicodeDecodeError):
            cut[len(codecs.BOM_UTF8) :].decode("utf-8")

        found = dialect.detect_bytes(cut)
        self.assertEqual("utf-8", found.encoding)
        self.assertTrue(found.has_bom)
        self.assertEqual(2, found.header_line)
        self.assertEqual(("A", "B"), found.columns)


class DescribeTest(unittest.TestCase):
    def test_describes_a_real_file_for_the_inspect_command(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AerodromosPublicos.csv"
            path.write_bytes(fixture_bytes("csv_head_aerodromos.bin"))
            text = dialect.describe(path)

        self.assertIn("AerodromosPublicos.csv", text)
        self.assertIn("Latin-1", text)
        self.assertIn("';'", text)
        self.assertIn("line 2", text)
        self.assertIn("2026-09-15", text)


if __name__ == "__main__":
    unittest.main()
