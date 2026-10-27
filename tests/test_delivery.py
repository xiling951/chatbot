import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore
from plugins.astrbot_plugin_companion_core.memory import MemoryStore
from plugins.astrbot_plugin_companion_core.scheduling import daily_slot


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.scope = self.memory.scope("web", "bot", "alice", "private")
        self.other = self.memory.scope("web", "bot", "bob", "private")
        self.core = DeliveryStore(self.memory)
        self.current = daily_slot(date(2026, 10, 26), 12, 0, "America/Tijuana")
        self.core.authorize(
            self.scope,
            "trusted-owner-target",
            True,
            self.current - timedelta(minutes=1),
        )
        self.counter = 0

    def tearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def create(self, **kwargs):
        self.counter += 1
        return self.core.create(
            self.memory.begin(self.scope, f"message-{self.counter}"),
            "本人明确提醒",
            kwargs.pop("next_run", self.current),
            kwargs.pop("expires", self.current + timedelta(hours=1)),
            self.current,
            key=kwargs.pop("key", f"task-{self.counter}"),
            **kwargs,
        )

    def queue(self, **kwargs):
        task_id = self.create(**kwargs)
        execution = next(
            e for e in self.core.begin_due(self.current) if e["task_id"] == task_id
        )
        return self.core.complete_generation(
            execution["id"], execution["content"], self.current
        )

    def row(self, outbox_id):
        return dict(
            self.memory.db.execute(
                "SELECT * FROM outbox WHERE id=?", (outbox_id,)
            ).fetchone()
        )

    def accept(self, outbox_id, current=None):
        current = current or self.current
        self.assertIsNotNone(self.core.claim_send(outbox_id, current, capability=True))
        self.core.finish_send(outbox_id, "accepted_by_platform", current)

    def test_unauthorized_subject_cannot_create_or_borrow_target(self):
        with self.assertRaises(ValueError):
            self.core.create(
                self.memory.begin(self.other, "other"),
                "私有提醒",
                self.current,
                self.current + timedelta(hours=1),
                self.current,
                key="same",
            )
        self.core.authorize(self.scope, "trusted-owner-target", False, self.current)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(
            self.memory.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0
        )

    def test_duplicate_wakeup_has_one_execution_and_one_candidate(self):
        first = self.create(key="stable-key")
        self.assertEqual(first, self.create(key="stable-key"))
        execution = self.core.begin_due(self.current)[0]
        self.assertEqual(self.core.begin_due(self.current), [])
        self.assertIsNotNone(
            self.core.complete_generation(execution["id"], "提醒", self.current)
        )
        self.assertIsNone(
            self.core.complete_generation(execution["id"], "重复提醒", self.current)
        )
        self.assertEqual(len(self.core.queued()), 1)

    def test_twenty_events_have_source_reason_authorization_and_result(self):
        for i in range(20):
            self.create(key=f"event-{i}")
        executions = self.core.begin_due(self.current)
        self.assertEqual(len(executions), 20)
        for execution in executions:
            self.assertEqual(len(execution["source"]), 64)
            self.assertEqual(execution["reason"], "latest_due_slot")
            self.assertEqual(execution["auth_revision"], 1)
            self.core.complete_generation(
                execution["id"], execution["content"], self.current
            )
        self.assertEqual(len({r["idempotency_key"] for r in self.core.queued()}), 20)
        self.assertEqual(
            self.memory.db.execute(
                "SELECT count(*) FROM executions WHERE state='succeeded'"
            ).fetchone()[0],
            20,
        )

    def test_quiet_hours_and_unverified_capability_do_not_reserve_quota(self):
        outbox = self.queue()
        self.assertIsNone(self.core.claim_send(outbox, self.current, capability=False))
        self.assertEqual(self.row(outbox)["reason"], "channel_not_verified")
        self.current = daily_slot(date(2026, 10, 26), 23, 0, "America/Tijuana")
        outbox = self.queue()
        self.assertIsNone(self.core.claim_send(outbox, self.current, capability=True))
        self.assertEqual(self.row(outbox)["reason"], "quiet_hours")
        self.assertEqual(
            self.memory.db.execute(
                "SELECT count(*) FROM ledger.submissions"
            ).fetchone()[0],
            0,
        )

    def test_daily_quota_is_atomic_and_delivery_observation_stays_unknown(self):
        for index in range(3):
            self.current += timedelta(seconds=2)
            self.core.record_response(self.scope, self.current)
            outbox = self.queue()
            if index < 2:
                self.accept(outbox)
            else:
                self.assertIsNone(
                    self.core.claim_send(outbox, self.current, capability=True)
                )
                self.assertEqual(self.row(outbox)["reason"], "local_daily_quota")
        self.assertEqual(self.row(outbox)["device_observation"], "not_observed")
        self.assertEqual(self.row(outbox)["read_state"], "unknown")

    def test_no_response_reduces_frequency_without_resetting_quota(self):
        self.accept(self.queue())
        self.current += timedelta(seconds=1)
        second = self.queue()
        self.assertIsNone(self.core.claim_send(second, self.current, capability=True))
        self.assertEqual(self.row(second)["reason"], "unanswered_cooldown")
        self.core.record_response(self.scope, self.current)
        self.accept(second)

    def test_local_midnight_resets_day_even_within_one_utc_day(self):
        self.current = daily_slot(date(2026, 10, 26), 23, 58, "America/Tijuana")
        self.core.authorize(
            self.scope,
            "trusted-owner-target",
            True,
            self.current - timedelta(minutes=1),
            quiet_start=2,
            quiet_end=3,
        )
        for _ in range(2):
            self.current += timedelta(seconds=1)
            self.core.record_response(self.scope, self.current)
            self.accept(self.queue())
        self.current = daily_slot(date(2026, 10, 27), 0, 1, "America/Tijuana")
        self.core.record_response(self.scope, self.current)
        self.accept(self.queue())
        days = [
            r[0]
            for r in self.memory.db.execute(
                "SELECT local_day FROM ledger.submissions ORDER BY submitted_at"
            )
        ]
        self.assertEqual(days, ["2026-10-26", "2026-10-26", "2026-10-27"])

    def test_candidate_and_missed_task_expiry_do_not_submit(self):
        outbox = self.queue(grace_seconds=1)
        self.assertIsNone(
            self.core.claim_send(
                outbox, self.current + timedelta(seconds=2), capability=True
            )
        )
        self.assertEqual(self.row(outbox)["state"], "expired")
        self.create(
            next_run=self.current - timedelta(hours=2),
            expires=self.current - timedelta(hours=1),
        )
        self.assertEqual(self.core.begin_due(self.current), [])
        self.assertEqual(
            self.memory.db.execute(
                "SELECT count(*) FROM executions WHERE state='expired'"
            ).fetchone()[0],
            1,
        )

    def test_revoke_during_generation_discards_late_result(self):
        self.create()
        execution = self.core.begin_due(self.current)[0]
        self.core.authorize(self.scope, "trusted-owner-target", False, self.current)
        self.assertIsNone(
            self.core.complete_generation(execution["id"], "晚到结果", self.current)
        )
        self.assertEqual(self.core.queued(), [])

    def test_revoke_after_queue_blocks_submission_and_future_tasks(self):
        outbox = self.queue()
        self.core.authorize(self.scope, "trusted-owner-target", False, self.current)
        self.assertIsNone(self.core.claim_send(outbox, self.current, capability=True))
        self.assertEqual(self.row(outbox)["state"], "cancelled")
        self.core.authorize(self.scope, "trusted-owner-target", True, self.current)
        self.assertIsNone(self.core.claim_send(outbox, self.current, capability=True))

    def test_revoke_in_flight_cannot_erase_platform_acceptance(self):
        outbox = self.queue()
        self.assertIsNotNone(
            self.core.claim_send(outbox, self.current, capability=True)
        )
        self.core.authorize(self.scope, "trusted-owner-target", False, self.current)
        self.core.finish_send(outbox, "accepted_by_platform", self.current)
        self.assertEqual(self.row(outbox)["state"], "accepted_by_platform")
        self.assertEqual(
            self.memory.db.execute("SELECT state FROM ledger.submissions").fetchone()[
                0
            ],
            "accepted_by_platform",
        )

    def test_unknown_keeps_quota_and_never_blindly_retries(self):
        outbox = self.queue()
        self.assertIsNotNone(
            self.core.claim_send(outbox, self.current, capability=True)
        )
        self.core.finish_send(outbox, "unknown", self.current)
        for seconds in (1, 31, 61):
            self.assertIsNone(
                self.core.claim_send(
                    outbox, self.current + timedelta(seconds=seconds), capability=True
                )
            )
        self.assertEqual(self.row(outbox)["state"], "unknown")
        self.assertEqual(
            self.memory.db.execute(
                "SELECT attempts FROM ledger.submissions"
            ).fetchone()[0],
            1,
        )

    def test_proven_rejection_releases_quota_with_bounded_backoff(self):
        outbox = self.queue()
        self.assertIsNotNone(
            self.core.claim_send(outbox, self.current, capability=True)
        )
        self.core.finish_send(outbox, "rejected", self.current)
        self.assertIsNone(
            self.core.claim_send(
                outbox, self.current + timedelta(seconds=29), capability=True
            )
        )
        self.current += timedelta(seconds=30)
        self.assertIsNotNone(
            self.core.claim_send(outbox, self.current, capability=True)
        )
        self.core.finish_send(outbox, "rejected", self.current)
        self.assertEqual(self.row(outbox)["state"], "failed")
        self.assertIsNone(
            self.core.claim_send(
                outbox, self.current + timedelta(seconds=60), capability=True
            )
        )
        self.assertEqual(
            self.memory.db.execute(
                "SELECT attempts FROM ledger.submissions"
            ).fetchone()[0],
            2,
        )
        self.accept(self.queue())

    def test_restart_recovers_expired_submission_as_unknown(self):
        outbox = self.queue()
        self.assertIsNotNone(
            self.core.claim_send(outbox, self.current, capability=True)
        )
        self.memory.close()
        self.memory = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.core = DeliveryStore(self.memory)
        self.core.recover(self.current + timedelta(seconds=61))
        self.assertEqual(self.row(outbox)["state"], "unknown")
        self.assertIsNone(
            self.core.claim_send(
                outbox, self.current + timedelta(seconds=61), capability=True
            )
        )

    def test_generation_lease_expiry_prevents_late_result(self):
        self.create()
        execution = self.core.begin_due(self.current)[0]
        self.core.recover(self.current + timedelta(seconds=61))
        self.assertIsNone(
            self.core.complete_generation(
                execution["id"], "迟到", self.current + timedelta(seconds=61)
            )
        )

    def test_interval_downtime_coalesces_to_one_recent_candidate(self):
        self.create(
            next_run=self.current - timedelta(days=3),
            rule={"kind": "interval", "seconds": 3600},
        )
        executions = self.core.begin_due(self.current)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0]["scheduled_at"], self.current.isoformat())
        self.assertEqual(self.core.begin_due(self.current), [])

    def test_memory_delete_cancels_candidates_and_old_backup_cannot_revive_them(self):
        outbox = self.queue()
        old = self.root / "old.sqlite"
        self.memory.backup(old)
        self.memory.forget(self.scope)
        self.assertEqual(self.row(outbox)["content"], "")
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertIsNone(
                DeliveryStore(restored).claim_send(
                    outbox, self.current, capability=True
                )
            )
            self.assertEqual(
                restored.db.execute("SELECT content FROM outbox").fetchone()[0], ""
            )
        finally:
            restored.close()

    def test_old_snapshot_cannot_resend_an_accepted_submission(self):
        outbox = self.queue()
        old = self.root / "old.sqlite"
        self.memory.backup(old)
        self.accept(outbox)
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertIsNone(
                DeliveryStore(restored).claim_send(
                    outbox, self.current, capability=True
                )
            )
            self.assertEqual(
                restored.db.execute("SELECT state FROM outbox").fetchone()[0],
                "accepted_by_platform",
            )
        finally:
            restored.close()

    def test_old_snapshot_cannot_restore_revoked_authorization(self):
        outbox = self.queue()
        old = self.root / "old.sqlite"
        self.memory.backup(old)
        self.core.authorize(self.scope, "trusted-owner-target", False, self.current)
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertIsNone(
                DeliveryStore(restored).claim_send(
                    outbox, self.current, capability=True
                )
            )
            self.assertEqual(
                restored.db.execute("SELECT state FROM outbox").fetchone()[0],
                "cancelled",
            )
        finally:
            restored.close()

    def test_independent_connections_cannot_both_claim_one_send(self):
        outbox = self.queue()

        def claim(_):
            memory = MemoryStore(
                self.root / "memory.sqlite", self.root / "latest.sqlite"
            )
            try:
                return (
                    DeliveryStore(memory).claim_send(
                        outbox, self.current, capability=True
                    )
                    is not None
                )
            finally:
                memory.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim, range(2))), 1)

    def test_competing_distinct_messages_cannot_exceed_daily_reservations(self):
        outbox_ids = [self.queue() for _ in range(3)]
        self.core.record_response(self.scope, self.current)

        def claim(outbox):
            memory = MemoryStore(
                self.root / "memory.sqlite", self.root / "latest.sqlite"
            )
            try:
                return (
                    DeliveryStore(memory).claim_send(
                        outbox, self.current, capability=True
                    )
                    is not None
                )
            finally:
                memory.close()

        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(sum(pool.map(claim, outbox_ids)), 2)

    def test_snapshot_before_execution_cannot_recreate_a_submitted_message(self):
        self.create()
        old = self.root / "old.sqlite"
        self.memory.backup(old)
        execution = self.core.begin_due(self.current)[0]
        outbox = self.core.complete_generation(execution["id"], "提醒", self.current)
        self.accept(outbox)
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            core = DeliveryStore(restored)
            execution = core.begin_due(self.current)[0]
            candidate = core.complete_generation(execution["id"], "提醒", self.current)
            self.assertIsNone(core.claim_send(candidate, self.current, capability=True))
        finally:
            restored.close()

    def test_snapshot_cannot_reset_exhausted_safe_retry_counter(self):
        outbox = self.queue()
        old = self.root / "old.sqlite"
        self.memory.backup(old)
        for _ in range(2):
            self.assertIsNotNone(
                self.core.claim_send(outbox, self.current, capability=True)
            )
            self.core.finish_send(outbox, "rejected", self.current)
            self.current += timedelta(seconds=30)
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertIsNone(
                DeliveryStore(restored).claim_send(
                    outbox, self.current, capability=True
                )
            )
            self.assertEqual(
                restored.db.execute(
                    "SELECT attempts FROM ledger.submissions"
                ).fetchone()[0],
                2,
            )
        finally:
            restored.close()

    def test_restore_preserves_acceptance_even_when_permission_was_revoked(self):
        outbox = self.queue()
        snapshot = self.root / "old.sqlite"
        self.memory.backup(snapshot)
        self.accept(outbox)
        self.core.authorize(self.scope, "trusted-owner-target", False, self.current)
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(
                restored.db.execute("SELECT state FROM outbox").fetchone()[0],
                "accepted_by_platform",
            )
            self.assertEqual(
                restored.db.execute("SELECT content FROM outbox").fetchone()[0], ""
            )
            self.assertIsNone(
                DeliveryStore(restored).claim_send(
                    outbox, self.current, capability=True
                )
            )
        finally:
            restored.close()
