import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from availability_report import summarize


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 27, tzinfo=timezone.utc)

    def sample(self, second, **changes):
        return dict(
            at=(self.start + timedelta(seconds=second)).isoformat(),
            process_alive=True,
            dashboard_http_ok=True,
            channel=dict(
                adapter_running=True, configured=True, has_reported_error=False
            ),
            model_probe="not_run",
            **changes,
        )

    def test_missing_slots_unknown_and_future_unscored(self):
        result = summarize(
            [self.sample(0), self.sample(90)],
            self.start + timedelta(seconds=75),
            start=self.start,
        )
        self.assertEqual(result["completed_slots"], 2)
        self.assertEqual(result["not_elapsed_slots"], 8638)
        self.assertEqual(result["layers"]["process"]["healthy"], 1)
        self.assertEqual(result["layers"]["process"]["unknown"], 1)
        self.assertEqual(result["layers"]["process"]["minimum_available_percent"], 50)
        self.assertEqual(result["layers"]["process"]["maximum_available_percent"], 100)
        self.assertIsNone(result["model_available_percent"])

    def test_duplicate_and_recovery_in_same_slot_use_worst(self):
        failed = self.sample(1)
        failed["process_alive"] = False
        failed["after_recovery"] = self.sample(25)
        result = summarize(
            [failed, self.sample(29), self.sample(30)],
            self.start + timedelta(seconds=60),
            start=self.start,
        )
        for layer in ("process", "adapter_report"):
            self.assertEqual(result["layers"][layer]["failed"], 1)
            self.assertEqual(result["layers"][layer]["healthy"], 1)

    def test_window_right_edge_excluded_and_complete_not_automatically_passed(self):
        result = summarize(
            [self.sample(0), self.sample(72 * 3600)],
            self.start + timedelta(hours=73),
            start=self.start,
        )
        self.assertEqual(result["window_status"], "complete")
        self.assertEqual(result["completed_slots"], 8640)
        self.assertEqual(result["layers"]["process"]["unknown"], 8639)

    def test_invalid_timestamp_and_unknown_adapter_do_not_become_healthy(self):
        unknown = self.sample(0)
        unknown["channel"]["has_reported_error"] = None
        result = summarize(
            [unknown, {"at": "bad"}, {"at": "2026-10-27T00:00:01"}],
            self.start + timedelta(seconds=30),
            start=self.start,
        )
        self.assertEqual(result["invalid_timestamps"], 2)
        self.assertEqual(result["layers"]["process"]["healthy"], 1)
        self.assertEqual(result["layers"]["adapter_report"]["unknown"], 1)

    def test_legacy_weixin_success_does_not_backfill_qq_health(self):
        row = self.sample(0)
        result = summarize([row], self.start + timedelta(seconds=30), start=self.start)
        self.assertEqual(result["layers"]["adapter_report"]["healthy"], 1)
        self.assertEqual(result["layers"]["qq_adapter_report"]["unknown"], 1)

    def test_qq_failure_does_not_change_recorded_weixin_success(self):
        row = self.sample(0)
        row["channels"] = {
            "companion-qq": {
                "adapter_running": False,
                "configured": True,
                "has_reported_error": True,
            }
        }
        result = summarize([row], self.start + timedelta(seconds=30), start=self.start)
        self.assertEqual(result["layers"]["adapter_report"]["healthy"], 1)
        self.assertEqual(result["layers"]["qq_adapter_report"]["failed"], 1)
