# Tasks

Tests extend the existing stdlib `unittest` suite (`python3 -m unittest discover tests -v`, currently 107 passing) — there is no `pip` on this machine. Packaging and rebuild tests run against small synthetic fixtures, not the 342 MiB file; only group 6 touches the real download. `powershell.exe`/`pwsh.exe` are reachable from WSL, so the PowerShell script is tested for real.

## 1. Packaging core

- [x] 1.1 Add `CHUNK_LIMIT_BYTES = 45 * 1024 * 1024` and a `DEFAULT_DIST_ROOT = "data/dist"` to `paths.py`; verify `grep -n "45 \* 1024" src/anac_data/*.py` matches only `paths.py`, keeping configuration in the one established place
- [x] 1.2 Create `src/anac_data/packaging.py` with deterministic gzip compression pinned to `mtime=0` and a fixed `compresslevel`; verify a test compresses the same bytes twice and asserts byte-identical output, and a second test asserts the output round-trips back to the source bytes
- [x] 1.3 Implement byte-splitting of the compressed stream into `<original>.gz.partNNN` (3-digit, 1-based, zero-padded); verify tests assert every chunk but the last is exactly the chunk size, the last is a non-empty remainder, and that sorting 12 chunk names as text yields concatenation order
- [x] 1.4 Fail loudly rather than emit a 4-digit index when a stream would need more than 999 chunks; verify a test with a tiny chunk size raises an error naming the limit
- [x] 1.5 Write `MANIFEST.json` (original name/size/sha256, compressed size/sha256, chunk size, per-chunk name/size/sha256) and `checksums.sha256` (`# key: value` comment header plus `<digest>  <chunkname>` lines); verify a test asserts every field is populated and that `sha256sum -c checksums.sha256` exits 0 when run inside the chunk directory
- [x] 1.6 Remove stale chunks left by a previous longer version before writing; verify a test packages a large payload, repackages a smaller one, and asserts only the current chunks remain and the manifest lists exactly those
- [x] 1.7 Skip files at or under the limit without chunking them; verify a test asserts no chunk set is produced for an under-limit file and the result reports it as not needing packaging

## 2. Packaging CLI

- [x] 2.1 Add a `package` subcommand to `cli.py` with `--datasets`, `--chunk-size`, `--out`, `--dist`, `--force` and `--dry-run`, reading sizes and digests from the download manifest rather than walking the tree; verify a test asserts it packages an oversized fixture dataset and leaves an under-limit one alone
- [x] 2.2 Report a missing source file as a failure with non-zero exit and no partial chunk set; verify a test asserts the exit code, the message naming the dataset, and that no chunk directory was created
- [x] 2.3 Detect unchanged sources by digest and skip rewriting; verify a test runs `package` twice and asserts the second run reports every dataset unchanged and leaves chunk file mtimes untouched
- [x] 2.4 Print the packaging summary (packaged / not needed / unchanged / failed, plus chunk count and combined size); verify tests assert exit 0 on a mixed run and non-zero when a requested dataset is absent
- [x] 2.5 Check whether each packaged source file is git-ignored and print the exact `.gitignore` line when it is not; verify a test in a temporary git repo asserts the warning appears for an untracked-but-not-ignored source and is absent once the rule is added
- [x] 2.6 Print a hint at the end of `fetch` when a stored file exceeds the limit, pointing at `package`; verify a test asserts the hint appears for an oversized download and not otherwise

## 3. POSIX rebuild script

- [x] 3.1 Write `scripts/rebuild.sh` as `/bin/sh`-compatible (no bashisms), taking an optional chunk directory and `-o` output directory, defaulting to rebuilding every chunk set under `data/dist/`; verify `sh -n scripts/rebuild.sh` parses and `shellcheck -s sh` reports no errors if available
- [x] 3.2 Detect `sha256sum` with a `shasum -a 256` fallback once at startup, and fail with a clear message if neither exists; verify a test runs the script with a `PATH` containing only one of them and asserts success in both cases
- [x] 3.3 Verify every chunk against `checksums.sha256` before concatenating, then concatenate, `gunzip`, and verify the final digest from the comment header; verify a test rebuilds a fixture and asserts the output is byte-identical to the original
- [x] 3.4 Write to a temporary name and move into place on success only; verify a test corrupts one chunk and asserts the script exits non-zero, names the offending chunk, and leaves no file at the destination path
- [x] 3.5 Report a missing chunk by name and exit non-zero; verify a test deletes a chunk and asserts the message names it
- [x] 3.6 Report a final-digest mismatch distinctly from a chunk failure; verify a test tampers with the recorded original digest and asserts the message distinguishes the two cases
- [x] 3.7 Recognise an already-correct output file and skip rewriting it; verify a test runs the script twice and asserts the second run reports the file already correct and exits 0
- [x] 3.8 Work when copied outside the repository beside a chunk directory; verify a test copies the script and one chunk set into a temporary directory with no repository present and rebuilds successfully

