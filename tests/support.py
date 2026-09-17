"""A tiny local HTTP server so network behavior is tested without the portal.

Routes are registered per test, which keeps each test's expectations next to
the responses that produce them.
"""

import _bootstrap  # noqa: F401  (sys.path setup)

import pathlib
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

from anac_data import http as anac_http

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def autoindex(entries) -> bytes:
    """Build an nginx-style autoindex page.

    ``entries`` is a sequence of names; a trailing ``/`` marks a directory.
    Used where a hand-built tree states a test's intent more plainly than a
    captured fixture would.
    """
    rows = ['<a href="../">../</a>']
    for name in entries:
        rows.append(f'<a href="{quote(name)}">{name}</a>')
    body = "\n".join(rows)
    return (
        f"<html><head><title>Index of /</title></head><body>"
        f"<h1>Index of /</h1><hr><pre>{body}\n</pre><hr></body></html>"
    ).encode("utf-8")

#: No pacing and negligible backoff, so tests stay fast. What is under test is
#: *whether* a retry happens and how often, not how long it waits.
FAST_POLICY = anac_http.Policy(
    timeout=5.0, attempts=3, backoff_base=0.001, delay=0.0
)


@dataclass
class Route:
    """How the fake server answers one path."""

    body: bytes = b""
    status: int = 200
    headers: dict = field(default_factory=dict)
    omit_content_length: bool = False
    support_ranges: bool = False
    ignore_ranges: bool = False
    """Answer a Range request with the full body and a 200, as some servers do."""
    advertise_size: int | None = None
    """Send this as Content-Length instead of the real body length."""
    truncate_to: int | None = None
    """Send only this many bytes of the body."""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output clean
        pass

    def _route(self):
        routes = self.server.routes
        if self.path in routes:
            return routes[self.path]
        # Tests register readable keys like "/Aerodromos/Aeródromos Públicos/",
        # while the client sends them percent-encoded.
        return routes.get(unquote(self.path))

    def _record(self):
        self.server.requests.append(
            {
                "path": self.path,
                "method": self.command,
                "user_agent": self.headers.get("User-Agent"),
                "range": self.headers.get("Range"),
            }
        )

    def do_HEAD(self):
        self._handle(body_wanted=False)

    def do_GET(self):
        self._handle(body_wanted=True)

    def _handle(self, *, body_wanted):
        self._record()
        route = self._route()
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if route.status >= 400:
            self.send_response(route.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = route.body
        status = route.status
        extra_headers = dict(route.headers)

        requested_range = self.headers.get("Range")
        if requested_range and route.support_ranges and not route.ignore_ranges:
            start = int(requested_range.split("=", 1)[1].split("-", 1)[0])
            extra_headers["Content-Range"] = (
                f"bytes {start}-{len(body) - 1}/{len(body)}"
            )
            body = body[start:]
            status = 206

        if route.truncate_to is not None:
            body = body[: route.truncate_to]

        self.send_response(status)
        for key, value in extra_headers.items():
            self.send_header(key, value)
        if route.support_ranges:
            self.send_header("Accept-Ranges", "bytes")
        if route.omit_content_length:
            # Without Content-Length the client needs the connection closed to
            # know where the body ends.
            self.send_header("Connection", "close")
            self.close_connection = True
        else:
            advertised = (
                route.advertise_size
                if route.advertise_size is not None
                else len(body)
            )
            self.send_header("Content-Length", str(advertised))
        self.end_headers()
        if body_wanted and body:
            self.wfile.write(body)


class FakeServer:
    """Context manager exposing ``url(path)`` and a log of requests."""

    def __init__(self, routes=None):
        # Held by reference, not copied: tests change what the portal serves
        # part-way through a run (a republished file, a folder that vanishes).
        self.routes = routes if routes is not None else {}

    def __enter__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.routes = self.routes
        self._server.requests = []
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def requests(self):
        return self._server.requests

    def url(self, path):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{path}"

    def requests_for(self, path):
        return [r for r in self.requests if r["path"] == path]
