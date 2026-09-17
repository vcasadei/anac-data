# Proposal

## Why

The project's optional dataset, `Dados_Estatisticos.csv`, is 358,893,852 bytes (342.3 MiB) and cannot be pushed to the original self-hosted GitLab, which caps per-file size. The whole dataset should live in the repository so a student can clone it and work offline, but today `data/` is git-ignored entirely and the one file worth committing is the one file that will not fit. We need a way to store oversized files in the repo as size-limited pieces and to put them back together on any machine - including one with no Python installed.

## What Changes

- Add an `anac_data package` command that turns any downloaded file over the size limit into a committable chunk set: gzip it, then byte-split the compressed stream into parts of at most 45 MiB, written to `data/dist/<dataset>/`.
- Compress with a **pinned gzip timestamp** so re-packaging unchanged data produces byte-identical parts. Without this, gzip embeds the current time and every run would create new git objects for data that did not change.
- Write two integrity files next to the parts: `MANIFEST.json` (rich metadata for the tool) and `checksums.sha256` (plain `sha256sum`-compatible lines, so the shell script needs no JSON parser).
- Add `scripts/rebuild.sh` (POSIX shell) and `scripts/rebuild.ps1` (PowerShell) that reassemble, decompress and verify the original file. **Both are standalone**: no Python, no `anac_data`, no network. PowerShell decompresses via .NET `GzipStream` since Windows has no `gunzip`.
- Verify on rebuild: each part's digest before concatenating, and the final file's digest after decompressing. A corrupt or missing part is reported and the rebuild fails rather than silently producing a truncated CSV.
- **Invert `.gitignore`**: track all data under `data/` instead of ignoring it. VRA (~12 MB each) and the aerodrome list (~100 KB) are committed as plain CSVs; only the oversized original stays untracked, because its committed form is its chunk set.
- Fetch the optional dataset for real, which the previous change deliberately left off by default.
- Replace the GitLab template `README.md` with real documentation: what the project is, what it downloads and why, how to run `fetch`/`inspect`/`package`, how to rebuild from chunks on Linux/macOS and Windows, the repository layout, and the CSV encoding and header-line quirks that ingestion has to handle.
- Fix the stale `.gitignore` comment that points at a change directory now moved to `openspec/changes/archive/`.

### Assumption recorded

The request asks for all data in the repo *and* for oversized files to be split. Taken literally these conflict: the 342 MiB original is precisely what cannot be committed. This plan resolves it as - **every data file is represented in the repository; for a file over the limit, that representation is its chunk set.** The reassembled original is treated as a build product: reproducible from the parts, never committed. If the intent was instead to commit the full file via Git LFS, that is a different change and this one should be revised first.

A second consequence worth stating plainly: committing data makes the repository grow permanently, since git keeps every version. Three VRA months plus the chunk set is roughly 73 MiB, and each future month or re-publication adds its own copy to history forever. Under-limit files are committed uncompressed, as chosen, even though they would shrink about 9x if gzipped.

## Capabilities

### New Capabilities
- `data-packaging`: Turning oversized data files into size-limited, integrity-checked pieces that fit a repository host's per-file limit, and rebuilding the originals from those pieces without the project's own tooling.

### Modified Capabilities

None. `anac-data-download` continues to download and catalogue into `data/raw/anac/<dataset>/` exactly as specified; packaging reads what it produced and writes elsewhere. No existing requirement changes.

## Impact

- **New code**: `src/anac_data/packaging.py` plus a `package` subcommand in the existing CLI, and a size threshold in the `paths.py` navigation table. No change to the download, discovery, manifest or dialect modules.
- **New scripts**: `scripts/rebuild.sh`, `scripts/rebuild.ps1` - deliberately outside the Python package so they survive being copied on their own.
- **Dependencies**: still standard library only (`gzip`, `hashlib`, `json`). The rebuild scripts use only shell builtins plus `gunzip`/`sha256sum` (with a `shasum` fallback for macOS) and, on Windows, .NET types already present in PowerShell 5.1.
- **Repository**: `.gitignore` inverts from "ignore all data" to "track all data except oversized originals". Roughly 73 MiB of tracked data on first commit.
- **Filesystem**: adds `data/dist/<dataset>/`; `data/raw/anac/` is unchanged.
- **Docs**: `README.md` is rewritten from the GitLab boilerplate.
- **Network**: one additional ~342 MiB download when the operator opts in. No new hosts.
