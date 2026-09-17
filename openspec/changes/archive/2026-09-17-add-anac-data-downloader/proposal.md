# Proposal

## Why

A four-lesson course (ingestion, punctuality analytics, risk simulator) needs ANAC open data on disk before any lesson can run, but the only documented path today is manual browser clicking through `https://sistemas.anac.gov.br/dadosabertos/`. Manual download is not reproducible, cannot be re-run when a student arrives with an empty `data/` directory, and ANAC has changed its URL naming more than once — so a fixed list of URLs goes stale. We need a small, dependency-free Python tool that discovers what is currently published and fetches it.

## What Changes

- Add a Python CLI (`anac_data`) that crawls the ANAC open-data nginx directory index and downloads the datasets the course requires into `data/raw/anac/<dataset>/`.
- **VRA (required)**: discover the most recent months that actually contain files and download the 3 most recent (count configurable). Discovery is listing-driven, never name-guessing.
- **Lista de Aeródromos Públicos (required)**: download the current `AerodromosPublicos.csv`.
- **Dados Estatísticos (optional)**: download the single full `Dados_Estatisticos.csv` only behind an explicit `--include-optional` flag, off by default.
- Record a `manifest.json` per run capturing source URL, size, `ETag`/`Last-Modified`, SHA-256 and the portal's own `Atualizado em:` date for every downloaded file, so a dataset on disk is traceable to what the portal served.
- Re-runs are idempotent: an unchanged remote file is skipped via `ETag`/`Last-Modified` revalidation rather than re-downloaded; interrupted downloads resume via HTTP `Range`.
- Probe and report each file's real CSV dialect (delimiter, encoding, header line) after download, so the ingestion lesson is not written against a guess.
- Add `data/` to `.gitignore` — the required downloads alone are ~36 MB and the optional one is ~359 MB.

### Corrections to the written instructions

Live inspection of the portal on 2026-09-17 contradicted three assumptions in the source instructions. The plan follows the portal, not the notes:

- **Encoding is per-dataset, not uniformly Latin-1.** VRA and Dados Estatísticos are UTF-8 with a BOM; `AerodromosPublicos.csv` is Latin-1. Both must be handled explicitly.
- **Line 1 is not the header** in any of the three CSVs. Every file opens with an `Atualizado em: YYYY-MM-DD` metadata line; the real header is line 2.
- **Dados Estatísticos is not split by month and has no separate "básica" file.** It is one ~359 MB CSV spanning 2000→present with `ANO`/`MES` columns; "básica" is a row-level attribute inside the data, not a file to pick. Downloading "1 month, básica" is therefore not possible as described — the flag fetches the whole file, and any month/type filtering belongs to a later ingestion step.

Also note the portal pre-creates empty month folders for the rest of the calendar year (as of today, `08 - Agosto` through `12 - Dezembro` of 2026 exist but are empty). "Latest folder" is not "latest data", which is why discovery must inspect folder contents.

## Capabilities

### New Capabilities
- `anac-data-download`: Discovering, downloading, verifying and cataloguing ANAC open-data files from the public nginx directory index into a local raw-data tree.

### Modified Capabilities

None — this is the project's first capability.

## Impact

- **New code**: `src/anac_data/` package plus a `python3 -m anac_data` entry point. No existing code to modify (the repo currently holds only a README).
- **Dependencies**: none. Standard library only (`urllib.request`, `html.parser`, `hashlib`, `csv`) — this machine has Python 3.14 with no working `pip` or `uv`, and students should not need one.
- **Filesystem**: creates and writes `data/raw/anac/{vra,aerodromos,estatisticos}/`, which becomes git-ignored.
- **Network**: read-only HTTPS GETs against `sistemas.anac.gov.br`, a public open-data portal. Requests are sequential and rate-limited to stay well within polite use; no authentication is involved.
- **Downstream**: lessons 2–4 consume the resulting files. The dialect report is the contract the ingestion lesson codes against.
