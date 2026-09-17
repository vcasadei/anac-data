"""Deciding which remote files a dataset means, by reading the portal.

No filename is ever constructed here. Every file the tool downloads came from
a listing, which is what lets the tool survive ANAC renaming things.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import http as anac_http
from . import listing
from .paths import BASE_URL, Dataset

_YEAR = re.compile(r"^(\d{4})$")
_LEADING_MONTH = re.compile(r"^(\d{1,2})\b")


@dataclass(frozen=True)
class RemoteFile:
    """A file on the portal that we intend to download."""

    dataset: str
    name: str
    url: str
    required: bool
    year: int | None = None
    month: int | None = None

    @property
    def label(self) -> str:
        if self.year and self.month:
            return f"{self.dataset} {self.year}-{self.month:02d} ({self.name})"
        return f"{self.dataset} ({self.name})"


def matches(name: str, dataset: Dataset) -> bool:
    """Apply the dataset's filename filter from the navigation table."""
    lowered = name.lower()
    if dataset.suffixes and not lowered.endswith(
        tuple(s.lower() for s in dataset.suffixes)
    ):
        return False
    if dataset.name_contains and dataset.name_contains.lower() not in lowered:
        return False
    for excluded in dataset.name_excludes:
        if excluded.lower() in lowered:
            return False
    return True


def files_in(entries, dataset: Dataset):
    """The listing's file entries that this dataset wants."""
    return [e for e in entries if not e.is_dir and matches(e.name, dataset)]


def _month_number(folder_name: str) -> int | None:
    """``"07 - Julho"`` -> ``7``.

    Read from the leading digits rather than the Portuguese month name, which
    avoids a lookup table and the accent in ``Março``.
    """
    found = _LEADING_MONTH.match(folder_name.strip())
    if not found:
        return None
    month = int(found.group(1))
    return month if 1 <= month <= 12 else None


def discover(
    dataset: Dataset,
    *,
    vra_months: int,
    policy=anac_http.DEFAULT_POLICY,
    base_url: str = BASE_URL,
    report=None,
) -> list[RemoteFile]:
    """Resolve ``dataset`` to the concrete files to download.

    ``report`` receives human-readable notes about what discovery saw, such as
    a month folder that exists but holds nothing.
    """
    folder_url = listing.resolve_path(base_url, dataset.segments, policy=policy)
    if dataset.monthly:
        return select_monthly(
            dataset, folder_url, vra_months, policy=policy, report=report
        )
    entries = listing.list_dir(folder_url, policy=policy)
    return [
        RemoteFile(
            dataset=dataset.key,
            name=entry.name,
            url=entry.url(folder_url),
            required=dataset.required,
        )
        for entry in files_in(entries, dataset)
    ]


def select_monthly(
    dataset: Dataset,
    folder_url: str,
    wanted: int,
    *,
    policy=anac_http.DEFAULT_POLICY,
    report=None,
) -> list[RemoteFile]:
    """Take the ``wanted`` most recent months that actually hold a file.

    ``folder_url`` is the dataset's folder of year folders, already resolved.

    Year folders are walked newest-first and months within them likewise, so
    running out of populated months in the latest year simply continues into
    the previous one. Emptiness is decided by listing a month folder, never by
    its name or modification date - the portal pre-creates empty folders for
    the rest of the calendar year.
    """
    selected: list[RemoteFile] = []
    year_entries = listing.list_dir(folder_url, policy=policy)
    years = sorted(
        (
            (int(_YEAR.match(e.name).group(1)), e)
            for e in year_entries
            if e.is_dir and _YEAR.match(e.name)
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    for year, year_entry in years:
        if len(selected) >= wanted:
            break
        year_url = year_entry.url(folder_url)
        month_entries = listing.list_dir(year_url, policy=policy)
        months = sorted(
            (
                (_month_number(e.name), e)
                for e in month_entries
                if e.is_dir and _month_number(e.name)
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        for month, month_entry in months:
            if len(selected) >= wanted:
                break
            month_url = month_entry.url(year_url)
            found = files_in(
                listing.list_dir(month_url, policy=policy), dataset
            )
            if not found:
                if report:
                    report(
                        f"{year}-{month:02d}: folder {month_entry.name!r} "
                        "exists but holds no matching file - skipping"
                    )
                continue
            for entry in found:
                selected.append(
                    RemoteFile(
                        dataset=dataset.key,
                        name=entry.name,
                        url=entry.url(month_url),
                        required=dataset.required,
                        year=year,
                        month=month,
                    )
                )
    return selected[:wanted]
