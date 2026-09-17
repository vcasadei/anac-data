# Design

See `proposal.md` for motivation and `specs/anac-data-download/spec.md` for the behavior contract.

## Context

The portal is an **nginx `autoindex`** — plain HTML directory listings, no API, no search, no JSON index. Verified against the live site on 2026-09-17:

```
/dadosabertos/
├── Voos e operações aéreas/
│   ├── Voo Regular Ativo (VRA)/<YYYY>/<MM - Month>/VRA_<YYYY><M>.csv
│   └── Dados Estatísticos do Transporte Aéreo/Dados_Estatisticos.csv
└── Aerodromos/Aeródromos Públicos/Lista de aeródromos públicos/AerodromosPublicos.csv
```

Properties that shape the design:

- **Path segments contain spaces, accents and parentheses**, percent-encoded as UTF-8 in the listing HTML (`Aer%C3%B3dromos%20P%C3%BAblicos`).
- **Responses carry `Content-Length`, `ETag`, `Last-Modified` and `Accept-Ranges: bytes`** — everything needed for revalidation and resume. `Content-Type` is `application/octet-stream` for CSVs, so it is useless for format detection.
- **Month folders for future months exist and are empty.** As of today, VRA `2026/08 - Agosto` … `12 - Dezembro` are present but contain nothing; the last month with data is July (`VRA_20267.csv`). Folder modification dates are also misleading — the empty folders are dated `31-Mar-2026`, later than some folders that do hold data.
- **Month files are not zero-padded**: `VRA_20267.csv` for 2026-07, so the folder name (`07 - Julho`) and the filename digit (`7`) disagree in format.
- **Folders hold sibling `.json` twins and stray files** — each month has `VRA_<...>.json` alongside the CSV, and `2026/` contains a `tmp.txt`.
- **Every CSV begins with an `Atualizado em: YYYY-MM-DD` line**; the column header is line 2.
- **Encoding is per-file**: VRA and Dados Estatísticos are UTF-8 with BOM (`EF BB BF`); `AerodromosPublicos.csv` is Latin-1 (`0xF3` for `ó`, `0xED` for `í`).
- **Sizes**: VRA ≈ 12 MB/month, aerodromes ≈ 100 KB, statistical CSV ≈ 359 MB.

Environment constraint: Python 3.14 with **no working `pip` or `uv`**. Standard library only.

## Goals / Non-Goals

**Goals:**

- One command produces a complete, verifiable raw-data tree on a machine with nothing installed.
- Survive ANAC renaming files or changing folder layouts within the documented navigation path, failing loudly rather than silently downloading nothing.
- Make the real CSV dialect discoverable, so lesson 2 codes against measured facts.

**Non-Goals:**

- No parsing, cleaning, or loading of the data — this change only lands bytes and describes them. Row-level filtering (including "básica" vs "combinada") belongs to ingestion.
- No incremental/partial-file updates — a republished file is re-fetched whole.
- No parallel downloads. Three required files do not justify concurrency against a public agency portal.
- No mirroring of the `.json` twins. CSV only, per the course instructions.

## Decisions

### Follow `href` values from the listing; never build a path by hand

The crawler resolves each step by fetching the parent listing, matching the wanted entry, and following that entry's `href` via `urljoin`. The hrefs are already correctly percent-encoded by nginx.

*Why:* re-encoding `Aeródromos Públicos` ourselves invites encoding bugs on every accented segment, and it is exactly the brittleness the source instructions warn about. Following hrefs makes the tool correct by construction and gives a natural place to report "expected X, found [A, B, C]".

*Alternative rejected:* hardcoding percent-encoded URLs. Fastest, but breaks the moment ANAC reorganizes — and it has before.

*Matching is tolerant by necessity:* folder names are compared case-insensitively with accents stripped and whitespace collapsed, so `Aeródromos Públicos` still matches if ANAC writes `AERODROMOS PUBLICOS`.

### Parse with `html.parser`, take sizes from HTTP headers

An `HTMLParser` subclass collects `<a href>` values, discarding `../`. Directory entries are those ending in `/`.

*Why:* nginx wraps listings in a `<pre>` block where the size column is loose text, not markup. Parsing that text means re-implementing nginx's column formatting (including its `1.2M` human-readable variants). A `HEAD` request per candidate gives an exact `Content-Length` plus the `ETag`/`Last-Modified` we need anyway.

*Alternative rejected:* a regex over the raw HTML. Works today, but an autoindex format or `autoindex_format` change breaks it in ways that are hard to notice.

### Select VRA months from a flattened, content-checked candidate list

Walk year folders newest-first; for each, list its month folders and keep only those containing a file matching `*.csv` with a `VRA` prefix. Sort candidates by `(year, month)` descending and take the first N. Month number comes from the leading digits of the folder name (`07 - Julho` → 7), not from the Portuguese month name.

*Why:* this satisfies the spec's three VRA scenarios with one mechanism — empty future folders drop out because they hold no file, and crossing a year boundary is just the loop continuing to the next year. Parsing the leading digits avoids a Portuguese-month lookup table and the accent in `Março`.

*Cost:* one listing request per month folder inspected. Bounded (~N+2 requests) and acceptable at this rate.

*Alternative rejected:* trusting folder modification dates. Verified wrong — empty placeholder folders carry later dates than populated ones.

### Download to `<name>.part`, then `os.replace`

