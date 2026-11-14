# Source data (not committed)

The raw files are large and belong to their publishers, so they are **not** in git.
Download them and put them in this folder (`data/`) with exactly these names, then run
`python scripts/load_raw.py` from the repository root.

| File (exact name) | What it is | Where to get it |
|---|---|---|
| `eco2mix-national-cons-def.csv` | RTE eCO2mix, national consolidated and definitive data, 15-minute grid, 2012 to 2026-01-01 (about 82 MB) | RTE open data portal, dataset "eco2mix-national-cons-def": https://odre.opendatasoft.com (export as CSV, semicolon-separated) |
| `open-meteo-48.85N2.36E43m.csv` | Open-Meteo historical weather, hourly `temperature_2m` for Paris (48.85 N, 2.36 E), 2022-01-01 to 2025-12-31, timezone GMT | https://open-meteo.com/en/docs/historical-weather-api (set the coordinates, the date range, hourly `temperature_2m`, timezone GMT, then download CSV) |

The URLs above were written from memory and **not verified**; if a page has moved, search
for the dataset or product name. Keep the filenames as listed, because the script looks for them.

## Download date and drift

Both files were downloaded on **2026-11-13** (taken from the file timestamps).
RTE revises its "consolidees" data, so a later download can have a different row count or
different values. The script checks the counts it expects (491,040 and 35,064) and fails
if they differ: when that happens, investigate before trusting the new data, then update
the expected numbers in `scripts/load_raw.py`.

## Why `raw.load` is not a byte-for-byte copy of the file: `ND` values

The eco2mix file contains the text `ND` in numeric columns:

| Column | Rows with `ND` |
|---|---|
| `Gaz - Cogénération (MW)` | 17,570 |
| `Ech. comm. Allemagne-Belgique (MW)` | 28 |

(One row has both, so 17,597 rows in total differ from a literal reading of the file.)
`scripts/load_raw.py` loads `ND` as `NULL`, so these columns stay numeric (`BIGINT`).
This is an **assumption I have not verified against RTE's documentation**: that `ND` means
"non disponible" (not available), that is, missing data and not a real value.
