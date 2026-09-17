"""Command line interface.

    python3 -m anac_data fetch    # download the required datasets
    python3 -m anac_data inspect  # report what the downloaded CSVs really are
    python3 -m anac_data package  # chunk oversized files so they fit in git
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import dialect as dialect_mod
from . import discovery, download, listing, manifest, packaging
from . import http as anac_http
from .paths import (
    BASE_URL,
    CHUNK_LIMIT_BYTES,
    DATASETS,
    DATASETS_BY_KEY,
    DEFAULT_DIST_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_VRA_MONTHS,
    OPTIONAL_KEYS,
    REQUIRED_KEYS,
)


@dataclass
class Options:
    """Everything a fetch run needs. Tests build this directly."""

    datasets: tuple[str, ...]
    out: Path
    vra_months: int = DEFAULT_VRA_MONTHS
    force: bool = False
    dry_run: bool = False
    verbose: bool = False
    base_url: str = BASE_URL
    policy: anac_http.Policy = anac_http.DEFAULT_POLICY
    stream: object | None = None
    """Where output goes. Tests pass a StringIO to read it back."""

    def write(self, message: str = "") -> None:
        print(message, file=self.stream or sys.stdout)


@dataclass
class Report:
    """What a run did, for the summary and the exit status."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    planned: list[tuple[str, int | None]] = field(default_factory=list)
    """Dry-run only: the files that would have been fetched."""
    oversized: list[str] = field(default_factory=list)
    """Files too big to commit as they are, so `package` has work to do."""
    required_failed: bool = False

    @property
    def exit_code(self) -> int:
        return 1 if self.required_failed else 0


def human_size(size: int | None) -> str:
    if size is None:
        return "size unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            precision = 0 if unit == "B" else 1
            return f"{value:.{precision}f} {unit}"
        value /= 1024
    return f"{size} B"


def resolve_datasets(
    requested: tuple[str, ...] | None, *, include_optional: bool
) -> tuple[str, ...]:
    """Which dataset keys a run covers.

    Naming a dataset explicitly is its own opt-in; the flag only widens the
    default set.
    """
    if requested:
        unknown = [k for k in requested if k not in DATASETS_BY_KEY]
        if unknown:
            raise SystemExit(
                f"unknown dataset(s): {', '.join(unknown)}\n"
                f"known datasets: {', '.join(DATASETS_BY_KEY)}"
            )
        return tuple(requested)
    if include_optional:
        return REQUIRED_KEYS + OPTIONAL_KEYS
    return REQUIRED_KEYS


def run_fetch(options: Options) -> Report:
    """Discover, download and record. Returns what happened."""
    report = Report()
    catalogue = manifest.load(options.out)
    files = dict(catalogue.get("files", {}))

    for key in options.datasets:
        dataset = DATASETS_BY_KEY[key]
        options.write(f"== {dataset.title} [{key}]")

        try:
            found = discovery.discover(
                dataset,
                vra_months=options.vra_months,
                policy=options.policy,
                base_url=options.base_url,
                report=(
                    (lambda note: options.write(f"   note: {note}"))
                    if options.verbose
                    else None
                ),
            )
        except (listing.PathNotFound, anac_http.HttpError) as error:
            report.failed.append((key, str(error)))
            report.required_failed |= dataset.required
            options.write(f"   FAILED to locate: {error}")
            continue

        if not found:
            message = "no matching file found in the portal listing"
            report.failed.append((key, message))
            report.required_failed |= dataset.required
            options.write(f"   FAILED: {message}")
            continue

        for remote in found:
            _handle_one(remote, dataset, options, files, report)

    if not options.dry_run:
        catalogue["files"] = files
        saved = manifest.save(options.out, catalogue)
        options.write(f"\nmanifest: {saved}")

    _write_summary(options, report)
    return report


