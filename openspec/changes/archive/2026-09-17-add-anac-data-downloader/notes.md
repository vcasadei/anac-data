# Verification notes

Measured against the live portal on 2026-09-17, after `python3 -m anac_data fetch`.
This is the contract the ingestion lesson should code against - every value below
came from `anac_data inspect` reading the files' actual bytes, not from assumption.

## Files obtained

| Local path | Bytes | `Atualizado em:` |
| --- | --- | --- |
| `data/raw/anac/vra/VRA_20267.csv` | 12,691,900 | 2026-08-28 |
| `data/raw/anac/vra/VRA_20266.csv` | 11,496,087 | 2026-08-28 |
| `data/raw/anac/vra/VRA_20265.csv` | 11,655,230 | 2026-08-28 |
| `data/raw/anac/aerodromos/AerodromosPublicos.csv` | 101,504 | 2026-09-15 |

All four matched the size the portal advertised. July 2026 is the most recent VRA
month holding data; `08 - Agosto` through `12 - Dezembro` exist as empty
placeholder folders and were correctly skipped.

## Dialect report

```
AerodromosPublicos.csv        VRA_2026{5,6,7}.csv
  encoding:   Latin-1           encoding:   UTF-8 with BOM
  delimiter:  ';'               delimiter:  ';'
  header:     line 2            header:     line 2
  columns:    18                columns:    12
```

Three things to carry into ingestion:

1. **Encoding differs per dataset.** Read VRA as `utf-8-sig` and the aerodrome
   list as `latin-1`. A single encoding for both will corrupt one of them.
2. **Skip one line before the header.** Every file's line 1 is
   `Atualizado em: YYYY-MM-DD`; the column names are on line 2. With pandas:
   `skiprows=1`. That first line is also the dataset's publication date and is
   recorded per file in `manifest.json` as `atualizado_em`.
3. **Delimiter is `;`** in all files, with `"`-quoted fields in VRA and unquoted
   fields in the aerodrome list. Line endings are CRLF.

Watch out in the aerodrome list: `Latitude`/`Longitude` use a decimal **point**
(`-9.8683333`) while `Altitude` uses a decimal **comma** (`193,0`) in the same row.

## Columns

`VRA_*.csv` (12): `ICAO Empresa Aérea`, `Número Voo`, `Código Autorização (DI)`,
`Código Tipo Linha`, `ICAO Aeródromo Origem`, `ICAO Aeródromo Destino`,
`Partida Prevista`, `Partida Real`, `Chegada Prevista`, `Chegada Real`,
`Situação Voo`, `Código Justificativa`

`AerodromosPublicos.csv` (18): `Código OACI`, `CIAD`, `Nome`, `Município`, `UF`,
`Município Servido`, `UF Servido`, `LATGEOPOINT`, `LONGEOPOINT`, `Latitude`,
`Longitude`, `Altitude`, `Operação Diurna`, `Operação Noturna`, `Situação`,
`Validade do Registro`, `Portaria de Registro`, `Link Portaria`

The join for the "busiest airport" ranking is VRA's `ICAO Aeródromo Origem` /
`ICAO Aeródromo Destino` against the aerodrome list's `Código OACI`.

## Behaviors confirmed live

- **Idempotent re-run**: a second `fetch` reported all four files `up to date`
  and transferred no bytes; the manifest kept all four entries.
- **Resume**: a download killed mid-transfer left a 3,145,728-byte `.part`; the
  next run finished it and produced a file byte-identical
  (`sha256 2cd8ceb8…74d`) to an uninterrupted download. A separate check on the
  aerodrome file confirmed the portal honours `Range` - staged at 50,000 bytes,
  only the remaining 51,504 crossed the wire.
- **Optional dataset untouched by default**: no request was made to the
  `Dados Estatísticos` path on a default run.
