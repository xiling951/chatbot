"""Clock-boundary logic only; simulated dates never count as a real-day trial."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from memory_crossday import due


class CrossdayClockTests(unittest.TestCase):
    def test_crossing_beijing_midnight_does_not_skip_24_hours(self):
        seeded = datetime(2026, 10, 29, 15, 59, tzinfo=timezone.utc)
        protocol = {"seeded_at": seeded.isoformat()}
        self.assertFalse(due(protocol, seeded + timedelta(minutes=2)))
        self.assertFalse(due(protocol, seeded + timedelta(hours=24, microseconds=-1)))
        self.assertTrue(due(protocol, seeded + timedelta(hours=24)))

    def test_utc_offset_does_not_change_elapsed_time(self):
        seeded = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)
        protocol = {
            "seeded_at": seeded.astimezone(timezone(timedelta(hours=8))).isoformat()
        }
        self.assertFalse(due(protocol, seeded - timedelta(seconds=1)))
        self.assertTrue(due(protocol, seeded + timedelta(days=2)))

    def test_naive_clock_is_not_accepted_as_elapsed_time_evidence(self):
        with self.assertRaises(ValueError):
            due({"seeded_at": "2026-10-29T15:00:00"}, datetime.now(timezone.utc))
        with self.assertRaises(ValueError):
            due({"seeded_at": "2026-10-29T15:00:00+00:00"}, datetime(2026, 10, 31))


if __name__ == "__main__":
    unittest.main()
