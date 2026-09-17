import _bootstrap  # noqa: F401

import unittest

from support import FAST_POLICY, FakeServer, Route

from anac_data import http as anac_http


class FetchTest(unittest.TestCase):
    def test_fetch_returns_body_and_sends_user_agent(self):
        with FakeServer({"/ok": Route(body=b"hello portal")}) as server:
            body = anac_http.fetch(server.url("/ok"), policy=FAST_POLICY)
            self.assertEqual(b"hello portal", body)
            sent = server.requests_for("/ok")[0]["user_agent"]
        self.assertEqual(anac_http.USER_AGENT, sent)
        self.assertIn("anac-data/", sent)

    def test_fetch_text_decodes_utf8_listing(self):
        with FakeServer({"/i": Route(body="Aeródromos".encode())}) as server:
            self.assertEqual(
                "Aeródromos",
                anac_http.fetch_text(server.url("/i"), policy=FAST_POLICY),
            )


class RetryTest(unittest.TestCase):
    def test_server_error_is_retried_up_to_the_attempt_limit(self):
        with FakeServer({"/boom": Route(status=503)}) as server:
            with self.assertRaises(anac_http.HttpError) as caught:
                anac_http.fetch(server.url("/boom"), policy=FAST_POLICY)
            attempts = len(server.requests_for("/boom"))
        self.assertEqual(FAST_POLICY.attempts, attempts)
        self.assertEqual(503, caught.exception.status)

    def test_not_found_is_reported_without_retrying(self):
        with FakeServer({"/gone": Route(status=404)}) as server:
            with self.assertRaises(anac_http.NotFound) as caught:
                anac_http.fetch(server.url("/gone"), policy=FAST_POLICY)
            attempts = len(server.requests_for("/gone"))
        self.assertEqual(1, attempts)
        self.assertEqual(404, caught.exception.status)

    def test_not_found_is_an_http_error_subclass(self):
        # Callers that only care about failure can catch the base class.
        self.assertTrue(issubclass(anac_http.NotFound, anac_http.HttpError))

    def test_404_is_not_in_the_retry_set(self):
        self.assertNotIn(404, anac_http.RETRY_STATUSES)


class HeadTest(unittest.TestCase):
    def test_head_reports_size_validators_and_range_support(self):
        route = Route(
            body=b"x" * 1234,
            headers={
                "ETag": '"abc123"',
                "Last-Modified": "Fri, 28 Aug 2026 07:09:14 GMT",
            },
            support_ranges=True,
        )
        with FakeServer({"/f.csv": route}) as server:
            info = anac_http.head(server.url("/f.csv"), policy=FAST_POLICY)
            method = server.requests_for("/f.csv")[0]["method"]
        self.assertEqual("HEAD", method)
        self.assertEqual(1234, info.size)
        self.assertTrue(info.size_known)
        self.assertEqual('"abc123"', info.etag)
        self.assertEqual("Fri, 28 Aug 2026 07:09:14 GMT", info.last_modified)
        self.assertTrue(info.accepts_ranges)

    def test_head_tolerates_a_missing_content_length(self):
        route = Route(body=b"y" * 10, omit_content_length=True)
        with FakeServer({"/nolen": route}) as server:
            info = anac_http.head(server.url("/nolen"), policy=FAST_POLICY)
        self.assertIsNone(info.size)
        self.assertFalse(info.size_known)
        self.assertFalse(info.accepts_ranges)


if __name__ == "__main__":
    unittest.main()
