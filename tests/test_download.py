import _bootstrap  # noqa: F401

import hashlib
import tempfile
import unittest
from pathlib import Path

from support import FAST_POLICY, FakeServer, Route

from anac_data import download

BODY = bytes(range(256)) * 400  # 102,400 bytes, non-repeating enough to catch
# an offset bug that a run of identical bytes would hide.
BODY_SHA = hashlib.sha256(BODY).hexdigest()


class DownloadTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def dest(self, name="VRA_20267.csv"):
        return self.tmp / "vra" / name


class FreshDownloadTest(DownloadTestCase):
    def test_stores_the_file_with_a_matching_digest(self):
        dest = self.dest()
        with FakeServer({"/f.csv": Route(body=BODY)}) as server:
            result = download.download(
                server.url("/f.csv"), dest, policy=FAST_POLICY
            )

        self.assertEqual(BODY, dest.read_bytes())
        self.assertEqual(BODY_SHA, result.sha256)
        self.assertEqual(len(BODY), result.size)
        self.assertTrue(result.size_verified)
        self.assertEqual(0, result.resumed_from)

    def test_leaves_no_part_file_behind(self):
        dest = self.dest()
        with FakeServer({"/f.csv": Route(body=BODY)}) as server:
            download.download(server.url("/f.csv"), dest, policy=FAST_POLICY)

        self.assertFalse(download.part_path(dest).exists())
        self.assertEqual([dest.name], [p.name for p in dest.parent.iterdir()])

    def test_creates_the_dataset_directory(self):
        dest = self.dest()
        self.assertFalse(dest.parent.exists())
        with FakeServer({"/f.csv": Route(body=BODY)}) as server:
            download.download(server.url("/f.csv"), dest, policy=FAST_POLICY)
        self.assertTrue(dest.is_file())

    def test_records_the_portal_validators(self):
        route = Route(
            body=BODY,
            headers={"ETag": '"6a91341a"', "Last-Modified": "Fri, 28 Aug 2026 07:09:14 GMT"},
        )
        with FakeServer({"/f.csv": route}) as server:
            result = download.download(
                server.url("/f.csv"), self.dest(), policy=FAST_POLICY
            )
        self.assertEqual('"6a91341a"', result.etag)
        self.assertEqual("Fri, 28 Aug 2026 07:09:14 GMT", result.last_modified)


class ResumeTest(DownloadTestCase):
    def test_resumes_from_the_stored_offset(self):
        dest = self.dest()
        partial = download.part_path(dest)
        partial.parent.mkdir(parents=True)
        cut = 40_000
        partial.write_bytes(BODY[:cut])

        route = Route(body=BODY, support_ranges=True)
        with FakeServer({"/f.csv": route}) as server:
            result = download.download(
                server.url("/f.csv"), dest, policy=FAST_POLICY
            )
            sent_range = server.requests_for("/f.csv")[0]["range"]

        self.assertEqual(f"bytes={cut}-", sent_range)
        self.assertEqual(cut, result.resumed_from)
        # Only the remainder crossed the wire.
        self.assertEqual(len(BODY) - cut, result.bytes_transferred)
        # And the file is byte-identical to an uninterrupted download.
        self.assertEqual(BODY, dest.read_bytes())
        self.assertEqual(BODY_SHA, result.sha256)
        self.assertFalse(partial.exists())

    def test_restarts_cleanly_when_the_server_ignores_the_range(self):
        dest = self.dest()
        partial = download.part_path(dest)
        partial.parent.mkdir(parents=True)
        partial.write_bytes(BODY[:40_000])

        notes = []
        route = Route(body=BODY, support_ranges=True, ignore_ranges=True)
        with FakeServer({"/f.csv": route}) as server:
            result = download.download(
                server.url("/f.csv"),
                dest,
                policy=FAST_POLICY,
                progress=notes.append,
            )

        # The partial bytes must not have been appended to.
        self.assertEqual(BODY, dest.read_bytes())
        self.assertEqual(BODY_SHA, result.sha256)
        self.assertEqual(len(BODY), result.size)
        self.assertEqual(0, result.resumed_from)
        self.assertTrue(any("ignored the range" in n for n in notes), notes)

    def test_a_partial_file_is_never_visible_at_the_destination(self):
        """An interrupted transfer leaves the destination absent, not truncated."""
        dest = self.dest()
        route = Route(body=BODY, advertise_size=len(BODY), truncate_to=30_000)
        with FakeServer({"/f.csv": route}) as server:
            with self.assertRaises(download.SizeMismatch):
                download.download(
                    server.url("/f.csv"), dest, policy=FAST_POLICY
                )

        self.assertFalse(dest.exists())

    def test_an_interrupted_transfer_leaves_bytes_to_resume_from(self):
        """A short delivery keeps its .part, so a retry can finish the job."""
        dest = self.dest()
        interrupted = Route(
            body=BODY, advertise_size=len(BODY), truncate_to=30_000
        )
        with FakeServer({"/f.csv": interrupted}) as server:
            with self.assertRaises(download.SizeMismatch):
                download.download(
                    server.url("/f.csv"), dest, policy=FAST_POLICY
                )
            partial = download.part_path(dest)
            self.assertEqual(BODY[:30_000], partial.read_bytes())

        # A second run against a healthy server resumes and completes.
        with FakeServer({"/f.csv": Route(body=BODY, support_ranges=True)}) as server:
            result = download.download(
                server.url("/f.csv"), dest, policy=FAST_POLICY
            )
            self.assertEqual(f"bytes={30_000}-", server.requests_for("/f.csv")[0]["range"])

        self.assertEqual(BODY, dest.read_bytes())
        self.assertEqual(BODY_SHA, result.sha256)
        self.assertEqual(30_000, result.resumed_from)


