import _bootstrap  # noqa: F401

import json
import tempfile
import unittest
from pathlib import Path

from anac_data import manifest
from anac_data.http import HeadInfo


def head(etag=None, last_modified=None, size=10):
    return HeadInfo(
        url="http://example.test/f.csv",
        size=size,
        etag=etag,
        last_modified=last_modified,
        accepts_ranges=True,
    )


class ManifestFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_loading_a_missing_manifest_yields_an_empty_one(self):
        data = manifest.load(self.root)
        self.assertEqual({}, data["files"])

    def test_round_trips_through_disk(self):
        manifest.save(self.root, {"files": {"vra/a.csv": {"size": 1}}})
        data = manifest.load(self.root)

        self.assertEqual(1, data["files"]["vra/a.csv"]["size"])
        self.assertEqual(manifest.SCHEMA_VERSION, data["schema_version"])
        self.assertIn("updated_at", data)

    def test_save_leaves_no_temporary_file(self):
        manifest.save(self.root, {"files": {}})
        self.assertEqual(
            ["manifest.json"], [p.name for p in self.root.iterdir()]
        )

    def test_accented_names_are_stored_readably(self):
        manifest.save(self.root, {"files": {"a/Aeródromos.csv": {"size": 1}}})
        text = manifest.manifest_path(self.root).read_text(encoding="utf-8")
        self.assertIn("Aeródromos.csv", text)

    def test_a_corrupt_manifest_is_treated_as_empty(self):
        manifest.manifest_path(self.root).write_text("{not json", encoding="utf-8")
        self.assertEqual({}, manifest.load(self.root)["files"])

    def test_a_manifest_of_the_wrong_shape_is_treated_as_empty(self):
        manifest.manifest_path(self.root).write_text(
            json.dumps(["a", "list"]), encoding="utf-8"
        )
        self.assertEqual({}, manifest.load(self.root)["files"])


class IsUnchangedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dest = self.root / "vra" / "f.csv"
        self.dest.parent.mkdir(parents=True)
        self.dest.write_bytes(b"0123456789")
        self.addCleanup(self._tmp.cleanup)

    def test_matching_etag_means_unchanged(self):
        entry = {"etag": '"abc"', "size": 10}
        self.assertTrue(manifest.is_unchanged(entry, head(etag='"abc"'), self.dest))

    def test_different_etag_means_changed(self):
        entry = {"etag": '"abc"', "size": 10}
        self.assertFalse(manifest.is_unchanged(entry, head(etag='"xyz"'), self.dest))

    def test_last_modified_is_used_when_there_is_no_etag(self):
        entry = {"etag": None, "last_modified": "Mon, 01 Jan 2026 00:00:00 GMT", "size": 10}
        self.assertTrue(
            manifest.is_unchanged(
                entry,
                head(last_modified="Mon, 01 Jan 2026 00:00:00 GMT"),
                self.dest,
            )
        )
        self.assertFalse(
            manifest.is_unchanged(
                entry,
                head(last_modified="Tue, 02 Jan 2026 00:00:00 GMT"),
                self.dest,
            )
        )

    def test_a_file_with_no_entry_is_unknown_not_current(self):
        self.assertFalse(manifest.is_unchanged(None, head(etag='"abc"'), self.dest))

    def test_a_missing_file_is_never_current(self):
        entry = {"etag": '"abc"', "size": 10}
        self.assertFalse(
            manifest.is_unchanged(entry, head(etag='"abc"'), self.root / "gone.csv")
        )

    def test_a_file_whose_size_drifted_is_not_current(self):
        """Guards against a truncated or externally modified local copy."""
        entry = {"etag": '"abc"', "size": 999}
        self.assertFalse(manifest.is_unchanged(entry, head(etag='"abc"'), self.dest))

    def test_no_validators_at_all_means_not_current(self):
        entry = {"etag": None, "last_modified": None, "size": 10}
        self.assertFalse(manifest.is_unchanged(entry, head(), self.dest))


if __name__ == "__main__":
    unittest.main()
