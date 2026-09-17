# Design

See `proposal.md` for motivation and `specs/data-packaging/spec.md` for the behavior contract.

## Context

The downloader from the archived `add-anac-data-downloader` change is in place and working: `src/anac_data/` is a stdlib-only package with `fetch` and `inspect` subcommands wired in `cli.py` (`_parser()` builds them around `subcommands.add_parser`, and `main()` dispatches on `args.command`). Datasets are declared in one table in `paths.py` as frozen `Dataset` records; `DEFAULT_OUTPUT_ROOT` is `data/raw/anac`. Downloads are recorded in `data/raw/anac/manifest.json`.

Facts measured for this change:

- **`Dados_Estatisticos.csv` is 358,893,852 bytes (342.3 MiB)** as of 2026-09-17, `ETag "6aab68a1-1564491c"`. It is republished frequently - its `Last-Modified` moved to the same day it was inspected.
- **ANAC CSV compresses well, but the ratio is per-file.** `gzip -6` took a 12,691,900-byte VRA file to 1,354,065 bytes (9.4x). Extrapolating that to the statistical file predicted ~38 MiB and a single chunk. Measured after implementation, the real figure is **358,893,852 -> 47,488,050 bytes, 13.2% (7.6x)** - 45.3 MiB, which lands just *over* the 45 MiB chunk size and yields **two chunks** (47,185,920 + 302,130). The prediction was optimistic because the two files differ in content, not because compression misbehaved. It also shows the file sits close to the boundary, so a modest growth changes the chunk count - which is exactly why the count is never assumed anywhere.
- **Default gzip is not reproducible.** Python's `gzip` writes the current time into the header, so two runs over identical input produced different digests. Pinning `mtime=0` produced byte-identical output that still round-trips.
- **The host was a self-hosted GitLab instance**, so the per-file limit is whatever the administrator configured. 45 MiB was chosen to be safe under a 50 MB limit without needing to know.
- `README.md` is still the 93-line GitLab boilerplate, and `.gitignore` carries a comment pointing at a change directory that has since moved into `openspec/changes/archive/`.

## Goals / Non-Goals

**Goals:**

- A clone of this repository plus one script run yields the full dataset, on Linux, macOS or Windows, with no Python and no network.
- Re-running `package` over unchanged data touches no files and adds nothing to git history.
- Corruption is caught before it can be mistaken for data.

**Non-Goals:**

- No Git LFS. The whole point is to fit the plain per-file limit; introducing LFS would be a different change (and is noted in the proposal as the alternative if the intent was to commit the file whole).
- No parallel or resumable packaging. It is a local CPU-bound pass over a file we already have; re-running it from scratch costs seconds.
- No generic archive format (zip, tar, 7z). A single gzip stream split by bytes is the smallest thing that meets the requirement.
- No change to how anything is downloaded. `package` reads the raw tree and writes elsewhere.
- The rebuild scripts do not download anything. If a chunk set is missing they say so; fetching is `fetch`'s job.

## Decisions

### `gzip` then byte-split, not a line-aware CSV split

The compressed stream is cut at fixed byte offsets. Parts are meaningless individually and only mean something concatenated in order.

*Why:* compression is worth roughly 9x here, which is the difference between ~38 MiB and ~342 MiB of permanent git history. Once the bytes are compressed, a line-aware split is impossible anyway - there are no line boundaries in a gzip stream. Byte-splitting is also the one operation both `cat` and PowerShell do natively and identically.

*Alternative rejected:* splitting into independently-valid CSVs with repeated headers. Each part would open directly in pandas, which is genuinely nice, but it forfeits compression entirely and makes the rebuild scripts parse and skip headers - more logic in the two places hardest to test.

### Pin the gzip timestamp to 0

`gzip.GzipFile(..., mtime=0)` with a fixed `compresslevel`.

*Why:* verified above - without it, every `package` run emits different bytes for identical input, so git stores another ~38 MiB blob each time and the "unchanged since last run" report becomes impossible. This single argument is what makes the idempotence requirement achievable.