class IntegrityTest(DownloadTestCase):
    def test_short_delivery_is_reported_and_discarded(self):
        dest = self.dest()
        route = Route(body=BODY, advertise_size=len(BODY), truncate_to=1000)
        with FakeServer({"/f.csv": route}) as server:
            with self.assertRaises(download.SizeMismatch) as caught:
                download.download(
                    server.url("/f.csv"), dest, policy=FAST_POLICY
                )

        error = caught.exception
        self.assertEqual(len(BODY), error.expected)
        self.assertEqual(1000, error.actual)
        self.assertFalse(dest.exists())
        self.assertIn("size mismatch", str(error))

    def test_a_stale_oversized_partial_is_discarded_not_resumed(self):
        """The portal replaced the file with a smaller one since we last tried."""
        dest = self.dest()
        partial = download.part_path(dest)
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"leftovers from a bigger, older file" * 5000)
        self.assertGreater(partial.stat().st_size, len(BODY))

        notes = []
        route = Route(body=BODY, support_ranges=True)
        with FakeServer({"/f.csv": route}) as server:
            result = download.download(
                server.url("/f.csv"),
                dest,
                expected_size=len(BODY),
                policy=FAST_POLICY,
                progress=notes.append,
            )
            # No doomed Range request went out.
            self.assertIsNone(server.requests_for("/f.csv")[0]["range"])

        self.assertEqual(BODY, dest.read_bytes())
        self.assertEqual(BODY_SHA, result.sha256)
        self.assertEqual(0, result.resumed_from)
        self.assertTrue(any("stale partial file" in n for n in notes), notes)

    def test_missing_content_length_keeps_the_file_and_flags_it(self):
        dest = self.dest()
        route = Route(body=BODY, omit_content_length=True)
        with FakeServer({"/f.csv": route}) as server:
            result = download.download(
                server.url("/f.csv"), dest, policy=FAST_POLICY
            )

        self.assertTrue(dest.is_file())
        self.assertEqual(BODY, dest.read_bytes())
        self.assertFalse(result.size_verified)
        self.assertEqual(BODY_SHA, result.sha256)
        self.assertEqual(len(BODY), result.size)


class Sha256FileTest(DownloadTestCase):
    def test_digests_a_file_in_chunks(self):
        path = self.tmp / "big.bin"
        payload = BODY * 12  # larger than one CHUNK
        path.write_bytes(payload)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(), download.sha256_file(path)
        )


if __name__ == "__main__":
    unittest.main()
