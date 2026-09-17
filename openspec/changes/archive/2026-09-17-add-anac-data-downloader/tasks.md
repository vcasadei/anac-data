# Tasks

Tests use stdlib `unittest` (`python3 -m unittest discover tests -v`) — this machine has no working `pip`, so `pytest` is not available. Network-dependent logic is tested against fixtures and a local `http.server`, never against the live portal; only the tasks in group 7 hit `sistemas.anac.gov.br`.

## 1. Project scaffolding

- [x] 1.1 Create `src/anac_data/` with `__init__.py`, `__main__.py`, and empty `tests/`; verify `python3 -c "import sys; sys.path.insert(0,'src'); import anac_data"` exits 0
- [x] 1.2 Add `data/` and `*.part` to `.gitignore`; verify `git status --porcelain` stays clean after creating `data/raw/anac/vra/probe.csv`
- [x] 1.3 Add `paths.py` holding the portal base URL and one navigation table mapping each dataset to its ordered path segments and filename filter; verify the table is the only place any portal path string appears (`grep -rn "dadosabertos\|Aeródromos\|Voo Regular" src/` hits only this file)

## 2. HTTP layer

- [x] 2.1 Implement `http.py:fetch()` over `urllib.request` with descriptive User-Agent, timeout, and inter-request delay; verify a unit test against a local `http.server` receives the expected body and sends the expected User-Agent header
- [x] 2.2 Add retry with exponential backoff on timeouts and 5xx, and immediate failure on 404; verify tests assert a 503 route is retried the configured number of times and a 404 route is attempted exactly once
- [x] 2.3 Implement `head()` returning size, `ETag` and `Last-Modified`, tolerating a missing `Content-Length`; verify a test covers both a full-header response and one with no `Content-Length`

## 3. Discovery

- [x] 3.1 Implement the `HTMLParser` subclass that extracts `href` entries from an nginx autoindex, discarding `../` and classifying directories by trailing `/`; verify unit tests parse a saved fixture of the real `/dadosabertos/` listing and a real month-folder listing
- [x] 3.2 Implement accent- and case-insensitive segment matching plus `urljoin`-based path walking; verify a test resolves the aerodrome path from fixtures and that a missing segment raises an error naming the segment and listing the entries actually present
- [x] 3.3 Implement VRA month selection: walk years newest-first, keep only month folders containing a matching CSV, parse month number from leading digits, take the N most recent; verify tests cover empty future-month folders being skipped, a year boundary being crossed when the latest year has fewer than N populated months, and `N=2` selecting exactly two
- [x] 3.4 Filter discovery to CSV only, excluding the `.json` twins and stray files like `tmp.txt`; verify a test on a month-folder fixture containing all three returns just the CSV

## 4. Download and integrity

- [x] 4.1 Implement streaming download to a sibling `<name>.part` with chunked SHA-256, then `os.replace` onto the final path; verify a test confirms the final file's digest and that no `.part` file remains
- [x] 4.2 Implement resume: send `Range` when a `.part` exists, append on `206`, and discard-and-restart on a `200` that ignored the range; verify tests against a local server for both the honoured-range and ignored-range cases, each producing a byte-identical file
- [x] 4.3 Verify received size against advertised `Content-Length`, discarding the file and failing on mismatch; verify a test where the server under-delivers leaves no file at the destination path and returns a non-zero result
- [x] 4.4 Handle a missing `Content-Length` by storing the file, recording its digest, and flagging size as unverified; verify a test asserts the flag is set and the file is kept

## 5. Manifest

- [x] 5.1 Implement load/merge/atomic-write of `data/raw/anac/manifest.json` keyed by output-root-relative path, with the full recorded field set; verify a test asserts every spec'd field is populated after a download
- [x] 5.2 Use recorded `ETag`/`Last-Modified` to skip unchanged files and re-download republished ones, with `--force` bypassing the check; verify tests cover skip-on-match, re-download-on-changed-ETag, force-overrides-match, and that a file present on disk but absent from the manifest is re-downloaded
- [x] 5.3 Preserve untouched entries on merge and omit failed files entirely; verify a test where one dataset fails and another succeeds retains the prior entry, records the success, and writes no placeholder entry for the failure

## 6. Dialect report and CLI

- [x] 6.1 Implement dialect detection over a 64 KB sample: BOM check, UTF-8-then-Latin-1 candidate ordering, delimiter by frequency count, and header line as the first multi-field non-`Atualizado em:` line; verify unit tests on byte fixtures covering a UTF-8-BOM VRA head, a Latin-1 aerodrome head, a file whose header is on line 1, and an undecodable file reporting `undetermined`
- [x] 6.2 Expose the `Atualizado em:` date as the dataset publication date in the report and manifest; verify a test extracts `2026-09-15` from the aerodrome fixture
- [x] 6.3 Implement the `fetch` CLI with `--datasets`, `--vra-months`, `--include-optional`, `--out`, `--force` and `--dry-run`; verify `--dry-run` against a local server prints URLs and sizes while transferring no bodies, and that the optional dataset is not requested unless opted in
- [x] 6.4 Implement the `inspect` subcommand and the end-of-run summary (downloaded / skipped / failed) with non-zero exit when a required file is missing; verify tests assert exit code 0 on full success and non-zero on a required-file failure
- [x] 6.5 Report the optional dataset's size before transferring it, and report a month folder that exists but is empty in verbose output; verify tests assert both messages appear

## 7. End-to-end verification against the live portal

- [x] 7.1 Run `python3 -m anac_data fetch --dry-run` and confirm it resolves 3 VRA months plus the aerodrome list, that the newest VRA month is the latest one actually holding data (not an empty placeholder folder), and that no `estatisticos` URL appears
- [x] 7.2 Run the real default fetch and confirm `data/raw/anac/vra/` holds 3 monthly CSVs and `data/raw/anac/aerodromos/AerodromosPublicos.csv` exists, each matching the size the portal advertises
- [x] 7.3 Re-run the same command and confirm every file is reported as skipped/unchanged with no bytes transferred, and that `manifest.json` still lists all files
- [x] 7.4 Interrupt a download mid-transfer, re-run, and confirm the file completes with a digest matching an uninterrupted download of the same file
- [x] 7.5 Run `inspect` on the downloaded files and confirm it reports `;` delimiter for all, UTF-8-with-BOM for VRA, Latin-1 for the aerodrome list, and header line 2 for each; record the output in the change notes as the contract for the ingestion lesson
- [x] 7.6 Run `python3 -m unittest discover tests -v` and confirm the full suite passes