def _handle_one(remote, dataset, options: Options, files: dict, report: Report):
    dest = Path(options.out) / dataset.key / remote.name
    try:
        head = anac_http.head(remote.url, policy=options.policy)
    except anac_http.HttpError as error:
        report.failed.append((remote.name, str(error)))
        report.required_failed |= dataset.required
        options.write(f"   FAILED {remote.name}: {error}")
        return

    if options.dry_run:
        report.planned.append((remote.name, head.size))
        options.write(f"   would fetch {remote.name} ({human_size(head.size)})")
        options.write(f"     from {remote.url}")
        options.write(f"     to   {dest}")
        return

    key = _relative_key(options.out, dest)
    if not options.force and manifest.is_unchanged(files.get(key), head, dest):
        report.skipped.append(remote.name)
        options.write(f"   up to date {remote.name} ({human_size(head.size)})")
        return

    if not dataset.required:
        options.write(
            f"   {remote.name} is optional and weighs "
            f"{human_size(head.size)} - starting transfer"
        )
    else:
        options.write(
            f"   downloading {remote.name} ({human_size(head.size)})"
        )

    try:
        result = download.download(
            remote.url,
            dest,
            expected_size=head.size,
            policy=options.policy,
            progress=lambda note: options.write(f"     {note}"),
        )
    except (download.SizeMismatch, anac_http.HttpError, OSError) as error:
        report.failed.append((remote.name, str(error)))
        report.required_failed |= dataset.required
        options.write(f"   FAILED {remote.name}: {error}")
        return

    measured = dialect_mod.detect(result.path)
    files[key] = manifest.entry_for(
        dataset=dataset.key,
        remote_url=remote.url,
        result=result,
        dialect=measured,
    )
    report.downloaded.append(remote.name)
    if packaging.needs_packaging(result.size):
        report.oversized.append(remote.name)
    note = "" if result.size_verified else " (size not advertised, unverified)"
    options.write(
        f"   saved {remote.name} ({human_size(result.size)}){note}"
    )


def _relative_key(root: Path, dest: Path) -> str:
    """Manifest key, computed without requiring the file to exist yet."""
    return Path(dest).relative_to(Path(root)).as_posix()


def _write_summary(options: Options, report: Report) -> None:
    options.write("\n-- summary --")
    if options.dry_run:
        total = sum(s or 0 for _, s in report.planned)
        options.write(f"would download: {len(report.planned)} file(s)")
        if report.planned:
            options.write(f"total size:     {human_size(total)}")
        options.write("nothing was transferred (dry run)")
    else:
        options.write(f"downloaded: {len(report.downloaded)}")
        for name in report.downloaded:
            options.write(f"  + {name}")
        options.write(f"skipped (unchanged): {len(report.skipped)}")
        for name in report.skipped:
            options.write(f"  = {name}")
    if report.failed:
        options.write(f"failed: {len(report.failed)}")
        for name, reason in report.failed:
            options.write(f"  ! {name}: {reason}")
    if report.required_failed:
        options.write("\na required dataset could not be obtained")
    if report.oversized:
        options.write(
            f"\n{len(report.oversized)} file(s) exceed "
            f"{human_size(CHUNK_LIMIT_BYTES)} and cannot be committed as they "
            "are:"
        )
        for name in report.oversized:
            options.write(f"  > {name}")
        options.write("run 'python3 -m anac_data package' to chunk them")


@dataclass
class PackageOptions:
    """Everything a packaging run needs. Tests build this directly."""

    datasets: tuple[str, ...]
    out: Path
    """Where the raw downloads are, i.e. what `fetch --out` wrote."""
    dist: Path
    """Where the chunk sets go."""
    chunk_size: int = CHUNK_LIMIT_BYTES
    force: bool = False
    dry_run: bool = False
    stream: object | None = None

    def write(self, message: str = "") -> None:
        print(message, file=self.stream or sys.stdout)


@dataclass
class PackageReport:
    packaged: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    not_needed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    planned: list[tuple[str, int]] = field(default_factory=list)
    chunk_count: int = 0
    chunk_bytes: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


def run_package(options: PackageOptions) -> PackageReport:
    """Chunk every requested dataset whose file is over the limit."""
    report = PackageReport()
    catalogue = manifest.load(options.out).get("files", {})

    for key in options.datasets:
        dataset = DATASETS_BY_KEY[key]
        options.write(f"== {dataset.title} [{key}]")
        for source, recorded_sha in _sources_for(key, options.out, catalogue):
            _package_one(
                source, recorded_sha, dataset, options, report
            )

    _write_package_summary(options, report)
    return report


