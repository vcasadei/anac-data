"""The record of what is on disk and where it came from.

A single ``manifest.json`` at the output root, keyed by the file's path
relative to that root. It doubles as the revalidation cache: the ``ETag`` and
``Last-Modified`` recorded here are what a later run compares against, so no
separate state store is needed.

Runs merge into it rather than overwrite it, which is what keeps a skipped
file's entry alive and preserves the successes of a partially failed run.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "manifest.json"

#: Bumped if the entry shape changes, so a future reader can tell.
SCHEMA_VERSION = 1


def manifest_path(root: Path) -> Path:
    return Path(root) / MANIFEST_NAME


def key_for(root: Path, path: Path) -> str:
    """A stable, portable key: the path relative to the output root."""
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def load(root: Path) -> dict:
    """Read the manifest, returning an empty one if there is none yet."""
    path = manifest_path(root)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt manifest must not stop the tool; it means every file
        # looks unknown and gets re-downloaded, which is safe.
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    data.setdefault("schema_version", SCHEMA_VERSION)
    return data


def save(root: Path, data: dict) -> Path:
    """Write the manifest atomically, so it is never half-written."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = manifest_path(root)
    data = dict(data)
    data["schema_version"] = SCHEMA_VERSION
    data["updated_at"] = _now()
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def entry_for(
    *,
    dataset: str,
    remote_url: str,
    result,
    dialect=None,
) -> dict:
    """Build one manifest entry from a completed download."""
    return {
        "dataset": dataset,
        "source_url": remote_url,
        "size": result.size,
        "size_verified": result.size_verified,
        "sha256": result.sha256,
        "etag": result.etag,
        "last_modified": result.last_modified,
        "atualizado_em": dialect.publication_date if dialect else None,
        "encoding": dialect.encoding if dialect else None,
        "delimiter": dialect.delimiter if dialect else None,
        "header_line": dialect.header_line if dialect else None,
        "downloaded_at": _now(),
    }


def is_unchanged(entry: dict | None, head, dest: Path) -> bool:
    """Whether the local copy can be trusted to match what the portal has.

    Requires a recorded entry, a file of the recorded size actually present,
    and at least one validator that still agrees. A file on disk with no
    entry is treated as unknown - we genuinely do not know what it is.
    """
    if not entry or not Path(dest).exists():
        return False
    if entry.get("size") is not None:
        try:
            if Path(dest).stat().st_size != entry["size"]:
                return False
        except OSError:
            return False

    recorded_etag = entry.get("etag")
    if recorded_etag and head.etag:
        return recorded_etag == head.etag

    recorded_modified = entry.get("last_modified")
    if recorded_modified and head.last_modified:
        return recorded_modified == head.last_modified

    # No validator to compare: do not claim the file is current.
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
