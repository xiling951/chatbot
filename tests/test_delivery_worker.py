import asyncio
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore
from plugins.astrbot_plugin_companion_core.delivery_worker import (
    DeliveryWorker,
    DefinitelyNotAccepted,
)
from plugins.astrbot_plugin_companion_core.memory import MemoryStore
from plugins.astrbot_plugin_companion_core.scheduling import daily_slot


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.scope = self.memory.scope("web", "bot", "alice", "private")
        self.core = DeliveryStore(self.memory)
        self.current = daily_slot(date(2026, 10, 26), 12, 0, "America/Tijuana")
        self.enabled = True
        self.core.authorize(
            self.scope, "trusted-owner", True, self.current - timedelta(minutes=1)
        )
        self.core.create(
            self.memory.begin(self.scope, "reminder"),
            "本人提醒",
            self.current,
            self.current + timedelta(hours=1),
            self.current,
            key="reminder",
        )
        self.sender = AsyncMock(return_value="accepted_by_platform")
        self.worker = DeliveryWorker(
            self.core,
            enabled=lambda: self.enabled,
            capability=lambda row: True,
            sender=self.sender,
            clock=lambda: self.current,
        )

    async def asyncTearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def state(self):
        row = self.memory.db.execute("SELECT state FROM outbox").fetchone()
        return row[0] if row else None

    async def test_global_stop_blocks_generation_and_sending(self):
        self.enabled = False
        self.worker.generator = AsyncMock(return_value="不应生成")
        await self.worker.tick()
        self.worker.generator.assert_not_awaited()
        self.sender.assert_not_awaited()
        self.assertEqual(
            self.memory.db.execute("SELECT state FROM tasks").fetchone()[0], "cancelled"
        )

    async def test_normal_literal_reminder_uses_one_transport_call(self):
        await self.worker.tick()
        await self.worker.tick()
        self.sender.assert_awaited_once()
        self.assertEqual(self.sender.call_args.args[:2], ("trusted-owner", "本人提醒"))
        self.assertEqual(self.state(), "accepted_by_platform")

    async def test_timeout_unknown_never_retries_on_next_tick(self):
        self.sender.side_effect = TimeoutError("simulated uncertain transport")
        await self.worker.tick()
        self.current += timedelta(seconds=61)
        await self.worker.tick()
        self.sender.assert_awaited_once()
        self.assertEqual(self.state(), "unknown")

    async def test_proven_rejection_retries_at_most_once_after_backoff(self):
        self.sender.side_effect = DefinitelyNotAccepted(
            "simulated pre-submission rejection"
        )
        await self.worker.tick()
        self.current += timedelta(seconds=29)
        await self.worker.tick()
        self.assertEqual(self.sender.await_count, 1)
        self.current += timedelta(seconds=1)
        await self.worker.tick()
        self.current += timedelta(minutes=1)
        await self.worker.tick()
        self.assertEqual(self.sender.await_count, 2)
        self.assertEqual(self.state(), "failed")

    async def test_revocation_during_generation_discards_model_result(self):
        async def generate(execution):
            self.core.authorize(self.scope, "trusted-owner", False, self.current)
            return "晚到模型结果"

        self.worker.generator = generate
        await self.worker.tick()
        self.sender.assert_not_awaited()
        self.assertIsNone(self.state())

    async def test_global_stop_during_generation_discards_result(self):
        async def generate(execution):
            self.enabled = False
            return "晚到模型结果"

        self.worker.generator = generate
        await self.worker.tick()
        self.sender.assert_not_awaited()
        self.assertIsNone(self.state())

    async def test_deletion_during_generation_discards_derived_content(self):
        async def generate(execution):
            self.memory.forget(self.scope)
            return "含被删事实的模型结果"

        self.worker.generator = generate
        await self.worker.tick()
        self.sender.assert_not_awaited()
        self.assertIsNone(self.state())

    async def test_stop_after_reservation_prevents_network_submission(self):
        original = self.core.claim_send

        def claim(*args, **kwargs):
            result = original(*args, **kwargs)
            self.enabled = False
            return result

        self.core.claim_send = claim
        await self.worker.tick()
        self.sender.assert_not_awaited()
        self.assertEqual(
            self.memory.db.execute("SELECT state FROM ledger.submissions").fetchone()[
                0
            ],
            "failed",
        )
        self.assertEqual(self.state(), "cancelled")

    async def test_already_in_flight_acceptance_is_recorded_after_revocation(self):
        async def sender(*args):
            self.core.authorize(self.scope, "trusted-owner", False, self.current)
            return "accepted_by_platform"

        self.worker.sender = sender
        await self.worker.tick()
        self.assertEqual(self.state(), "accepted_by_platform")
        self.assertEqual(
            self.memory.db.execute("SELECT enabled FROM ledger.proactive").fetchone()[
                0
            ],
            0,
        )

    async def test_unverified_channel_produces_no_submission(self):
        self.worker.capability = lambda row: False
        await self.worker.tick()
        self.sender.assert_not_awaited()
        self.assertEqual(self.state(), "queued")
        self.assertEqual(
            self.memory.db.execute(
                "SELECT count(*) FROM ledger.submissions"
            ).fetchone()[0],
            0,
        )

    async def test_competing_ticks_do_not_generate_or_send_twice(self):
        async def generate(execution):
            await asyncio.sleep(0)
            return execution["content"]

        self.worker.generator = AsyncMock(side_effect=generate)
        await asyncio.gather(self.worker.tick(), self.worker.tick())
        self.worker.generator.assert_awaited_once()
        self.sender.assert_awaited_once()

    async def test_second_long_running_loop_cannot_acquire_owner_lock(self):
        first_stop = asyncio.Event()
        second_stop = asyncio.Event()
        started = asyncio.Event()

        async def first_tick():
            started.set()

        self.worker.tick = first_tick
        other = DeliveryWorker(
            self.core,
            enabled=lambda: True,
            capability=lambda row: False,
            sender=self.sender,
            clock=lambda: self.current,
        )
        first = asyncio.create_task(self.worker.run(first_stop, poll_seconds=0.05))
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.assertFalse(await other.run(second_stop, poll_seconds=0.05))
        finally:
            first_stop.set()
            self.assertTrue(await first)
