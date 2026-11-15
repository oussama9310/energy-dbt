"""Extract hourly Paris temperature from the Open-Meteo Historical Weather API.

Usage:
    python scripts/extract_weather.py --start 2022-01-01 --end 2025-12-31
    python scripts/extract_weather.py --start 2022-01-01 --end 2025-12-31 --out-dir other/folder
    python scripts/extract_weather.py --start ... --end ... --latitude 48.853382 --longitude 2.355208
    python scripts/extract_weather.py --start ... --end ... --extra-param models=era5

This is EXTRACTION ONLY (the E of ELT): it saves the API answer exactly as received into a
landing folder, plus a small metadata file. It does not clean anything and does not touch
the database. Loading into raw is a separate step.

Each run writes a NEW pair of files and never overwrites an older pull:
    open-meteo_archive_48.85N_2.36E_<start>_<end>_pulled-<UTC timestamp>.json       (raw bytes)
    open-meteo_archive_48.85N_2.36E_<start>_<end>_pulled-<UTC timestamp>.meta.json   (metadata)

Exit status: 0 on success, 1 on any failure (message on stderr).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

SCRIPT_VERSION = "1.0"
REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO / "data" / "landing" / "weather"

ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
LATITUDE, LONGITUDE = 48.85, 2.36  # Paris; matches the manual file open-meteo-48.85N2.36E43m.csv

# Rule 7 (CLAUDE.md): state every setting that influences the values, even when it equals
# the API default, so a change of default on their side cannot silently change our data.
# NOTE: the docs list no "models" parameter, so none is sent. The docs say the default
# "Best match" "combines IFS HRES, ERA5 and ERA5-Land seamlessly", and the response does not
# say which model produced the values (recorded in the metadata as not reported).
FIXED_PARAMS = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "hourly": "temperature_2m",
    "timezone": "GMT",
    "temperature_unit": "celsius",
    "timeformat": "iso8601",
    "cell_selection": "land",
}

# Network behaviour. A timeout stops a stalled server from hanging the script forever.
CONNECT_TIMEOUT_S, READ_TIMEOUT_S = 10, 60
RETRY_WAITS_S = (2, 4, 8)  # 3 retries, waiting longer each time (exponential backoff)

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:00$")  # e.g. 2022-01-01T00:00 (no offset: GMT)

ATTRIBUTION = (
    "Weather data by Open-Meteo.com, CC BY 4.0. Non-commercial use only on the free API. "
    "See README.md."
)


class ExtractionError(Exception):
    """Anything that must stop the script with a clear message and exit status 1."""


def parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ExtractionError(f"Invalid date '{text}': expected format yyyy-mm-dd") from None


def fetch(params: dict) -> tuple[requests.Response, str]:
    """GET the API, retrying only on failures that can pass by themselves.

    Retried: timeouts, connection errors, HTTP 429 (too many requests) and 5xx (server trouble).
    NOT retried: other 4xx, notably 400. That means OUR request is wrong, and sending the same
    wrong request again can never help. We wait longer after each failure so we do not hammer a
    server that is already struggling, and the retry cap stops us looping forever.
    """
    prepared = requests.Request("GET", ENDPOINT, params=params).prepare()
    url = prepared.url
    session = requests.Session()
    # A generic client name only: no personal details are ever sent to the API.
    session.headers["User-Agent"] = "energy-dbt-extract/" + SCRIPT_VERSION
    last_problem = ""
    for attempt in range(len(RETRY_WAITS_S) + 1):
        try:
            resp = session.send(prepared, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_problem = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_problem = f"HTTP {resp.status_code}: {resp.text[:300]}"
            elif resp.status_code != 200:
                raise ExtractionError(f"HTTP {resp.status_code} (not retried): {resp.text[:500]}")
            else:
                return resp, url
        if attempt < len(RETRY_WAITS_S):
            wait = RETRY_WAITS_S[attempt]
            print(f"Attempt {attempt + 1} failed ({last_problem}); retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise ExtractionError(f"Giving up after {len(RETRY_WAITS_S) + 1} attempts. Last problem: {last_problem}")


def validate(body: bytes) -> dict:
    """Check the structure we depend on. Raises ExtractionError with a precise message."""
    if not body.strip():
        raise ExtractionError("Empty response body")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Response is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ExtractionError(f"Expected a JSON object, got {type(data).__name__}")
    if data.get("error"):
        raise ExtractionError(f"API reported an error: {data.get('reason')}")
    hourly = data.get("hourly")
    if not isinstance(hourly, dict):
        raise ExtractionError("Missing 'hourly' object in response")
    times, temps = hourly.get("time"), hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temps, list):
        raise ExtractionError("'hourly.time' and 'hourly.temperature_2m' must both be lists")
    if len(times) != len(temps):
        raise ExtractionError(f"Length mismatch: {len(times)} timestamps vs {len(temps)} values")
    if not times:
        raise ExtractionError("Empty series: the API returned zero hourly rows")
    bad = [t for t in times if not (isinstance(t, str) and TIME_RE.match(t))]
    if bad:
        raise ExtractionError(f"{len(bad)} timestamps do not look like yyyy-mm-ddThh:00, e.g. {bad[0]!r}")
    if (data.get("hourly_units") or {}).get("temperature_2m") != "°C":
        raise ExtractionError(f"Unexpected units: {data.get('hourly_units')}")
    if data.get("timezone") != "GMT" or data.get("utc_offset_seconds") != 0:
        raise ExtractionError(
            f"Expected timezone GMT and utc_offset_seconds 0, got {data.get('timezone')!r} / "
            f"{data.get('utc_offset_seconds')!r}"
        )
    return data


def write_new_file(path: Path, content: bytes) -> None:
    """Create `path` with `content`, but only if it does not exist yet.

    Two protections in one:
    - Exclusive create: os.link() fails if the destination exists, so a landing file can never be
      overwritten, even by two runs racing each other (the check and the creation are one step).
    - Atomic: the bytes are written under a temporary name first and only linked to the final
      name when complete, so a crash mid-write cannot leave a half file that looks finished.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(content)
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise ExtractionError(f"Refusing to overwrite an existing pull: {path}") from None
    finally:
        tmp.unlink()


SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # these go into file names, so keep them boring


def parse_extra_params(items: list[str], reserved: set[str]) -> dict:
    """Turn ['models=era5'] into {'models': 'era5'}, refusing anything ambiguous or unsafe."""
    extra = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not SAFE_TEXT_RE.match(key) or not SAFE_TEXT_RE.match(value):
            raise ExtractionError(f"--extra-param must look like KEY=VALUE (letters, digits, . _ -), got {item!r}")
        if key in reserved:
            raise ExtractionError(f"--extra-param cannot override the core parameter {key!r}; use its own option")
        if key in extra:
            raise ExtractionError(f"--extra-param {key!r} given twice")
        extra[key] = value
    return extra


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", required=True, help="first day, yyyy-mm-dd")
    parser.add_argument("--end", required=True, help="last day, yyyy-mm-dd")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="landing folder")
    parser.add_argument("--latitude", type=float, default=LATITUDE, help=f"default {LATITUDE}")
    parser.add_argument("--longitude", type=float, default=LONGITUDE, help=f"default {LONGITUDE}")
    parser.add_argument(
        "--cell-selection", choices=["land", "sea", "nearest"], default=FIXED_PARAMS["cell_selection"],
        help="how the API picks the grid cell (docs: default 'land')",
    )
    parser.add_argument(
        "--extra-param", action="append", default=[], metavar="KEY=VALUE",
        help="additional API query parameter, repeatable (e.g. models=era5). Cannot override the "
             "core parameters. Recorded in the filename and the metadata.",
    )
    args = parser.parse_args()

    try:
        start, end = parse_date(args.start), parse_date(args.end)
        if start > end:
            raise ExtractionError(f"--start {start} is after --end {end}")

        params = {
            **FIXED_PARAMS,
            "latitude": args.latitude,
            "longitude": args.longitude,
            "cell_selection": args.cell_selection,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        extra = parse_extra_params(args.extra_param, reserved=set(params))
        params.update(extra)
        pulled_at = datetime.now(timezone.utc)
        response, url = fetch(params)
        body = response.content  # the exact bytes received; we never re-serialise them
        data = validate(body)

        times, temps = data["hourly"]["time"], data["hourly"]["temperature_2m"]
        expected_rows = ((end - start).days + 1) * 24
        null_count = sum(1 for v in temps if v is None)

        # The name says what was asked for (location, non-default cell selection, dates) and when.
        cell = "" if args.cell_selection == FIXED_PARAMS["cell_selection"] else f"_cell-{args.cell_selection}"
        extras = "".join(f"_{key}-{value}" for key, value in extra.items())
        stem = (
            f"open-meteo_archive_{args.latitude}N_{args.longitude}E{cell}{extras}_{start:%Y%m%d}_{end:%Y%m%d}"
            # %f = microseconds (6 digits): two runs a fraction of a second apart get different names.
            f"_pulled-{pulled_at:%Y%m%dT%H%M%S%fZ}"
        )
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = out_dir / f"{stem}.json", out_dir / f"{stem}.meta.json"

        meta = {
            "script_version": SCRIPT_VERSION,
            "request_url": url,
            "request_params": params,
            "pulled_at_utc": pulled_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "http_status": response.status_code,
            "response_content_type": response.headers.get("Content-Type"),
            "response_bytes": len(body),
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "row_count": len(times),
            "expected_rows_for_requested_range": expected_rows,
            "null_value_count": null_count,
            "first_timestamp": times[0],
            "last_timestamp": times[-1],
            "api_reported": {
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "elevation": data.get("elevation"),
                "utc_offset_seconds": data.get("utc_offset_seconds"),
                "timezone": data.get("timezone"),
                "timezone_abbreviation": data.get("timezone_abbreviation"),
            },
            "weather_model": {
                "reported_by_api": None,
                "docs_say_default_is": "Best match (combines IFS HRES, ERA5 and ERA5-Land)",
                "note": "The response has no model field and the docs list no 'models' query "
                        "parameter, so no model was requested explicitly.",
            },
            "landing_file": data_path.name,
            "attribution": ATTRIBUTION,
        }
        if extra:
            meta["extra_params"] = extra
        if "models" in extra:
            meta["weather_model"] = {
                "requested": extra["models"],
                "reported_by_api": None,
                "note": "Requested through an extra 'models' query parameter. It is NOT documented on "
                        "the Open-Meteo archive docs page (only seen in third-party client code), so "
                        "check that the values differ from a pull without it: an unknown parameter "
                        "could be silently ignored.",
            }
        # Data file first, metadata last: a metadata file exists only for a complete pull.
        write_new_file(data_path, body)
        write_new_file(meta_path, (json.dumps(meta, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    except ExtractionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"saved {data_path}")
    print(f"saved {meta_path}")
    print(f"rows: {len(times):,} (expected {expected_rows:,}), nulls: {null_count}, "
          f"{times[0]} -> {times[-1]}")
    if len(times) != expected_rows:
        print("WARNING: row count differs from the requested range. Check the metadata before "
              "loading this file.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
