"""Build the raw schema (raw.load and raw.weather) in a DuckDB file from the source CSVs.

Usage:
    python scripts/load_raw.py                       # writes energy.duckdb in the repo root
    python scripts/load_raw.py --db other.duckdb     # write somewhere else (e.g. a test copy)

Why this exists: dbt only transforms data that is already in the database. This script is
the one step that gets the downloaded files INTO the database, so anyone can rebuild it.

Idempotent: it uses CREATE OR REPLACE TABLE, so running it twice gives the same result
(the table is rebuilt from the files, rows are never appended twice).

Safety check: the row counts are compared with the expected ones at the end. A truncated
or wrong download makes the script exit with a non-zero status instead of loading silently.
"""
import argparse
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
LOAD_FILE = "eco2mix-national-cons-def.csv"
WEATHER_FILE = "open-meteo-48.85N2.36E43m.csv"

# Counts measured on the files downloaded on 2026-11-13. RTE revises its "consolidees"
# data, so a later download may legitimately differ: if so, check why, then update these.
EXPECTED_ROWS = {"raw.load": 491_040, "raw.weather": 35_064}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(REPO / "energy.duckdb"), help="DuckDB file to write")
    parser.add_argument("--data-dir", default=str(REPO / "data"), help="Folder with the CSV files")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    load_path, weather_path = data_dir / LOAD_FILE, data_dir / WEATHER_FILE
    missing = [p for p in (load_path, weather_path) if not p.exists()]
    if missing:
        print("Missing source file(s), see data/README.md for where to download them:")
        for p in missing:
            print(f"  {p}")
        return 1

    con = duckdb.connect(args.db)
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")

    # Every load states its settings explicitly (see CLAUDE.md, rule 7): delimiter, null
    # strings and sample size. Auto-detection alone can pick different types on a different
    # file or sample, which is exactly how raw would silently change.
    #
    # eco2mix: semicolon-delimited, French headers.
    #   sample_size=-1  read EVERY row before choosing column types (types cannot drift).
    #   nullstr         the file contains the text "ND" in numeric columns (17,570 times in
    #                   "Gaz - Cogeneration (MW)" and 28 times in "Ech. comm.
    #                   Allemagne-Belgique (MW)"). We load it as NULL so those columns stay
    #                   numeric (BIGINT), as in the original raw tables.
    #                   ASSUMPTION, NOT VERIFIED against RTE's documentation: "ND" means
    #                   "non disponible" (not available), i.e. missing, not a real value.
    con.execute(
        "CREATE OR REPLACE TABLE raw.load AS "
        "SELECT * FROM read_csv(?, delim=';', sample_size=-1, nullstr=['', 'ND'])",
        [str(load_path)],
    )
    # Open-Meteo: comma-delimited; 3 metadata lines (coordinates, values, blank) come before
    # the real header, hence skip=3. This file has no missing values, so only the empty
    # string counts as NULL.
    con.execute(
        "CREATE OR REPLACE TABLE raw.weather AS "
        "SELECT * FROM read_csv(?, delim=',', skip=3, sample_size=-1, nullstr=[''])",
        [str(weather_path)],
    )

    ok = True
    for table, expected in EXPECTED_ROWS.items():
        actual = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        status = "OK" if actual == expected else "MISMATCH"
        print(f"{table}: {actual:,} rows (expected {expected:,}) {status}")
        ok &= actual == expected
    con.close()

    if not ok:
        print("Row counts do not match. Check the downloaded files before using this database.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
