"""Streaming downloads that are safe to interrupt.

Bytes land in a sibling ``<name>.part`` file and are moved onto the final path
only once the transfer is complete and its size checks out. Nothing at the
destination path is ever a half-written file.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import http as anac_http

CHUNK = 1024 * 1024

_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)", re.IGNORECASE)


class SizeMismatch(Exception):
    """The transfer finished at a different size than the portal advertised."""

    def __init__(self, url: str, expected: int, actual: int):
        self.url = url
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"size mismatch for {url}: portal advertised {expected} bytes, "
            f"received {actual}"
        )


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str
    etag: str | None
    last_modified: str | None
    size_verified: bool
    """False when the portal advertised no size for us to check against."""
    resumed_from: int = 0
    bytes_transferred: int = 0


def part_path(dest: Path) -> Path:
    """Partial downloads live beside their destination.

    Same directory, so the final move is a rename within one filesystem and
    therefore atomic - which matters because ``data/`` may sit on a Windows
    drive mounted into WSL.
    """
    return dest.with_name(dest.name + ".part")


def download(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    policy=anac_http.DEFAULT_POLICY,
    progress=None,
) -> DownloadResult:
    """Fetch ``url`` to ``dest``, resuming a previous attempt if one is there.

    ``expected_size`` is the size a prior ``HEAD`` reported, used to recognize
    a stale partial file before asking to resume from it.

    Raises :class:`SizeMismatch` if the finished file does not match the
    advertised ``Content-Length``, leaving nothing at ``dest``.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = part_path(dest)

    resume_from = partial.stat().st_size if partial.exists() else 0
    if resume_from and expected_size is not None and resume_from >= expected_size:
        # Left over from a larger, since-replaced version of the file. Asking
        # to resume past the end would just earn a 416.
        if progress:
            progress(
                f"discarding a stale partial file for {dest.name} "
                f"({resume_from} bytes, remote file is {expected_size})"
            )
        partial.unlink()
        resume_from = 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}

    with anac_http.open_url(url, headers=headers, policy=policy) as response:
        status = getattr(response, "status", 200)
        expected_total, appending = _plan(response, status, resume_from)

        if resume_from and not appending:
            # The server ignored our Range and is sending the whole body.
            # Appending it to what we already have would corrupt the file.
            if progress:
                progress(
                    f"server ignored the range request; restarting {dest.name}"
                )
            resume_from = 0

        mode = "ab" if appending else "wb"
        digest = hashlib.sha256()
        transferred = 0
        with open(partial, mode) as handle:
            if appending:
                # A resumed digest cannot continue from a previous process, so
                # it is computed from the finished file further down instead.
                digest = None
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                transferred += len(chunk)
                if digest is not None:
                    digest.update(chunk)

        info = anac_http.header_info(url, response.headers)

    total = partial.stat().st_size

    if expected_total is not None and total != expected_total:
        # Nothing is published to `dest` either way - a short file must never
        # look like a complete one. What happens to the `.part` differs:
        #
        # - short: this is what an interrupted transfer looks like, so the
        #   bytes are kept and the next run resumes from them.
        # - long: we are past the advertised end, so there is no sensible
        #   offset to resume from and the bytes are discarded.
        if total > expected_total:
            partial.unlink(missing_ok=True)
        raise SizeMismatch(url, expected_total, total)

    checksum = digest.hexdigest() if digest is not None else sha256_file(partial)

    os.replace(partial, dest)

    return DownloadResult(
        path=dest,
        size=total,
        sha256=checksum,
        etag=info.etag,
        last_modified=info.last_modified,
        size_verified=expected_total is not None,
        resumed_from=resume_from,
        bytes_transferred=transferred,
    )


def _plan(response, status: int, resume_from: int):
    """Work out the expected final size and whether we are appending.

    On a 206 the ``Content-Length`` covers only the remaining range, so the
    full size comes from ``Content-Range`` instead.
    """
    headers = response.headers
    if status == 206 and resume_from:
        found = _CONTENT_RANGE.search(headers.get("Content-Range", ""))
        if found:
            return int(found.group(3)), True
        # A 206 without a parseable Content-Range: trust the offset plus what
        # is still coming, if the server said how much.
        remaining = _int_or_none(headers.get("Content-Length"))
        if remaining is not None:
            return resume_from + remaining, True
        return None, True
    return _int_or_none(headers.get("Content-Length")), False


def _int_or_none(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    """Digest a file with one sequential pass."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
