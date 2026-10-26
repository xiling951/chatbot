import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from plugins.astrbot_plugin_companion_core.call_policy import reserve_attempt


class CallPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "allowance.local.json"
        self.record = {
            "mode": "unlimited_testing",
            "attempts_used": 100,
            "authorized_date": "2000-01-01",
            "remaining_calls": 0,
            "reserved_cny_remaining": 0,
        }
        self.path.write_text(json.dumps(self.record))

    def tearDown(self):
        self.temp.cleanup()

    def test_unlimited_ignores_superseded_count_date_and_money(self):
        self.assertEqual(reserve_attempt(self.path, 100), 101)
        self.assertEqual(reserve_attempt(self.path, 100), 102)
        self.assertEqual(json.loads(self.path.read_text())["remaining_calls"], 0)

    def test_concurrent_attempt_records_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            numbers = list(
                pool.map(lambda _: reserve_attempt(self.path, 100), range(20))
            )
        self.assertEqual(sorted(numbers), list(range(101, 121)))
        self.assertEqual(json.loads(self.path.read_text())["attempts_used"], 120)

    def test_input_guard_does_not_spend_attempt(self):
        with self.assertRaises(ValueError):
            reserve_attempt(self.path, 13001)
        self.assertEqual(json.loads(self.path.read_text())["attempts_used"], 100)

    def test_missing_explicit_unlimited_authorization_remains_finite(self):
        self.record.pop("mode")
        self.path.write_text(json.dumps(self.record))
        with self.assertRaises(ValueError):
            reserve_attempt(self.path, 100)
