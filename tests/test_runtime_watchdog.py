import os
import tempfile
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime_watchdog import (
    RecoveryPolicy,
    process_start_ticks,
    prune_owned_logs,
    save_state,
    run_once,
    read_json,
)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 26, tzinfo=timezone.utc)

    def test_manual_stop_blocks_recovery(self):
        policy = RecoveryPolicy()
        self.assertEqual(
            policy.decide(
                {"process_alive": False, "dashboard_http_ok": False}, self.current, True
            ),
            "stopped_by_user",
        )
        self.assertEqual(policy.state["restart_times"], [])

    def test_dead_process_starts_but_live_unhealthy_waits_three_observations(self):
        policy = RecoveryPolicy()
        self.assertEqual(
            policy.decide(
                {"process_alive": False, "dashboard_http_ok": False},
                self.current,
                False,
            ),
            "start",
        )
        sample = {"process_alive": True, "dashboard_http_ok": False}
        self.assertEqual(
            [policy.decide(sample, self.current, False) for _ in range(3)],
            ["wait_for_local_health", "wait_for_local_health", "restart_owned"],
        )

    def test_restart_budget_survives_policy_recreation_and_eventually_recovers(self):
        policy = RecoveryPolicy()
        sample = {"process_alive": False, "dashboard_http_ok": False}
        for _ in range(3):
            self.assertEqual(policy.decide(sample, self.current, False), "start")
        restored = RecoveryPolicy(policy.state)
        self.assertEqual(
            restored.decide(sample, self.current, False), "restart_backoff"
        )
        self.assertEqual(
            restored.decide(sample, self.current + timedelta(minutes=16), False),
            "start",
        )

    def test_dashboard_health_does_not_require_or_claim_model_probe(self):
        policy = RecoveryPolicy({"failures": 2, "restart_times": []})
        self.assertEqual(
            policy.decide(
                {"process_alive": True, "dashboard_http_ok": True}, self.current, False
            ),
            "healthy",
        )
        self.assertEqual(policy.state["failures"], 0)

    def test_retention_only_removes_our_old_exactly_named_files(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            old = folder / "availability-2026-10-01.local.jsonl"
            new = folder / "availability-2026-10-25.local.jsonl"
            other = folder / "user-notes.log"
            for p in (old, new, other):
                p.write_text("test")
            self.assertEqual(
                prune_owned_logs(folder, self.current, kind="availability"), 1
            )
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertTrue(other.exists())

    def test_state_replacement_preserves_previous_value_on_write_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            save_state(path, {"restart_times": ["first"]})
            with patch(
                "runtime_watchdog.os.replace", side_effect=OSError("simulated failure")
            ):
                with self.assertRaises(OSError):
                    save_state(path, {"restart_times": []})
            self.assertEqual(read_json(path), {"restart_times": ["first"]})
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_failed_durable_budget_write_prevents_launch(self):
        sample = {"process_alive": False, "dashboard_http_ok": False}
        with (
            patch("runtime_watchdog.observe", return_value=sample),
            patch(
                "runtime_watchdog.save_state",
                side_effect=OSError("simulated full disk"),
            ),
            patch("runtime_watchdog.recover") as launch,
        ):
            with self.assertRaises(OSError):
                run_once(
                    RecoveryPolicy(),
                    allow_recovery=True,
                    state_path=Path("not-used.json"),
                )
            launch.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows process identity API")
    def test_windows_creation_ticks_are_present_for_current_process(self):
        ticks = process_start_ticks(os.getpid())
        self.assertIsInstance(ticks, int)
        self.assertGreater(ticks, 0)
        self.assertIsNone(process_start_ticks(0))
