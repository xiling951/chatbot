"""Exercise the real grant script against isolated files, without remote calls."""

import contextlib
import io
import json
import runpy
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo


class AllowanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.home = self.data / "plugin_data" / "companion_core"
        self.home.mkdir(parents=True)
        self.path = self.home / "allowance.local.json"
        self.initial = {
            "authorized_date": datetime.now(ZoneInfo("America/Tijuana"))
            .date()
            .isoformat(),
            "remaining_calls": 0,
            "reserved_cny_remaining": 0,
            "reservation_per_call_cny": 0.2,
        }
        self.path.write_text(json.dumps(self.initial))
        (self.home / "settings.local.json").write_text('{"model_enabled": false}')
        (self.data / "cmd_config.json").write_text(
            json.dumps(
                {
                    "provider": [
                        {
                            "id": "companion-deepseek",
                            "provider_source_id": "official",
                            "model": "deepseek-flash",
                            "custom_extra_body": {
                                "thinking": {"type": "disabled"},
                                "max_tokens": 512,
                            },
                        }
                    ],
                    "provider_sources": [
                        {
                            "id": "official",
                            "api_base": "https://api.deepseek.com",
                            "request_max_retries": 1,
                        }
                    ],
                }
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def grant(self, *args):
        script = Path(__file__).resolve().parents[1] / "scripts/authorize_test.py"
        with (
            patch.dict(
                sys.modules, {"local_paths": SimpleNamespace(PLUGIN_DATA=self.home)}
            ),
            patch.object(sys, "argv", [str(script), *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(script), run_name="__main__")

    def test_explicit_extension_preserves_old_attempts(self):
        self.grant("--extend", "--calls", "60", "--daily-cny", "5")
        current = json.loads(self.path.read_text())
        self.assertEqual(current["attempts_used"], 25)
        self.assertEqual(current["remaining_calls"], 60)
        self.assertEqual(current["reserved_cny_remaining"], 3.75)

    def test_no_silent_refill(self):
        with self.assertRaises(SystemExit):
            self.grant("--calls", "25", "--daily-cny", "5")
        self.assertEqual(json.loads(self.path.read_text()), self.initial)

    def test_extension_cannot_overspend_day(self):
        self.initial.update(attempts_used=76, remaining_calls=9, daily_limit_cny=5)
        self.path.write_text(json.dumps(self.initial))
        with self.assertRaises(SystemExit):
            self.grant("--extend", "--calls", "20", "--daily-cny", "5")
        self.assertEqual(json.loads(self.path.read_text()), self.initial)

    def test_lower_daily_cap_is_not_increased(self):
        self.initial.update(daily_limit_cny=3)
        self.path.write_text(json.dumps(self.initial))
        self.grant("--extend", "--calls", "10", "--daily-cny", "5")
        self.assertEqual(json.loads(self.path.read_text())["daily_limit_cny"], 3)

    def test_explicit_unlimited_preserves_attempt_history_and_removes_caps(self):
        self.initial.update(attempts_used=100)
        self.path.write_text(json.dumps(self.initial))
        with self.assertRaises(SystemExit) as outcome:
            self.grant("--unlimited-testing")
        self.assertEqual(outcome.exception.code, 0)
        current = json.loads(self.path.read_text())
        self.assertEqual(current["mode"], "unlimited_testing")
        self.assertEqual(current["attempts_used"], 100)
        self.assertNotIn("remaining_calls", current)
        self.assertNotIn("daily_limit_cny", current)
        self.assertIn("superseded_limits", current)

    def test_unlimited_cannot_be_implicitly_combined_with_finite_grant(self):
        with self.assertRaises(SystemExit):
            self.grant("--unlimited-testing", "--calls", "25")
        self.assertEqual(json.loads(self.path.read_text()), self.initial)
