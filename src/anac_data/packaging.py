"""Turning an oversized file into committable chunks, and back again.

A file too large for the repository host is stored as a gzip stream cut into
fixed-size pieces. Compression is deterministic, so repackaging unchanged data
rewrites nothing and adds nothing to git history.

The chunks are opaque on their own - they mean something only concatenated in
name order. Everything needed to put them back together, and to prove the
result is right, travels with them in `MANIFEST.json` and `checksums.sha256`.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import (
    CHUNK_INDEX_WIDTH,
    CHUNK_LIMIT_BYTES,
    GZIP_COMPRESS_LEVEL,
    GZIP_MTIME,
)

MANIFEST_NAME = "MANIFEST.json"
CHECKSUMS_NAME = "checksums.sha256"

READ_CHUNK = 1024 * 1024

#: Bumped if the on-disk chunk-set layout changes, so a rebuild script can tell.
FORMAT_VERSION = 1

MAX_CHUNKS = 10**CHUNK_INDEX_WIDTH - 1


class PackagingError(Exception):
    """Packaging cannot proceed; the message says what the operator must fix."""


@dataclass
class ChunkInfo:
    name: str
    size: int
    sha256: str


@dataclass
class PackageResult:
    """What packaging did to one file."""

    dataset: str
    source: Path
    outcome: str
    """One of: packaged, unchanged, not_needed."""
    chunks: list[ChunkInfo] = field(default_factory=list)
    original_size: int = 0
    original_sha256: str = ""
    compressed_size: int = 0
    compressed_sha256: str = ""
    chunk_size: int = 0
    dist_dir: Path | None = None

    @property
    def total_chunk_bytes(self) -> int:
        return sum(c.size for c in self.chunks)


def sha256_file(path: Path) -> str:
    """Digest a file with one sequential pass."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(READ_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def compress_bytes(data: bytes) -> bytes:
    """Deterministically gzip ``data``.

    The timestamp gzip would normally take from the clock is pinned, so the
    same input always produces the same bytes.
    """
    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        fileobj=buffer,
        mode="wb",
        compresslevel=GZIP_COMPRESS_LEVEL,
        mtime=GZIP_MTIME,
    ) as handle:
        handle.write(data)
    return buffer.getvalue()


def compress_file(source: Path, destination: Path) -> None:
    """Deterministically gzip ``source`` into ``destination``, streaming.

    Streamed rather than read whole: the statistical CSV is ~342 MiB and there
    is no reason to hold it in memory.
    """
    with open(source, "rb") as reader, open(destination, "wb") as raw:
        # filename="" keeps the destination's name out of the gzip header.
        # Left to itself GzipFile takes it from fileobj.name and sets FNAME,
        # which would make the output depend on the temporary file's path.
        with gzip.GzipFile(
            filename="",
            fileobj=raw,
            mode="wb",
            compresslevel=GZIP_COMPRESS_LEVEL,
            mtime=GZIP_MTIME,
        ) as writer:
            while True:
                block = reader.read(READ_CHUNK)
                if not block:
                    break
                writer.write(block)


def chunk_name(original_name: str, index: int) -> str:
    """``VRA.csv``, 1 -> ``VRA.csv.gz.part001``."""
    return f"{original_name}.gz.part{index:0{CHUNK_INDEX_WIDTH}d}"


def plan_chunk_count(stream_size: int, chunk_size: int) -> int:
    if stream_size == 0:
        return 0
    return (stream_size + chunk_size - 1) // chunk_size


def needs_packaging(size: int, limit: int = CHUNK_LIMIT_BYTES) -> bool:
    """A file at or under the limit can be committed as itself."""
    return size > limit


def split_stream(source: Path, dist_dir: Path, original_name: str, chunk_size: int):
    """Cut ``source`` into chunks in ``dist_dir``. Returns their ChunkInfos."""
    total = source.stat().st_size
    planned = plan_chunk_count(total, chunk_size)
    if planned > MAX_CHUNKS:
        raise PackagingError(
            f"{original_name} would need {planned} chunks at "
            f"{chunk_size} bytes each, but the name format only sorts "
            f"correctly up to {MAX_CHUNKS}. Use a larger --chunk-size."
        )

    chunks: list[ChunkInfo] = []
    with open(source, "rb") as reader:
        for index in range(1, planned + 1):
            name = chunk_name(original_name, index)
            target = dist_dir / name
            digest = hashlib.sha256()
            written = 0
            with open(target, "wb") as writer:
                while written < chunk_size:
                    block = reader.read(min(READ_CHUNK, chunk_size - written))
                    if not block:
                        break
                    writer.write(block)
                    digest.update(block)
                    written += len(block)
            chunks.append(
                ChunkInfo(name=name, size=written, sha256=digest.hexdigest())
            )
    return chunks