*Consequence:* the chunk digests become a pure function of the source bytes, which is also what lets `package` decide "unchanged" by comparing digests rather than timestamps.

### Two metadata files, deliberately redundant

`MANIFEST.json` carries everything: original name/size/digest, compressed size/digest, chunk size, and per-chunk name/size/digest. `checksums.sha256` carries just `<digest>  <chunkname>` lines.

*Why:* POSIX shell has no JSON parser, and requiring `jq` would break "standalone on a stock system". `sha256sum -c checksums.sha256` verifies every chunk in one command with zero parsing. The JSON stays for the Python side, for humans, and for the fields the plain listing cannot express (the original file's digest, which is what the final verification needs).

*How the shell script gets the two scalars it still needs* (original filename and its digest): they are written as `# key: value` comment lines at the top of `checksums.sha256`. `sha256sum -c` ignores comment lines, and the script reads them with `sed`. This keeps one file authoritative for the shell path instead of teaching it to parse JSON.

*Alternative rejected:* JSON only, parsed with `grep`/`sed`. Fragile in exactly the way that bites silently.

### One chunk directory per file, not per dataset

`data/dist/<dataset>/<original-filename>/` holds that one file's chunks, `MANIFEST.json` and `checksums.sha256`.

*Why:* a dataset can hold several files - VRA has one per month - and the metadata describes exactly one. Sharing a directory across a dataset would leave a single `MANIFEST.json` describing only the last file packaged, and stale-chunk removal (which deletes chunks the current manifest does not list) would delete the *other* files' chunks. Found while implementing, by a test that packaged three VRA months.

*Bonus:* "one directory equals one rebuildable file" is exactly the contract the rebuild scripts want - point either script at a directory and it has everything it needs. "Rebuild everything" is then "every directory under `data/dist/` containing a `MANIFEST.json`".

### Chunk naming: `<original>.gz.partNNN`

Three-digit, 1-based, zero-padded: `Dados_Estatisticos.csv.gz.part001`.

*Why:* zero-padding is what makes "sort the names as text" equal "concatenate in the right order", which is the property both scripts rely on (`cat *.part*` in shell, `Sort-Object Name` in PowerShell). Three digits covers 999 chunks - about 44 GiB at this chunk size, far beyond anything this project will hold. Keeping `.gz` in the name documents what the concatenation produces.

*Guard:* if a file ever needs more than 999 chunks, packaging fails loudly rather than emitting `part1000` and silently mis-sorting.

### Threshold lives in `paths.py`, packaging in a new module

A `CHUNK_LIMIT_BYTES = 45 * 1024 * 1024` constant joins the existing table in `paths.py`; the logic goes in a new `src/anac_data/packaging.py`; `cli.py` gains a `package` subcommand beside `fetch` and `inspect`.

*Why:* `paths.py` is already established as the single place configuration lives, and the download modules stay untouched. Packaging is a distinct concern with no overlap with discovery or HTTP.

*Threshold applies per file, from the download manifest* - `package` reads `data/raw/anac/manifest.json` to learn what exists and how big it is, rather than walking the tree blindly. That also gives it the source digest for free.

### `package` is its own command, run after `fetch`

Not automatic. `fetch` ends by printing a hint when it has stored a file over the limit.

*Why:* packaging rewrites tracked files, which is a git-visible act; doing it as a side effect of a download would surprise. Keeping it separate also makes it re-runnable and testable on its own.

### `.gitignore` inverts to an allow-list with one exclusion

```
!data/
data/raw/anac/estatisticos/
*.part
*.tmp
```

The oversized dataset's raw directory is named explicitly rather than matched by a size rule, because git cannot ignore by size.

*Why:* this is the honest expression of "all data in the repo except the thing that does not fit". It is one line per oversized dataset, and there is exactly one today.

*Safety net:* `package` checks whether the source file it just chunked is actually ignored by git, and if not, prints the exact line to add. Without that check the very first `git add -A` after a package run would try to commit a 342 MiB file and be rejected by the server - a confusing failure a long way from its cause. The tool reports; it does not edit `.gitignore` itself.

### Rebuild scripts take a directory, default to the known one

```
scripts/rebuild.sh  [chunk-dir] [-o output-dir]
scripts/rebuild.ps1 [-ChunkDir <path>] [-OutputDir <path>]
```

With no arguments they rebuild every chunk set under `data/dist/`. Given a directory they rebuild just that one, which is what makes them work when copied elsewhere.

*Shell portability:* `sha256sum` on Linux, falling back to `shasum -a 256` on macOS, detected once; `gunzip -c` for decompression; `cat` for concatenation. No bashisms, so `/bin/sh` is enough.

*PowerShell:* `System.IO.Compression.GzipStream` for decompression, so no `gunzip` and no install. Concatenation feeds the parts through one stream rather than `Get-Content`, which would mangle bytes and read everything first.

Digests use `[System.Security.Cryptography.SHA256]` directly, **not** `Get-FileHash`. This corrects an assumption made before implementation: `Get-FileHash` turned out to be missing on this machine's PowerShell **5.1** (`5.1.26100.9444`), where it should exist, so it cannot be relied on by version. The .NET types ship with the framework every PowerShell runs on, which makes it one code path rather than a version branch. Verified working on both 5.1 and pwsh 7.6.

*Strict mode:* the script runs under `Set-StrictMode -Version 2.0`, which makes `.Count` on a non-array an error. Anything counted is forced into an array with `@(...)` - a single `Get-ChildItem` result would otherwise be a bare object and fail only in the one-chunk-set case.

*Write to a temporary name, then move* into place, so a failed or interrupted rebuild cannot leave something that looks like a finished file - the same discipline the downloader already uses for `.part` files.

## Risks / Trade-offs

- **Committed data grows history permanently** → stated in the proposal as an accepted consequence of the request. Compression keeps the large file's contribution near 38 MiB, and deterministic chunking means an unchanged file never adds a second copy. The residual risk is real: each newly published VRA month or ANAC re-release adds its own blob forever.
- **The statistical file is republished often** (its `Last-Modified` was the same day it was inspected) → each refresh that is downloaded and packaged adds a fresh ~38 MiB to history. Documented in the README so it is a decision, not a surprise; `package` reporting "unchanged" makes the no-op case free.
- **A 45 MiB guess against an unknown server limit** → if a push is rejected, the chunk size is one flag (`--chunk-size`) and one repackage away, with no format change. The value lives in `paths.py` next to the other configuration.
- **Two rebuild implementations can drift** → a test rebuilds the same fixture with both scripts and asserts identical digests, so drift fails the suite rather than being discovered on someone's laptop. This machine can run both: `powershell.exe` and `pwsh.exe` are reachable from WSL through interop, so the PowerShell path is genuinely exercised rather than assumed. The test translates paths with `wslpath` when invoking the Windows interpreter, and skips with a clear message on a host where no PowerShell is found.
- **Byte-split parts are opaque** → a corrupt or missing part cannot be diagnosed by looking at it, which is exactly why every part carries a digest and the rebuild checks each one before use.
- **`sha256sum` over ~38 MiB plus gzip decompression of 342 MiB** takes a few seconds and the rebuild needs ~380 MiB of free disk for the intermediate plus the result → the script states what it is doing and fails clearly on a short write rather than producing a truncated CSV.

## Migration Plan

Additive except for two intentional rewrites: `.gitignore` inverts, and `README.md` replaces the GitLab template. Both are single files and recoverable from git history.

Order matters on first use: `fetch --include-optional` (downloads ~342 MiB), then `package` (writes `data/dist/`), then commit. Rollback is deleting `data/dist/`, `scripts/`, `src/anac_data/packaging.py` and restoring `.gitignore`; no external state is touched. Anyone who already cloned an earlier commit is unaffected, since nothing existing changes meaning.

## Open Questions

- Whether under-limit files should also be gzipped in the repository. Deferrable: it would shrink tracked VRA data about 9x but make the committed files non-obvious to open, and it changes only which files the existing packaging path is pointed at - not the chunk format, the metadata, or the scripts.
