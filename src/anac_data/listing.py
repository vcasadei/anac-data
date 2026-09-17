"""Reading nginx `autoindex` directory listings.

The portal has no API - navigation is done by fetching a folder's HTML index
and following the links it contains. We follow nginx's own ``href`` values
rather than building paths ourselves, because the folder names are full of
spaces and accents whose percent-encoding is easy to get subtly wrong.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from . import http as anac_http


@dataclass(frozen=True)
class Entry:
    """One row of a directory listing."""

    name: str
    """Decoded display name, without any trailing slash."""
    href: str
    """The link exactly as nginx wrote it, ready for :func:`urljoin`."""
    is_dir: bool

    def url(self, parent_url: str) -> str:
        return urljoin(parent_url, self.href)


class PathNotFound(Exception):
    """A folder in the navigation path is not where we expected it.

    Carries what was actually listed, so the operator can see how the portal
    has been reorganized instead of just being told the tool failed.
    """

    def __init__(
        self,
        segment: str,
        parent_url: str,
        available: list[str],
        *,
        status: int | None = None,
    ):
        self.segment = segment
        self.parent_url = parent_url
        self.available = available
        self.status = status
        if status is not None:
            detail = (
                f"  the portal answered HTTP {status} for the folder itself, "
                "so its contents could not be listed"
            )
        else:
            listed = ", ".join(available) if available else "(nothing)"
            detail = f"  the portal listed: {listed}"
        super().__init__(
            f"folder {segment!r} not found under {parent_url}\n{detail}"
        )


class _IndexParser(HTMLParser):
    """Collects the anchors of an autoindex page, ignoring the parent link."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[Entry] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href or href.startswith(("?", "#")):
            return
        # The parent link, in the forms nginx and other indexes emit it.
        if href in ("../", "..", "/"):
            return
        is_dir = href.endswith("/")
        name = unquote(href).rstrip("/")
        if not name:
            return
        self.entries.append(Entry(name=name, href=href, is_dir=is_dir))


def parse_listing(html: str) -> list[Entry]:
    """Turn an autoindex page into entries. Empty folders yield an empty list."""
    parser = _IndexParser()
    parser.feed(html)
    parser.close()
    return parser.entries


def normalize(text: str) -> str:
    """Fold case, strip accents and collapse whitespace for name matching.

    Lets ``Aeródromos Públicos`` still match if ANAC rewrites it as
    ``AERODROMOS PUBLICOS``.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    return " ".join(without_accents.split()).casefold()


def find_entry(entries: list[Entry], wanted: str) -> Entry | None:
    """Find ``wanted`` among ``entries``, tolerating case and accent drift."""
    target = normalize(wanted)
    for entry in entries:
        if normalize(entry.name) == target:
            return entry
    return None


def list_dir(url: str, *, policy=anac_http.DEFAULT_POLICY) -> list[Entry]:
    """Fetch and parse one folder listing."""
    return parse_listing(anac_http.fetch_text(url, policy=policy))


def resolve_path(
    base_url: str, segments, *, policy=anac_http.DEFAULT_POLICY
) -> str:
    """Walk ``segments`` down from ``base_url``, returning the folder URL.

    Raises :class:`PathNotFound` naming the segment that went missing and what
    the portal listed in its place.
    """
    current = base_url
    for segment in segments:
        try:
            entries = list_dir(current, policy=policy)
        except anac_http.NotFound:
            # The folder we are standing in is gone, so the segment we wanted
            # cannot be found either - and we cannot say what is there now.
            raise PathNotFound(segment, current, [], status=404) from None
        match = find_entry(entries, segment)
        if match is None or not match.is_dir:
            raise PathNotFound(
                segment,
                current,
                [e.name + ("/" if e.is_dir else "") for e in entries],
            )
        current = match.url(current)
    return current
