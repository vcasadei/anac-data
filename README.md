# anac-data

Brazilian civil-aviation open data from ANAC, downloaded reproducibly and
committed to this repository so a clone carries the data with it.

The datasets here drive a four-lesson course: ingestion, punctuality
analytics, and a delay-risk simulator. The flight-level data is the spine of
all of it.

- **No dependencies.** Python 3 standard library only. There is nothing to
  install, no virtualenv, no `pip`.
- **The data is in the repo.** Clone it and the CSVs are there. One file is too
  large for GitLab's per-file limit, so it is stored as chunks and rebuilt with
  a script — see [Rebuilding the large file](#rebuilding-the-large-file).

---

## Quick start

```bash
git clone https://github.com/vcasadei/anac-data.git
cd anac-data

# The large statistical file ships as chunks. Rebuild it:
sh scripts/rebuild.sh            # Linux / macOS / WSL
# .\scripts\rebuild.ps1          # Windows PowerShell
```

That is all most people need. Everything below is for refreshing the data from
ANAC or understanding what is in it.

---

## What is in here

Five files, about 400 MB of CSV once the large one is rebuilt.

| Dataset | File | Size | Data rows | Required? |
| --- | --- | --- | --- | --- |
| VRA (Voo Regular Ativo) | `data/raw/anac/vra/VRA_20265.csv` | 11,655,230 | 82,120 | yes |
| VRA | `data/raw/anac/vra/VRA_20266.csv` | 11,496,087 | 81,119 | yes |
| VRA | `data/raw/anac/vra/VRA_20267.csv` | 12,691,900 | 89,517 | yes |
| Aeródromos Públicos | `data/raw/anac/aerodromos/AerodromosPublicos.csv` | 101,504 | 496 | yes |
| Dados Estatísticos | `data/raw/anac/estatisticos/Dados_Estatisticos.csv` | 358,893,852 | 1,096,554 | optional |

Figures measured on 2026-09-17.

### VRA — one row per flight leg

The central dataset: 12 columns covering scheduled and actual times, so delays
and cancellations come straight out of it.

`ICAO Empresa Aérea`, `Número Voo`, `Código Autorização (DI)`,
`Código Tipo Linha`, `ICAO Aeródromo Origem`, `ICAO Aeródromo Destino`,
`Partida Prevista`, `Partida Real`, `Chegada Prevista`, `Chegada Real`,
`Situação Voo`, `Código Justificativa`

Delay is `Partida Real − Partida Prevista`. `Situação Voo` is only ever
`REALIZADO` or `CANCELADO`.

**Which months?** The three most recent that ANAC has actually published —
currently May, June and July 2026. The filename carries the year and an
*unpadded* month (`VRA_20267.csv` is 2026-07). Each file holds its own month
plus a handful of rows spilling into the next, from flights scheduled near
midnight on the last day.

The month count is not hardcoded. The downloader reads the portal's directory
listings and takes the N most recent months that **contain a file** — because
ANAC pre-creates empty folders for the rest of the calendar year, and those
empty folders carry *later* modification dates than folders that hold data.
Picking the newest-looking folder gets you nothing.

### Aeródromos Públicos — the airport lookup table

18 columns. Join its `Código OACI` against VRA's `ICAO Aeródromo Origem` /
`ICAO Aeródromo Destino` to turn `SBRJ` into "Santos Dumont, Rio de Janeiro,
RJ". Also carries `Município`, `UF`, latitude, longitude and altitude.

Without it, a "busiest airport" ranking shows only four-letter codes.

### Dados Estatísticos — passenger and cargo volumes (optional)

38 columns, 1.1 million rows, spanning 2000 to the present with `ANO` and `MES`
columns. Gives real passenger and cargo volumes per airport rather than a count
of flights.

It is **optional**: flight counts per airport can be computed from VRA alone, so
none of the lessons need this file. It is also the file that does not fit in
git, and it is republished frequently — its `Atualizado em:` date was the same
day it was downloaded.

> One correction worth knowing: this dataset is **not** split by month, and
> there is no separate "básica" file on the portal. It is a single CSV covering
> everything, and the básica/combinada distinction is a row-level attribute
> inside the data. Filtering to one month and type happens after download.

---

## Reading the CSVs (do this before writing a parser)

These three facts are measured from the bytes, not assumed. Getting any of them
wrong produces silently corrupt data rather than an error.

| | VRA | Aeródromos | Estatísticos |
| --- | --- | --- | --- |
| Delimiter | `;` | `;` | `;` |
| Encoding | **UTF-8 with BOM** | **Latin-1** | UTF-8 with BOM |
| Header row | **line 2** | **line 2** | **line 2** |
| Line endings | CRLF | CRLF | CRLF |

1. **Encoding differs between datasets.** VRA is UTF-8 with a byte-order mark;
   the aerodrome list is Latin-1 (ISO-8859-1). Using one encoding for both will
   mangle the accents in whichever you guess wrong.
2. **Line 1 is never the header.** Every file opens with a metadata line,
   `Atualizado em: YYYY-MM-DD`. The column names are on line 2. Code that reads
   line 1 as the header gets a single junk column.
3. **Delimiter is `;`**, not a comma. VRA quotes its fields; the aerodrome list
   does not.

```python
import pandas as pd

vra = pd.read_csv(
    "data/raw/anac/vra/VRA_20267.csv",
    sep=";", encoding="utf-8-sig", skiprows=1,
)
aerodromos = pd.read_csv(
    "data/raw/anac/aerodromos/AerodromosPublicos.csv",
    sep=";", encoding="latin-1", skiprows=1,
)
```

### Gotchas that will bite

- **Literal `"null"` strings, not empty fields.** In July 2026, 2,724 rows are
  `CANCELADO` with `Partida Real` as the four-character text `null` — expected.
  But 2,576 rows are `REALIZADO` with **`Partida Prevista`** as `null`: flights
  that operated with no scheduled departure recorded. Those are not
  cancellations, and they will break a naive delay calculation.
- **Mixed decimal separators in the aerodrome list.** `Latitude` uses a decimal
  point (`-9.8683333`) while `Altitude` uses a decimal comma (`193,0`) — in the
  same row.
- **`Atualizado em:` is the publication date**, and it is recorded per file in
  `data/raw/anac/manifest.json` as `atualizado_em`, so you never have to reopen
  the CSV to find it.

To see these facts for yourself, from the files on disk:

```bash
PYTHONPATH=src python3 -m anac_data inspect
```

---

## Rebuilding the large file

`Dados_Estatisticos.csv` is 342.3 MiB, larger than GitLab's per-file limit. It
is committed as a gzipped, chunked form under `data/dist/`, and either script
puts it back:

```bash
sh scripts/rebuild.sh                  # Linux, macOS, WSL
```

```powershell
.\scripts\rebuild.ps1                  # Windows
```

Both scripts are **standalone**: no Python, no network, no dependency on this
project's package. Copy one next to a chunk directory and it still works. Run
either from the repository root and it rebuilds every chunk set it finds.

They verify as they go — each chunk's SHA-256 before concatenating, then the
rebuilt file's SHA-256 after decompressing — and write to a temporary name,
moving it into place only on success. A corrupt or missing chunk is named, the
script exits non-zero, and no file is left that could pass for a good rebuild.
Running again when the file is already correct does nothing.

Options:

```bash
sh scripts/rebuild.sh <chunk-dir> -o <output-dir>
.\scripts\rebuild.ps1 -ChunkDir <path> -OutputDir <path>
```

### How the chunking works

342.3 MiB compresses to 45.3 MiB (13.2%), which is then cut into pieces of at
most 45 MiB — currently two. One directory holds one rebuildable file:

```
data/dist/estatisticos/Dados_Estatisticos.csv/
├── Dados_Estatisticos.csv.gz.part001    47,185,920 bytes
├── Dados_Estatisticos.csv.gz.part002       302,130 bytes
├── MANIFEST.json          sizes and digests of the original, the gzip
│                          stream and every chunk
└── checksums.sha256       the same digests as plain `sha256sum -c` lines,
                           so the shell script needs no JSON parser
```

Chunk indices are zero-padded, so sorting the names as text gives the order to
concatenate them in.

Compression is **deterministic** — the gzip timestamp is pinned — so
repackaging unchanged data produces byte-identical chunks and adds nothing to
git history. `package` reports "unchanged" and rewrites nothing.

You can verify a chunk set by hand without this project at all:

```bash
cd data/dist/estatisticos/Dados_Estatisticos.csv
sha256sum -c checksums.sha256
```

---

## Refreshing the data from ANAC

Only needed when you want newer months or a fresher statistical file.

```bash
# See what would be downloaded, and from where, without transferring anything
PYTHONPATH=src python3 -m anac_data fetch --dry-run

# Download the required datasets (3 VRA months + the aerodrome list, ~34 MiB)
PYTHONPATH=src python3 -m anac_data fetch

# Include the optional 342 MiB statistical file
PYTHONPATH=src python3 -m anac_data fetch --include-optional

# Re-chunk anything now over the size limit
PYTHONPATH=src python3 -m anac_data package
```

`PYTHONPATH=src` is needed because the package lives in `src/`.

Re-running `fetch` transfers nothing for files ANAC has not republished: it
compares the `ETag` and `Last-Modified` recorded in
`data/raw/anac/manifest.json`. Interrupted downloads resume from where they
stopped rather than starting over.

### Useful flags

| Command | Flag | Effect |
| --- | --- | --- |
| `fetch` | `--vra-months N` | take N most recent VRA months (default 3) |
| `fetch` | `--datasets vra,aerodromos,estatisticos` | fetch only these |
| `fetch` | `--include-optional` | add the statistical dataset |
| `fetch` | `--dry-run` | report URLs and sizes, transfer nothing |
| `fetch` | `--force` | re-download despite matching validators |
| `fetch` | `-v` | show discovery details, e.g. empty month folders |
| `package` | `--chunk-size N` | bytes per chunk, and the size above which a file is chunked |
| `package` | `--dry-run` | report what would be packaged |
| `package` | `--force` | repackage even if unchanged |
| `inspect` | *(paths)* | report dialect for specific files |

### If a push is rejected for file size

The chunk size is a guess at this server's limit — 45 MiB, chosen to stay clear
of a 50 MB cap. If a push is still rejected, lower it and repackage; no format
change is involved.

```bash
PYTHONPATH=src python3 -m anac_data package --chunk-size 9437184   # 9 MiB
```

The default lives in `src/anac_data/paths.py` as `CHUNK_LIMIT_BYTES`.

---

## Repository layout

```
data/
├── raw/anac/                     working copies, committed
│   ├── vra/VRA_2026{5,6,7}.csv
│   ├── aerodromos/AerodromosPublicos.csv
│   ├── estatisticos/             NOT committed - rebuilt from chunks
│   └── manifest.json             provenance: URL, size, sha256, ETag,
│                                 Atualizado em, and measured dialect
└── dist/                         committed chunk sets
    └── estatisticos/Dados_Estatisticos.csv/

scripts/
├── rebuild.sh                    standalone POSIX rebuild
└── rebuild.ps1                   standalone PowerShell rebuild

src/anac_data/                    the tool (stdlib only)
├── paths.py                      the only place portal paths and limits live
├── http.py                       user agent, timeouts, pacing, retries
├── listing.py                    reads the portal's HTML directory index
├── discovery.py                  decides which files a dataset means
├── download.py                   streaming, resumable, verified downloads
├── manifest.py                   what is on disk and where it came from
├── dialect.py                    measures delimiter, encoding, header line
├── packaging.py                  gzip + chunk, and the chunk metadata
└── cli.py                        fetch / inspect / package

tests/                            stdlib unittest; no pytest, no pip
openspec/                         specs and change history
```

### A note on committing data

Keeping the datasets in git means a clone is self-sufficient, which is the
point. It also means **the repository grows permanently** — git keeps every
version of every file forever.

Current tracked data is about 80 MiB. Each future VRA month adds roughly 12 MiB
for good, and each refresh of the statistical file adds about **45 MiB** to
history, even if you later delete it. That file is republished often, so
refresh it when you need newer numbers rather than out of habit.

Deterministic chunking means re-running `package` on unchanged data costs
nothing. Only genuinely new content adds weight.

---

## Running the tests

```bash
python3 -m unittest discover tests -v
```

Stdlib `unittest`, because this machine has no working `pip`. Network behaviour
is tested against a local `http.server` and saved copies of the real portal
pages, so the suite never touches `sistemas.anac.gov.br`. Both rebuild scripts
are executed as real subprocesses, and one test asserts they produce identical
digests from the same chunk set.

---

## Data source and licence

All data comes from ANAC's open-data portal,
<https://sistemas.anac.gov.br/dadosabertos/>, a public nginx directory index.
Access is read-only, unauthenticated, sequential and rate-limited.

Portal paths used:

- `Voos e operações aéreas → Voo Regular Ativo (VRA) → <year> → <month>`
- `Aerodromos → Aeródromos Públicos → Lista de aeródromos públicos`
- `Voos e operações aéreas → Dados Estatísticos do Transporte Aéreo`

ANAC has changed its file-naming conventions more than once, which is why
nothing here hardcodes a filename — every download follows a link read from the
portal's own listing at run time. All portal paths live in one table in
`src/anac_data/paths.py`; that is the only file to edit if the portal is
reorganised again.

Consult ANAC for the licensing terms attached to the data itself.

The code in this repository is released under the [MIT License](LICENSE); the
data files are ANAC's and are covered by [DATA_NOTICE.md](DATA_NOTICE.md), not by
the MIT License.