def _write_manifest(dist_dir: Path, result: PackageResult) -> None:
    payload = {
        "format_version": FORMAT_VERSION,
        "dataset": result.dataset,
        "original": {
            "name": result.source.name,
            "size": result.original_size,
            "sha256": result.original_sha256,
        },
        "compressed": {
            "size": result.compressed_size,
            "sha256": result.compressed_sha256,
        },
        "chunk_size": result.chunk_size,
        "chunks": [
            {"name": c.name, "size": c.size, "sha256": c.sha256}
            for c in result.chunks
        ],
    }
    _atomic_write_text(
        dist_dir / MANIFEST_NAME,
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _write_checksums(dist_dir: Path, result: PackageResult) -> None:
    """The shell script's only input, in a format `sha256sum -c` reads directly.

    The two scalars the plain listing cannot express travel as comment lines,
    which `sha256sum -c` ignores and `sed` can pick out - so the POSIX script
    needs no JSON parser.
    """
    lines = [
        f"# original-name: {result.source.name}",
        f"# original-size: {result.original_size}",
        f"# original-sha256: {result.original_sha256}",
        f"# compressed-sha256: {result.compressed_sha256}",
        f"# chunk-size: {result.chunk_size}",
        f"# format-version: {FORMAT_VERSION}",
    ]
    lines += [f"{c.sha256}  {c.name}" for c in result.chunks]
    _atomic_write_text(dist_dir / CHECKSUMS_NAME, "\n".join(lines) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def read_manifest(dist_dir: Path) -> dict | None:
    path = Path(dist_dir) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def is_current(dist_dir: Path, source_sha256: str, chunk_size: int) -> bool:
    """Whether the chunk set on disk already represents this exact content.

    Requires the recorded source digest and chunk size to match, and every
    listed chunk to be present at its recorded size - so a half-deleted chunk
    set is repackaged rather than trusted.
    """
    manifest = read_manifest(dist_dir)
    if not manifest:
        return False
    if manifest.get("format_version") != FORMAT_VERSION:
        return False
    if manifest.get("original", {}).get("sha256") != source_sha256:
        return False
    if manifest.get("chunk_size") != chunk_size:
        return False
    listed = manifest.get("chunks")
    if not isinstance(listed, list) or not listed:
        return False
    for entry in listed:
        chunk = Path(dist_dir) / entry.get("name", "")
        if not chunk.is_file() or chunk.stat().st_size != entry.get("size"):
            return False
    return True


def _remove_stale(dist_dir: Path, keep: set[str]) -> list[str]:
    """Delete chunks that are not part of the current set.

    Without this, shrinking content would leave a longer run's trailing chunks
    behind and `cat *.part*` would append them onto good data.
    """
    removed = []
    for existing in sorted(dist_dir.glob("*.gz.part*")):
        if existing.name not in keep:
            existing.unlink()
            removed.append(existing.name)
    return removed


def chunk_dir_for(dist_root: Path, dataset: str, source_name: str) -> Path:
    """Where one file's chunk set lives: ``<dist>/<dataset>/<filename>/``.

    One directory per *file*, not per dataset. A dataset can hold several files
    (VRA has one per month) and each needs its own manifest and its own stale-
    chunk scope - sharing a directory would let one file's cleanup delete
    another's chunks.
    """
    return Path(dist_root) / dataset / source_name


def package_file(
    source: Path,
    dist_dir: Path,
    *,
    dataset: str,
    chunk_size: int = CHUNK_LIMIT_BYTES,
    limit: int | None = None,
    force: bool = False,
    source_sha256: str | None = None,
    progress=None,
) -> PackageResult:
    """Package ``source`` into ``dist_dir`` if it is over the limit.

    ``dist_dir`` holds the chunk set for this one file - see
    :func:`chunk_dir_for`. ``limit`` defaults to ``chunk_size``: a file that
    fits in one chunk does not need chunking at all. ``source_sha256`` skips
    re-digesting a file whose digest the caller already knows.
    """
    source = Path(source)
    dist_dir = Path(dist_dir)
    if limit is None:
        limit = chunk_size

    if not source.is_file():
        raise PackagingError(
            f"{dataset}: {source} is not present - download it first "
            f"(python3 -m anac_data fetch)"
        )

    size = source.stat().st_size
    if not needs_packaging(size, limit):
        return PackageResult(
            dataset=dataset,
            source=source,
            outcome="not_needed",
            original_size=size,
            chunk_size=chunk_size,
        )

    digest = source_sha256 or sha256_file(source)

    if not force and is_current(dist_dir, digest, chunk_size):
        manifest = read_manifest(dist_dir) or {}
        return PackageResult(
            dataset=dataset,
            source=source,
            outcome="unchanged",
            chunks=[
                ChunkInfo(c["name"], c["size"], c["sha256"])
                for c in manifest.get("chunks", [])
            ],
            original_size=size,
            original_sha256=digest,
            compressed_size=manifest.get("compressed", {}).get("size", 0),
            compressed_sha256=manifest.get("compressed", {}).get("sha256", ""),
            chunk_size=chunk_size,
            dist_dir=dist_dir,
        )

    dist_dir.mkdir(parents=True, exist_ok=True)
    compressed = dist_dir / (source.name + ".gz.tmp")
    if progress:
        progress(f"compressing {source.name} ({size:,} bytes)")
    try:
        compress_file(source, compressed)
        compressed_size = compressed.stat().st_size
        compressed_sha = sha256_file(compressed)
        if progress:
            progress(
                f"compressed to {compressed_size:,} bytes "
                f"({100 * compressed_size / size:.1f}% of the original)"
            )
        chunks = split_stream(compressed, dist_dir, source.name, chunk_size)
    finally:
        compressed.unlink(missing_ok=True)

    stale = _remove_stale(dist_dir, {c.name for c in chunks})
    if stale and progress:
        progress(f"removed {len(stale)} stale chunk(s): {', '.join(stale)}")

    result = PackageResult(
        dataset=dataset,
        source=source,
        outcome="packaged",
        chunks=chunks,
        original_size=size,
        original_sha256=digest,
        compressed_size=compressed_size,
        compressed_sha256=compressed_sha,
        chunk_size=chunk_size,
        dist_dir=dist_dir,
    )
    _write_manifest(dist_dir, result)
    _write_checksums(dist_dir, result)
    return result
