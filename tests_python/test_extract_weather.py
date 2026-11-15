"""Offline tests for scripts/extract_weather.py (no network: the API call is replaced by a fake).

Run from the repository root:
    .venv/bin/python -m unittest discover -s tests_python -v
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "extract_weather.py"
spec = importlib.util.spec_from_file_location("extract_weather", SCRIPT)
extract_weather = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract_weather)


def fake_body(hours=48, **overrides) -> bytes:
    """A minimal API answer with the structure the script validates."""
    body = {
        "latitude": 48.82, "longitude": 2.28, "elevation": 26.0,
        "generationtime_ms": 0.02, "utc_offset_seconds": 0, "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {
            "time": [f"2024-01-{1 + h // 24:02d}T{h % 24:02d}:00" for h in range(hours)],
            "temperature_2m": [5.0] * hours,
        },
    }
    body.update(overrides)
    return json.dumps(body).encode()


def run_script(out_dir, body=None, now=None):
    """Run main() with a faked API call. Returns (exit_code, stdout, stderr)."""
    response = SimpleNamespace(content=body or fake_body(), status_code=200,
                               headers={"Content-Type": "application/json"})
    argv = ["extract_weather.py", "--start", "2024-01-01", "--end", "2024-01-02", "--out-dir", str(out_dir)]
    out, err = io.StringIO(), io.StringIO()
    patches = [mock.patch.object(extract_weather, "fetch", return_value=(response, "https://fake/url")),
               mock.patch.object(sys, "argv", argv)]
    if now is not None:
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        patches.append(mock.patch.object(extract_weather, "datetime", FrozenDatetime))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        code = extract_weather.main()
    return code, out.getvalue(), err.getvalue()


class ExtractWeatherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def data_files(self):
        return sorted(p for p in self.out_dir.glob("*.json") if not p.name.endswith(".meta.json"))

    def test_two_back_to_back_runs_both_succeed_with_different_filenames(self):
        code1, _, _ = run_script(self.out_dir)
        code2, _, _ = run_script(self.out_dir)  # immediately after, no sleep
        self.assertEqual((code1, code2), (0, 0))
        files = self.data_files()
        self.assertEqual(len(files), 2)
        self.assertNotEqual(files[0].name, files[1].name)
        self.assertEqual(len(list(self.out_dir.glob("*.meta.json"))), 2)
        self.assertEqual(list(self.out_dir.glob("*.tmp")), [])  # no temporary files left behind

    def test_filename_has_microseconds(self):
        run_script(self.out_dir, now=datetime(2026, 11, 15, 19, 49, 41, 123456, tzinfo=timezone.utc))
        self.assertIn("pulled-20261115T194941123456Z", self.data_files()[0].name)

    def test_refuses_to_overwrite_an_existing_pull(self):
        same_instant = datetime(2026, 11, 15, 19, 49, 41, 123456, tzinfo=timezone.utc)
        self.assertEqual(run_script(self.out_dir, now=same_instant)[0], 0)
        first_bytes = self.data_files()[0].read_bytes()
        code, _, err = run_script(self.out_dir, body=fake_body(hours=24), now=same_instant)
        self.assertEqual(code, 1)
        self.assertIn("Refusing to overwrite", err)
        self.assertEqual(self.data_files()[0].read_bytes(), first_bytes)  # original untouched
        self.assertEqual(len(self.data_files()), 1)

    def test_saved_file_is_the_exact_response_bytes(self):
        body = fake_body()
        run_script(self.out_dir, body=body)
        self.assertEqual(self.data_files()[0].read_bytes(), body)

    def test_api_error_object_fails_loudly(self):
        code, _, err = run_script(self.out_dir, body=json.dumps({"error": True, "reason": "bad"}).encode())
        self.assertEqual(code, 1)
        self.assertIn("API reported an error: bad", err)
        self.assertEqual(self.data_files(), [])  # nothing is written on failure

    def test_empty_series_fails_loudly(self):
        code, _, err = run_script(self.out_dir, body=fake_body(hours=0))
        self.assertEqual(code, 1)
        self.assertIn("Empty series", err)


if __name__ == "__main__":
    unittest.main()
