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
    controlled_platforms,
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

    def channel_sample(self, status="error"):
        return {
            "process_alive": True,
            "dashboard_http_ok": True,
            "channels_observed": True,
            "channels": {"companion-qq": {"status": status}},
        }

    def test_qq_adapter_failure_waits_three_samples_and_not_host_restart(self):
        policy = RecoveryPolicy()
        sample = self.channel_sample()
        actions = [
            policy.decide_channels(sample, self.current, False)["companion-qq"]
            for _ in range(3)
        ]
        self.assertEqual(
            actions,
            [
                "wait_for_channel_health",
                "wait_for_channel_health",
                "reload_owned_adapter",
            ],
        )
        self.assertEqual(policy.decide(sample, self.current, False), "healthy")
        self.assertEqual(policy.state["restart_times"], [])

    def test_channel_budget_persists_and_stop_unknown_disable_interrupt_consecutive_failures(
        self,
    ):
        policy = RecoveryPolicy()
        sample = self.channel_sample()
        for _ in range(9):
            policy.decide_channels(sample, self.current, False)
        policy = RecoveryPolicy(policy.state)
        for _ in range(3):
            action = policy.decide_channels(sample, self.current, False)["companion-qq"]
        self.assertEqual(action, "channel_reload_backoff")
        self.assertEqual(policy.decide_channels(sample, self.current, True), {})
        self.assertEqual(
            len(policy.state["channels"]["companion-qq"]["reload_times"]), 3
        )
        self.assertEqual(
            policy.decide_channels(sample, self.current + timedelta(minutes=16), False)[
                "companion-qq"
            ],
            "wait_for_channel_health",
        )
        policy.decide_channels(sample, self.current + timedelta(minutes=16), False)
        policy.decide_channels(
            {
                "process_alive": True,
                "dashboard_http_ok": True,
                "channels_observed": False,
            },
            self.current,
            False,
        )
        self.assertEqual(
            policy.decide_channels(sample, self.current + timedelta(minutes=16), False)[
                "companion-qq"
            ],
            "wait_for_channel_health",
        )
        policy.decide_channels(
            {
                "process_alive": True,
                "dashboard_http_ok": True,
                "channels_observed": True,
                "channels": {},
            },
            self.current,
            False,
        )
        self.assertEqual(policy.state["channels"]["companion-qq"]["failures"], 0)

    def test_running_channel_with_old_error_does_not_trigger_reload(self):
        sample = self.channel_sample("running")
        sample["channels"]["companion-qq"]["has_reported_error"] = True
        policy = RecoveryPolicy()
        self.assertTrue(
            all(
                policy.decide_channels(sample, self.current, False)["companion-qq"]
                == "running_reported"
                for _ in range(10)
            )
        )

    def test_controlled_channels_reject_other_instances_missing_owner_and_changed_app(
        self,
    ):
        config = {
            "platform": [
                {
                    "id": "companion-qq",
                    "type": "qq_official",
                    "enable": True,
                    "appid": "app",
                },
                {
                    "id": "other-bot",
                    "type": "qq_official",
                    "enable": True,
                    "appid": "other",
                },
            ]
        }
        settings = {
            "allowed_senders": {"companion-qq": ["owner"], "other-bot": ["someone"]},
            "bot_account_ids": {"companion-qq": "app", "other-bot": "other"},
        }
        self.assertEqual(
            controlled_platforms(config, settings), {"companion-qq": "qq_official"}
        )
        settings["bot_account_ids"]["companion-qq"] = "changed"
        self.assertEqual(controlled_platforms(config, settings), {})
        settings["bot_account_ids"]["companion-qq"] = "app"
        settings["allowed_senders"]["companion-qq"] = []
        self.assertEqual(controlled_platforms(config, settings), {})

    def test_channel_reload_requires_durable_budget_and_once_never_mutates(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("runtime_watchdog.observe", return_value=self.channel_sample()),
            patch("runtime_watchdog.reload_adapter") as reload,
        ):
            policy = RecoveryPolicy(
                {
                    "failures": 0,
                    "restart_times": [],
                    "channels": {"companion-qq": {"failures": 2, "reload_times": []}},
                }
            )
            with patch(
                "runtime_watchdog.save_state", side_effect=OSError("disk unavailable")
            ):
                with self.assertRaises(OSError):
                    run_once(
                        policy,
                        allow_recovery=True,
                        state_path=Path(temp) / "state.json",
                    )
            reload.assert_not_called()
            policy = RecoveryPolicy(
                {
                    "failures": 0,
                    "restart_times": [],
                    "channels": {"companion-qq": {"failures": 2, "reload_times": []}},
                }
            )
            result = run_once(
                policy, allow_recovery=False, state_path=Path(temp) / "state.json"
            )
            self.assertNotIn("channel_recovery", result)
            self.assertFalse((Path(temp) / "state.json").exists())
            reload.assert_not_called()

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
