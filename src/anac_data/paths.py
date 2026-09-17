"""The only place portal paths are written down.

Everything the crawler knows about where files live on
``sistemas.anac.gov.br`` is in ``DATASETS`` below. If ANAC reorganizes the
portal, this table is the single file to edit - no other module contains a
portal path string.
"""

from dataclasses import dataclass, field

BASE_URL = "https://sistemas.anac.gov.br/dadosabertos/"


@dataclass(frozen=True)
class Dataset:
    """A dataset's location on the portal and which files to take from it."""

    key: str
    """Short name used on the command line and as the local subdirectory."""

    title: str
    """Human-readable name for messages."""

    segments: tuple[str, ...]
    """Folder names to walk from ``BASE_URL``, in order."""

    required: bool
    """Required datasets are fetched by default; optional ones need opt-in."""

    monthly: bool = False
    """If true, ``segments`` ends at a folder of year folders, each holding
    month folders. Files are selected per month rather than taken wholesale."""

    suffixes: tuple[str, ...] = (".csv",)
    """Keep only files whose name ends with one of these (case-insensitive).
    Excludes the ``.json`` twins and the ``.xls`` copy."""

    name_contains: str = ""
    """If set, keep only files whose name contains this (case-insensitive)."""

    name_excludes: tuple[str, ...] = ()
    """Drop files whose name contains any of these (case-insensitive)."""

    note: str = ""
    """Shown when the dataset is selected, e.g. to warn about size."""


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        key="vra",
        title="Voo Regular Ativo (VRA)",
        segments=("Voos e operações aéreas", "Voo Regular Ativo (VRA)"),
        required=True,
        monthly=True,
        name_contains="VRA",
        note="~12 MB per month",
    ),
    Dataset(
        key="aerodromos",
        title="Lista de aeródromos públicos",
        segments=(
            "Aerodromos",
            "Aeródromos Públicos",
            "Lista de aeródromos públicos",
        ),
        required=True,
        note="~100 KB",
    ),
    Dataset(
        key="estatisticos",
        title="Dados Estatísticos do Transporte Aéreo",
        segments=(
            "Voos e operações aéreas",
            "Dados Estatísticos do Transporte Aéreo",
        ),
        required=False,
        # Dados_Estatisticos_parte.csv is a stale 2023 partial extract; the
        # full file is the one worth having.
        name_excludes=("_parte",),
        note="single file covering 2000-present, roughly 350 MB",
    ),
)

DATASETS_BY_KEY = {d.key: d for d in DATASETS}

REQUIRED_KEYS = tuple(d.key for d in DATASETS if d.required)
OPTIONAL_KEYS = tuple(d.key for d in DATASETS if not d.required)

DEFAULT_OUTPUT_ROOT = "data/raw/anac"
DEFAULT_VRA_MONTHS = 3

#: Where committable chunk sets are written, beside the raw download tree.
DEFAULT_DIST_ROOT = "data/dist"

#: A file larger than this cannot be committed to the repository host, so it is
#: stored as a set of chunks this size instead. The original host was a self-hosted
#: GitLab whose per-file limit is whatever the administrator configured;
#: 45 MiB stays clear of a 50 MB cap without needing to know. Override with
#: `package --chunk-size` if a push is ever rejected.
CHUNK_LIMIT_BYTES = 45 * 1024 * 1024

#: Chunk indices are zero-padded to this width so sorting names as text equals
#: concatenation order - the property both rebuild scripts rely on.
CHUNK_INDEX_WIDTH = 3

#: Compression settings pinned so repackaging identical bytes yields identical
#: chunks. gzip embeds the current time by default, which would make every run
#: produce new git objects for unchanged data.
GZIP_COMPRESS_LEVEL = 6
GZIP_MTIME = 0
