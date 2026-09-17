"""Measuring what a downloaded CSV actually is.

Every value here comes from the file's bytes. The ANAC files disagree with
each other - VRA is UTF-8 with a BOM, the aerodrome list is Latin-1 - and all
of them put an ``Atualizado em:`` line above the real header, so guessing
from the dataset name produces wrong answers.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

SAMPLE_BYTES = 64 * 1024

#: Tried in order. UTF-8 must come first: Latin-1 decodes *any* byte sequence
#: without raising, so trying it first would mislabel every UTF-8 file.
ENCODING_CANDIDATES = ("utf-8", "latin-1")

DELIMITER_CANDIDATES = (";", ",", "\t", "|")

UNDETERMINED = "undetermined"

_UPDATED = re.compile(r"Atualizado\s+em\s*:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_METADATA_LINE = re.compile(r"^\s*Atualizado\s+em\s*:", re.IGNORECASE)

#: Control characters that never appear in these CSVs. Their presence means we
#: are looking at something that is not text in the encoding we tried.
_CONTROL = {
    chr(c) for c in list(range(0, 32)) + [127] if chr(c) not in "\t\r\n"
}
_MAX_CONTROL_RATIO = 0.01


@dataclass(frozen=True)
class Dialect:
    """What a CSV file turned out to be."""

    encoding: str
    """A codec name, or ``"undetermined"``."""
    has_bom: bool
    delimiter: str | None
    """``None`` when no candidate delimiter appears at all."""
    header_line: int | None
    """1-based line number of the column headers."""
    publication_date: str | None
    """The date from the file's own ``Atualizado em:`` line, if it has one."""
    columns: tuple[str, ...] = ()

    @property
    def encoding_label(self) -> str:
        """How to describe the encoding to a person."""
        if self.encoding == UNDETERMINED:
            return UNDETERMINED
        if self.encoding == "utf-8":
            return "UTF-8 with BOM" if self.has_bom else "UTF-8"
        if self.encoding == "latin-1":
            return "Latin-1 (ISO-8859-1)"
        return self.encoding


def _decode(sample: bytes, encoding: str) -> str | None:
    """Decode a possibly truncated sample, or return ``None`` if it will not.

    An incremental decoder is used because a 64 KB cut can land in the middle
    of a multi-byte character, which a plain ``decode`` would call invalid.
    """
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    try:
        return decoder.decode(sample, False)
    except UnicodeDecodeError:
        return None


def _is_plausible_text(text: str) -> bool:
    if not text:
        return False
    controls = sum(1 for ch in text if ch in _CONTROL)
    return controls / len(text) <= _MAX_CONTROL_RATIO


def detect_bytes(sample: bytes) -> Dialect:
    """Determine the dialect of a CSV from its opening bytes."""
    has_bom = sample.startswith(codecs.BOM_UTF8)
    body = sample[len(codecs.BOM_UTF8) :] if has_bom else sample

    encoding = UNDETERMINED
    text = ""
    for candidate in ENCODING_CANDIDATES:
        decoded = _decode(body, candidate)
        if decoded is not None and _is_plausible_text(decoded):
            encoding, text = candidate, decoded
            break

    if encoding == UNDETERMINED:
        return Dialect(
            encoding=UNDETERMINED,
            has_bom=has_bom,
            delimiter=None,
            header_line=None,
            publication_date=None,
        )

    lines = text.splitlines()
    # A sample that stops mid-line leaves a fragment we should not read as a
    # row. One that stops on a line break does not - dropping a complete line
    # there would throw away the header of a short file.
    if len(lines) > 1 and not text.endswith(("\n", "\r")):
        lines = lines[:-1]

    delimiter = _pick_delimiter(lines)
    publication_date = _publication_date(lines)
    header_line, columns = _find_header(lines, delimiter)

    return Dialect(
        encoding=encoding,
        has_bom=has_bom,
        delimiter=delimiter,
        header_line=header_line,
        publication_date=publication_date,
        columns=columns,
    )


def detect(path) -> Dialect:
    """Determine the dialect of a CSV on disk."""
    with open(path, "rb") as handle:
        return detect_bytes(handle.read(SAMPLE_BYTES))


def _pick_delimiter(lines) -> str | None:
    """The candidate that appears most often across the sampled lines."""
    window = "\n".join(lines[:20])
    counts = {c: window.count(c) for c in DELIMITER_CANDIDATES}
    best = max(counts, key=lambda c: counts[c])
    return best if counts[best] else None


def _publication_date(lines) -> str | None:
    for line in lines[:5]:
        found = _UPDATED.search(line)
        if found:
            return found.group(1)
    return None


def _find_header(lines, delimiter):
    """The first line that is neither metadata nor a single field.

    Detected by rule rather than assumed to be line 2, so a file that drops
    the metadata line is still read correctly.
    """
    if delimiter is None:
        return None, ()
    for index, line in enumerate(lines, start=1):
        if _METADATA_LINE.match(line):
            continue
        if not line.strip():
            continue
        fields = next(
            csv.reader(io.StringIO(line), delimiter=delimiter), []
        )
        if len(fields) > 1:
            return index, tuple(f.strip() for f in fields)
    return None, ()


def describe(path, dialect: Dialect | None = None) -> str:
    """One-line human-readable summary, used by the ``inspect`` command."""
    dialect = dialect or detect(path)
    name = Path(path).name
    delimiter = (
        repr(dialect.delimiter) if dialect.delimiter else UNDETERMINED
    )
    header = (
        f"line {dialect.header_line}"
        if dialect.header_line
        else UNDETERMINED
    )
    published = dialect.publication_date or "not stated"
    return (
        f"{name}\n"
        f"  encoding:    {dialect.encoding_label}\n"
        f"  delimiter:   {delimiter}\n"
        f"  header:      {header}\n"
        f"  atualizado:  {published}\n"
        f"  columns:     {len(dialect.columns)}"
    )
