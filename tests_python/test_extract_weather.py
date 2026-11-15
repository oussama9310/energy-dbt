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


def run_script(out_dir, body=None, now=None, extra=(), models=None):
    """Run main() with a faked API call. Returns (exit_code, stdout, stderr)."""
    response = SimpleNamespace(content=body or fake_body(), status_code=200,
                               headers={"Content-Type": "application/json"})
    argv = ["extract_weather.py", "--start", "2024-01-01", "--end", "2024-01-02", "--out-dir", str(out_dir)]
    if models is not None:
        argv += ["--models", models]
    for item in extra:
        argv += ["--extra-param", item]
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

    def meta(self):
        return json.loads(next(self.out_dir.glob("*.meta.json")).read_text())

    def test_models_is_pinned_to_ecmwf_ifs_by_default_and_recorded(self):
        self.assertEqual(run_script(self.out_dir)[0], 0)
        meta = self.meta()
        self.assertEqual(meta["request_params"]["models"], "ecmwf_ifs")
        self.assertEqual(meta["weather_model"]["requested"], "ecmwf_ifs")
        self.assertIn("_models-ecmwf_ifs_", self.data_files()[0].name)

    def test_the_real_request_url_contains_the_pinned_model(self):
        # Not mocking fetch(): only the network send is replaced, so the URL is built by the real code.
        sent = []
        fake = SimpleNamespace(content=fake_body(), status_code=200, headers={"Content-Type": "application/json"}, text="")

        def fake_send(session, prepared, **kwargs):
            sent.append(prepared.url)
            return fake

        argv = ["extract_weather.py", "--start", "2024-01-01", "--end", "2024-01-02", "--out-dir", str(self.out_dir)]
        with mock.patch("requests.Session.send", fake_send), mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(extract_weather.main(), 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("models=ecmwf_ifs", sent[0])
        for pinned in ("timezone=GMT", "temperature_unit=celsius", "timeformat=iso8601", "cell_selection=land"):
            self.assertIn(pinned, sent[0])
        self.assertEqual(self.meta()["request_url"], sent[0])  # the recorded URL is the URL that was sent

    def test_models_can_be_overridden_explicitly(self):
        self.assertEqual(run_script(self.out_dir, models="era5")[0], 0)
        self.assertEqual(self.meta()["request_params"]["models"], "era5")
        self.assertIn("_models-era5_", self.data_files()[0].name)

    def test_extra_param_is_sent_and_recorded(self):
        self.assertEqual(run_script(self.out_dir, extra=["wind_speed_unit=ms"])[0], 0)
        self.assertIn("_wind_speed_unit-ms_", self.data_files()[0].name)
        meta = self.meta()
        self.assertEqual(meta["request_params"]["wind_speed_unit"], "ms")
        self.assertEqual(meta["extra_params"], {"wind_speed_unit": "ms"})

    def test_extra_param_cannot_override_core_or_be_malformed(self):
        for bad in ("latitude=1", "start_date=2000-01-01", "models=era5", "wind_speed_unit", "mo dels=x", "foo=a/b"):
            code, _, err = run_script(self.out_dir, extra=[bad])
            self.assertEqual(code, 1, bad)
            self.assertIn("--extra-param", err)
        self.assertEqual(self.data_files(), [])

    def test_unsafe_models_value_is_rejected(self):
        code, _, err = run_script(self.out_dir, models="era5/../x")
        self.assertEqual(code, 1)
        self.assertIn("--models", err)

    def test_empty_series_fails_loudly(self):
        code, _, err = run_script(self.out_dir, body=fake_body(hours=0))
        self.assertEqual(code, 1)
        self.assertIn("Empty series", err)


if __name__ == "__main__":
    unittest.main()