Bytes stream to a sibling `.part` file in chunks, hashing as they are written. On success the file is `os.replace`d onto the final path — atomic within a filesystem.

*Why:* this is what makes "a partial file is never mistaken for complete" true rather than aspirational, and it gives resume a natural home: a leftover `.part` is the resume offset.

*Resume:* if a `.part` exists, send `Range: bytes=<size>-` and append on `206 Partial Content`. If the server answers `200` (range ignored), discard the `.part` and restart — appending to a full body would corrupt the file. Because the SHA-256 cannot be continued across a process restart, the digest for a resumed file is computed by reading the completed file once; at ≤360 MB this is a cheap sequential pass.

*Note:* `data/` lives on a Windows drive via WSL (`/mnt/c`). `os.replace` is atomic there, but the `.part` file must be created in the same directory as the destination, not in a temp dir on another filesystem.

### Manifest is a single `data/raw/anac/manifest.json`, merged not overwritten

Keyed by local path relative to the output root. Each run loads the existing manifest, updates the entries it touched, and writes the whole file back via the same write-temp-then-replace dance.

*Why:* merging is what keeps a skipped file's entry alive (spec: "manifest survives a skipped file") and keeps a partially failed run's successes recorded. A single file is easier to read in class than per-dataset sidecars, and it doubles as the revalidation cache — `ETag`/`Last-Modified` are read from it, so no separate state store is needed.

*Consequence:* deleting the manifest turns the next run into a full re-download. That is acceptable and honest — without recorded validators the tool genuinely does not know what is on disk. A file present on disk but absent from the manifest is treated as unknown and re-downloaded.

### Dialect detection: sniff bytes, decide over a candidate list

Read the first ~64 KB. Check for a UTF-8 BOM. Attempt `utf-8` (strict) then `cp1252`/`latin-1`; the first that decodes the sample cleanly wins, and if `utf-8` fails and the Latin-1 attempt produces no replacement characters, report Latin-1. If neither yields plausible text, report `undetermined` rather than guessing — the spec requires this over a confident wrong answer. The delimiter is chosen by counting `;` vs `,` vs `\t` in the first decoded lines. The header line is the first line that is not an `Atualizado em:` metadata line and that splits into more than one field on the chosen delimiter.

*Why:* Latin-1 decodes *any* byte sequence without raising, so "try Latin-1 and see if it throws" can never fail and would mislabel every UTF-8 file. Ordering UTF-8 first and treating Latin-1 as the fallback is the only ordering that distinguishes the two datasets we verified. Detecting the header by rule rather than assuming line 2 satisfies the "header is on the first line" scenario if ANAC drops the metadata line.

*Alternative rejected:* `csv.Sniffer`. It guesses delimiters from a decoded sample and does not address encoding at all, which is the harder half of the problem here; it also trips over the metadata first line.

### `urllib.request` with a retry wrapper

A single `fetch()` helper wraps `urllib.request.urlopen`, applying a descriptive `User-Agent`, a timeout, a small inter-request delay, and retries with exponential backoff on timeouts and 5xx — but not on 404, which is reported at once.

*Why:* stdlib-only is mandatory here (no `pip`), and this is the whole of what `requests` would have given us for this job. Keeping it in one function means retry and politeness policy cannot drift between call sites.

### CLI shape

```
python3 -m anac_data fetch [--datasets vra,aerodromos,estatisticos]
                           [--vra-months 3] [--include-optional]
                           [--out data/raw/anac] [--force] [--dry-run]
python3 -m anac_data inspect [path...]     # dialect report for downloaded CSVs
```

`--include-optional` is the opt-in gate from the spec; naming `estatisticos` in `--datasets` is the explicit single-dataset form and does not require the flag. `--dry-run` runs full discovery and `HEAD` requests, printing URLs and sizes without transferring bodies — which also makes it the diagnostic to run when the portal layout changes.

## Risks / Trade-offs

- **ANAC reorganizes folder names** → tolerant matching (case/accent-insensitive) absorbs cosmetic changes; a real restructure fails loudly with the actual listing contents so the navigation constants can be fixed in one place. Path constants live in one module-level table, not scattered through the crawler.
- **The optional 359 MB download on a slow or metered connection** → off by default, gated behind an explicit flag, size reported before the transfer starts, and resumable if interrupted.
- **Encoding sniffing is heuristic** → the sample is 64 KB rather than a couple of lines, the candidate order is grounded in the two verified files, and the report can say `undetermined` instead of asserting a wrong answer. The dialect report is advisory output, not something downstream code silently depends on.
- **`Atualizado em:` is assumed to be a metadata line, not data** → verified present in all three CSVs, and the header detection rule degrades correctly if it disappears. Risk is limited to a wrong `header_line` in an advisory report, not to lost bytes.
- **Empty-folder heuristic could hide a genuine publication gap** → if a month folder exists but is empty, it is reported as "present but empty" in verbose output rather than silently skipped, so a real ANAC publishing failure is distinguishable from a future placeholder.
- **Polite sequential downloads make a full optional run slow** → accepted deliberately; this is a public agency portal and a once-per-setup operation.

## Migration Plan

Additive — the repo has no code today. Rollback is deleting `src/anac_data/` and the `data/` tree; no external state is touched. `data/` is added to `.gitignore` in the same change so the downloads never enter git history.

## Open Questions

- Whether later lessons want the `.json` twins as well. Deferrable: adding a format selector would extend the existing discovery filter without changing the crawl, the layout, or the manifest shape.
