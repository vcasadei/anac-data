"""The single place network policy lives: user agent, timeouts, pacing, retries.

Every request the tool makes goes through :func:`open_url`, so politeness and
retry behavior cannot drift between call sites.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__

USER_AGENT = (
    f"anac-data/{__version__} (course dataset downloader; "
    "contact: repository owner) Python-urllib"
)

#: Statuses worth retrying. 404 is deliberately absent - a missing file will
#: still be missing on the next attempt.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Policy:
    """Network manners. Tests override this to keep runtimes sane."""

    timeout: float = 60.0
    attempts: int = 4
    backoff_base: float = 1.5
    """Delay before retry N is ``backoff_base ** N`` seconds."""
    delay: float = 0.5
    """Pause before every request, so we never hammer the portal."""


DEFAULT_POLICY = Policy()


class HttpError(Exception):
    """A request failed in a way worth reporting to the operator."""

    def __init__(self, message: str, *, url: str, status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


class NotFound(HttpError):
    """HTTP 404. Never retried."""


@dataclass(frozen=True)
class HeadInfo:
    """What the portal says about a file before we transfer it."""

    url: str
    size: int | None
    """``None`` when the response carried no ``Content-Length``."""
    etag: str | None
    last_modified: str | None
    accepts_ranges: bool

    @property
    def size_known(self) -> bool:
        return self.size is not None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRY_STATUSES
    if isinstance(exc, urllib.error.URLError):
        # DNS failures, refused connections, TLS errors, wrapped timeouts.
        return True
    return isinstance(exc, (TimeoutError, socket.timeout))


def open_url(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    policy: Policy = DEFAULT_POLICY,
    on_retry=None,
):
    """Open ``url`` and return the live response for the caller to read.

    Retries cover establishing the response, not consuming its body: a stream
    that breaks mid-transfer is resumed by the download layer instead, which
    can pick up where it left off rather than starting over.
    """
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)

    last_exc: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        if policy.delay:
            time.sleep(policy.delay)
        request = urllib.request.Request(
            url, method=method, headers=request_headers
        )
        try:
            return urllib.request.urlopen(request, timeout=policy.timeout)
        except urllib.error.HTTPError as exc:
            # An HTTPError holds an open response we are never going to read.
            exc.close()
            if exc.code == 404:
                raise NotFound(
                    f"not found (HTTP 404): {url}", url=url, status=404
                ) from exc
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_exc = exc

        if not _is_retryable(last_exc) or attempt == policy.attempts:
            break
        pause = policy.backoff_base**attempt
        if on_retry:
            on_retry(attempt, pause, last_exc)
        time.sleep(pause)

    status = getattr(last_exc, "code", None)
    raise HttpError(
        f"request failed after {attempt} attempt(s): {url} ({last_exc})",
        url=url,
        status=status,
    ) from last_exc


def fetch_text(url: str, *, policy: Policy = DEFAULT_POLICY, **kwargs) -> str:
    """GET ``url`` and decode it as text. Used for directory listings."""
    raw = fetch(url, policy=policy, **kwargs)
    # nginx serves autoindex pages as UTF-8; fall back rather than crash on a
    # listing that carries an unexpected byte.
    return raw.decode("utf-8", errors="replace")


def fetch(url: str, *, policy: Policy = DEFAULT_POLICY, **kwargs) -> bytes:
    """GET ``url`` and return the whole body. Only for small responses."""
    with open_url(url, policy=policy, **kwargs) as response:
        return response.read()


def head(url: str, *, policy: Policy = DEFAULT_POLICY, **kwargs) -> HeadInfo:
    """Ask the portal about a file without transferring it."""
    with open_url(url, method="HEAD", policy=policy, **kwargs) as response:
        return header_info(url, response.headers)


def header_info(url: str, headers) -> HeadInfo:
    """Read what a response's headers say about the file."""
    raw_length = headers.get("Content-Length")
    size: int | None
    try:
        size = int(raw_length) if raw_length is not None else None
    except ValueError:
        size = None
    return HeadInfo(
        url=url,
        size=size,
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
        accepts_ranges=(headers.get("Accept-Ranges", "").lower() == "bytes"),
    )