def _sources_for(key: str, out: Path, catalogue: dict):
    """The downloaded files for a dataset, newest manifest info first.

    Driven by the download manifest rather than a directory walk, so packaging
    sees exactly what `fetch` recorded - and gets each file's digest for free,
    which is what makes the unchanged check cheap.
    """
    found = []
    for relative, entry in sorted(catalogue.items()):
        if entry.get("dataset") != key:
            continue
        found.append((Path(out) / relative, entry.get("sha256")))
    if not found:
        # Nothing recorded: fall back to the directory so a missing download
        # is reported against a concrete path rather than silently skipped.
        directory = Path(out) / key
        for path in sorted(directory.glob("*.csv")):
            found.append((path, None))
    return found or [(Path(out) / key, None)]


def _package_one(source, recorded_sha, dataset, options, report):
    dist_dir = packaging.chunk_dir_for(
        options.dist, dataset.key, Path(source).name
    )
    label = f"{dataset.key}/{Path(source).name}"

    if options.dry_run:
        if not Path(source).is_file():
            report.failed.append((label, f"{source} is not present"))
            options.write(f"   FAILED {label}: not downloaded yet")
            return
        size = Path(source).stat().st_size
        if not packaging.needs_packaging(size, options.chunk_size):
            report.not_needed.append(label)
            options.write(
                f"   {Path(source).name} is {human_size(size)} - "
                "under the limit, no packaging needed"
            )
            return
        report.planned.append((label, size))
        options.write(
            f"   would package {Path(source).name} ({human_size(size)}) "
            f"into {dist_dir}"
        )
        return

    try:
        result = packaging.package_file(
            source,
            dist_dir,
            dataset=dataset.key,
            chunk_size=options.chunk_size,
            force=options.force,
            source_sha256=recorded_sha,
            progress=lambda note: options.write(f"     {note}"),
        )
    except (packaging.PackagingError, OSError) as error:
        report.failed.append((label, str(error)))
        options.write(f"   FAILED {label}: {error}")
        return

    if result.outcome == "not_needed":
        report.not_needed.append(label)
        options.write(
            f"   {Path(source).name} is "
            f"{human_size(result.original_size)} - under the limit, "
            "no packaging needed"
        )
        return

    report.chunk_count += len(result.chunks)
    report.chunk_bytes += result.total_chunk_bytes
    if result.outcome == "unchanged":
        report.unchanged.append(label)
        options.write(
            f"   {Path(source).name} already packaged and unchanged "
            f"({len(result.chunks)} chunk(s))"
        )
        return

    report.packaged.append(label)
    options.write(
        f"   packaged {Path(source).name} into {len(result.chunks)} chunk(s), "
        f"{human_size(result.total_chunk_bytes)} total"
    )
    _warn_if_trackable(source, options)


def _warn_if_trackable(source, options: PackageOptions) -> None:
    """Point out a packaged original that git would still try to commit.

    Without this, the next `git add -A` would stage a file too big for the
    server and the push would fail a long way from its cause.
    """
    path = Path(source)
    if _git_ignores(path):
        return
    options.write(
        f"     note: {path} is NOT ignored by git, but its committed form is "
        f"its chunk set.\n"
        f"           Add this line to .gitignore:\n"
        f"             {path.as_posix()}"
    )


def _git_ignores(path: Path) -> bool:
    """Whether git would ignore ``path``. False if git cannot say."""
    try:
        done = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=path.parent if path.parent.exists() else None,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # no git here; nothing to warn about
    return done.returncode == 0


def _write_package_summary(options: PackageOptions, report: PackageReport) -> None:
    options.write("\n-- summary --")
    if options.dry_run:
        options.write(f"would package: {len(report.planned)} file(s)")
        for name, size in report.planned:
            options.write(f"  ~ {name} ({human_size(size)})")
        options.write("nothing was written (dry run)")
    else:
        options.write(f"packaged: {len(report.packaged)}")
        for name in report.packaged:
            options.write(f"  + {name}")
        options.write(f"unchanged: {len(report.unchanged)}")
        for name in report.unchanged:
            options.write(f"  = {name}")
    options.write(f"no packaging needed: {len(report.not_needed)}")
    for name in report.not_needed:
        options.write(f"  . {name}")
    if report.chunk_count:
        options.write(
            f"chunks: {report.chunk_count} totalling "
            f"{human_size(report.chunk_bytes)}"
        )
    if report.failed:
        options.write(f"failed: {len(report.failed)}")
        for name, reason in report.failed:
            options.write(f"  ! {name}: {reason}")