## 4. PowerShell rebuild script

- [x] 4.1 Write `scripts/rebuild.ps1` with `-ChunkDir` and `-OutputDir` parameters mirroring the shell script's defaults, using `Get-FileHash -Algorithm SHA256` and `System.IO.Compression.GzipStream`; verify the script parses under `powershell.exe -NoProfile -Command { $null = [ScriptBlock]::Create((Get-Content -Raw scripts/rebuild.ps1)) }`
- [x] 4.2 Stream concatenation and decompression through `FileStream` rather than `Get-Content`, so bytes are not mangled and memory stays flat; verify a test rebuilds a fixture larger than one chunk and asserts a byte-identical result
- [x] 4.3 Verify each chunk's digest, then the final file's, writing to a temporary name and moving on success; verify tests corrupt a chunk and tamper with the recorded digest, asserting non-zero exit, the naming of the offending chunk, and no file left at the destination
- [x] 4.4 Recognise an already-correct output file and skip rewriting; verify a test asserts a second run reports it already correct and exits 0
- [x] 4.5 Add a test that invokes whichever of `pwsh`/`pwsh.exe`/`powershell.exe` exists, translating paths with `wslpath` for the Windows interpreters, and skips with a clear message when none is found; verify the test runs (not skips) on this machine
- [x] 4.6 Assert the two scripts agree: rebuild the same fixture with the shell and PowerShell scripts and verify both outputs have the same SHA-256

## 5. Repository layout and documentation

- [x] 5.1 Invert `.gitignore` to track all of `data/` except the oversized raw original, `*.part` and `*.tmp`, and fix the stale comment pointing at the now-archived change directory; verify `git check-ignore -v` reports the oversized CSV as ignored and `git status --porcelain` lists the VRA and aerodrome CSVs plus `data/dist/` contents as additions
- [x] 5.2 Rewrite `README.md`: what the project is, the datasets it downloads with their portal paths and which are required versus optional, which months VRA covers and how that is chosen, the repository layout, `fetch`/`inspect`/`package` usage with the `PYTHONPATH=src` invocation, and the per-dataset delimiter, encoding and header-line facts; verify every command shown in the README is executed and produces the documented result
- [x] 5.3 Document rebuilding from chunks for both Linux/macOS and Windows, including the GitLab per-file limit that motivates it and how to change `--chunk-size`; verify a fresh-clone walkthrough of just the README's rebuild steps reproduces the file
- [x] 5.4 Note in the README that committed data grows git history permanently and that the statistical file is republished often; verify the caveat is present and names the approximate per-refresh cost

## 6. End-to-end verification

- [x] 6.1 Run `fetch --include-optional` and confirm `Dados_Estatisticos.csv` lands at its advertised size with a verified digest recorded in the download manifest
- [x] 6.2 Run `package` and confirm `data/dist/estatisticos/` holds the chunk set, `MANIFEST.json` and `checksums.sha256`, that no chunk exceeds 45 MiB, and that the compressed total is close to the ~38 MiB the compression ratio predicts
- [x] 6.3 Re-run `package` and confirm every dataset reports unchanged with no file rewritten
- [x] 6.4 Delete the 342 MiB original, rebuild it with `scripts/rebuild.sh`, and confirm the result matches the digest recorded at packaging time
- [x] 6.5 Delete it again, rebuild with `scripts/rebuild.ps1` via the Windows interpreter, and confirm the same digest
- [x] 6.6 Confirm `git status` shows the chunk set and the under-limit CSVs as additions while the 342 MiB original stays ignored, and that `git add -A && git status --porcelain --cached` stages nothing over 45 MiB; verify with `git diff --cached --numstat` plus a size check rather than by pushing
- [x] 6.7 Run `python3 -m unittest discover tests -v` and confirm the whole suite passes, including the pre-existing 107 tests
