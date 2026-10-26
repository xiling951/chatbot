import unittest
from datetime import date, datetime, timedelta, timezone

from plugins.astrbot_plugin_companion_core.scheduling import (
    daily_slot,
    due_slot,
    in_quiet_hours,
    utc,
)

ZONE = "America/Tijuana"


class SchedulingTests(unittest.TestCase):
    def test_missing_time_moves_to_first_valid_minute(self):
        self.assertEqual(
            daily_slot(date(2026, 3, 8), 2, 30, ZONE),
            datetime(2026, 3, 8, 10, tzinfo=timezone.utc),
        )

    def test_repeated_time_uses_first_fold_only(self):
        first = daily_slot(date(2026, 11, 1), 1, 30, ZONE)
        self.assertEqual(first, datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc))
        latest, following = due_slot(
            first,
            first + timedelta(hours=1),
            {"kind": "daily", "hour": 1, "minute": 30},
            ZONE,
        )
        self.assertEqual(latest, first)
        self.assertEqual(following, datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc))

    def test_daily_downtime_coalesces_latest_local_occurrence(self):
        first = daily_slot(date(2026, 10, 20), 12, 0, ZONE)
        current = daily_slot(date(2026, 10, 26), 13, 0, ZONE)
        latest, following = due_slot(
            first, current, {"kind": "daily", "hour": 12, "minute": 0}, ZONE
        )
        self.assertEqual(latest, daily_slot(date(2026, 10, 26), 12, 0, ZONE))
        self.assertEqual(following, daily_slot(date(2026, 10, 27), 12, 0, ZONE))

    def test_interval_is_elapsed_time_across_dst(self):
        first = daily_slot(date(2026, 3, 7), 12, 0, ZONE)
        current = first + timedelta(days=3, minutes=3)
        latest, following = due_slot(
            first, current, {"kind": "interval", "seconds": 86400}, ZONE
        )
        self.assertEqual(latest, first + timedelta(days=3))
        self.assertEqual(following, first + timedelta(days=4))

    def test_quiet_hours_follow_local_date_and_end_boundary(self):
        for hour, expected in [
            (22, False),
            (23, True),
            (0, True),
            (7, True),
            (8, False),
        ]:
            with self.subTest(hour=hour):
                self.assertEqual(
                    in_quiet_hours(daily_slot(date(2026, 10, 26), hour, 0, ZONE), ZONE),
                    expected,
                )

    def test_once_future_and_aware_time_validation(self):
        start = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.assertEqual(
            due_slot(start, start - timedelta(seconds=1), {"kind": "once"}, ZONE),
            (None, start),
        )
        self.assertEqual(
            due_slot(start, start + timedelta(seconds=1), {"kind": "once"}, ZONE),
            (start, None),
        )
        with self.assertRaises(ValueError):
            utc(datetime(2026, 10, 26))
        with self.assertRaises(ValueError):
            due_slot(start, start, {"kind": "interval", "seconds": True}, ZONE)