def run_inspect(paths, *, out: Path, stream=None) -> int:
    """Report the measured dialect of each CSV."""
    def write(message=""):
        print(message, file=stream or sys.stdout)

    targets = [Path(p) for p in paths] if paths else _downloaded_csvs(out)
    if not targets:
        write(f"no CSV files found under {out}")
        return 1
    for path in targets:
        if not path.is_file():
            write(f"{path}: not found")
            return 1
        write(dialect_mod.describe(path))
        write()
    return 0


def _downloaded_csvs(out: Path):
    root = Path(out)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.csv") if p.is_file())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anac_data",
        description="Download ANAC open-data files for the course datasets.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    known = ", ".join(f"{d.key} ({d.title})" for d in DATASETS)
    fetch = subcommands.add_parser(
        "fetch", help="download datasets from the ANAC open-data portal"
    )
    fetch.add_argument(
        "--datasets",
        help=f"comma-separated subset to fetch. Known: {known}",
    )
    fetch.add_argument(
        "--vra-months",
        type=int,
        default=DEFAULT_VRA_MONTHS,
        help="how many of the most recent VRA months to take "
        f"(default: {DEFAULT_VRA_MONTHS})",
    )
    fetch.add_argument(
        "--include-optional",
        action="store_true",
        help="also fetch the optional statistical dataset (large)",
    )
    fetch.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_ROOT,
        help=f"destination root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    fetch.add_argument(
        "--force",
        action="store_true",
        help="re-download even if the recorded validators still match",
    )
    fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be downloaded without transferring anything",
    )
    fetch.add_argument(
        "-v", "--verbose", action="store_true", help="report discovery details"
    )
    fetch.add_argument(
        "--base-url",
        default=BASE_URL,
        help="portal root to crawl (for mirrors and testing)",
    )
    fetch.add_argument(
        "--timeout", type=float, default=anac_http.DEFAULT_POLICY.timeout
    )
    fetch.add_argument(
        "--attempts", type=int, default=anac_http.DEFAULT_POLICY.attempts
    )

    inspect = subcommands.add_parser(
        "inspect", help="report the real delimiter, encoding and header line"
    )
    inspect.add_argument("paths", nargs="*", help="CSV files (default: all downloaded)")
    inspect.add_argument("--out", default=DEFAULT_OUTPUT_ROOT)

    package = subcommands.add_parser(
        "package",
        help="split oversized downloads into chunks that fit the git host",
    )
    package.add_argument(
        "--datasets",
        help=f"comma-separated subset to package. Known: {known}",
    )
    package.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_LIMIT_BYTES,
        help="maximum bytes per chunk, and the size above which a file gets "
        f"chunked at all (default: {CHUNK_LIMIT_BYTES})",
    )
    package.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_ROOT,
        help=f"where the raw downloads are (default: {DEFAULT_OUTPUT_ROOT})",
    )
    package.add_argument(
        "--dist",
        default=DEFAULT_DIST_ROOT,
        help=f"where chunk sets are written (default: {DEFAULT_DIST_ROOT})",
    )
    package.add_argument(
        "--force",
        action="store_true",
        help="repackage even if the chunk set already matches the source",
    )
    package.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be packaged without writing anything",
    )
    return parser


def _requested_keys(raw):
    if not raw:
        return None
    return tuple(k.strip() for k in raw.split(",") if k.strip())


def main(argv=None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "inspect":
        return run_inspect(args.paths, out=Path(args.out))

    if args.command == "package":
        if args.chunk_size < 1024:
            raise SystemExit("--chunk-size must be at least 1024 bytes")
        return run_package(
            PackageOptions(
                datasets=resolve_datasets(
                    _requested_keys(args.datasets), include_optional=True
                ),
                out=Path(args.out),
                dist=Path(args.dist),
                chunk_size=args.chunk_size,
                force=args.force,
                dry_run=args.dry_run,
            )
        ).exit_code

    requested = _requested_keys(args.datasets)
    options = Options(
        datasets=resolve_datasets(
            requested, include_optional=args.include_optional
        ),
        out=Path(args.out),
        vra_months=args.vra_months,
        force=args.force,
        dry_run=args.dry_run,
        verbose=args.verbose,
        base_url=args.base_url,
        policy=anac_http.Policy(
            timeout=args.timeout, attempts=args.attempts
        ),
    )
    if options.vra_months < 1:
        raise SystemExit("--vra-months must be at least 1")
    return run_fetch(options).exit_code
