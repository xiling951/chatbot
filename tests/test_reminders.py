import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore
from plugins.astrbot_plugin_companion_core.memory import MemoryStore
from plugins.astrbot_plugin_companion_core.reminders import handle_reminder_command
from plugins.astrbot_plugin_companion_core.scheduling import daily_slot


class ReminderTests(unittest.TestCase):
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
        self.defaults = {
            "timezone": "America/Tijuana",
            "maximum_per_local_day": 2,
            "quiet_start_hour": 23,
            "quiet_end_hour": 8,
            "default_grace_seconds": 300,
        }

    def tearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def command(self, text, **kwargs):
        return handle_reminder_command(
            text,
            self.core,
            scope=kwargs.pop("scope", self.scope),
            target="trusted-event-target",
            source=kwargs.pop("source", "message"),
            current=kwargs.pop("current", self.current),
            enabled=kwargs.pop("enabled", True),
            capable=kwargs.pop("capable", True),
            private=kwargs.pop("private", True),
            defaults=self.defaults,
            **kwargs,
        )

    def test_disabled_or_unverified_does_not_authorize_or_schedule(self):
        self.assertIn("没有安排", self.command("/允许主动", enabled=False))
        self.assertIn("没有安排", self.command("/允许主动", capable=False))
        self.assertEqual(
            self.memory.db.execute("SELECT count(*) FROM ledger.proactive").fetchone()[
                0
            ],
            0,
        )
        self.assertIn("本人私聊", self.command("/允许主动", private=False))
        self.assertIsNone(self.command("正常聊天"))

    def test_explicit_consent_and_future_local_time_are_required(self):
        self.assertIn("尚未获得", self.command("/提醒 2026-10-26 13:00 喝水"))
        self.assertIn("已允许", self.command("/允许主动"))
        self.assertIn("已经过去", self.command("/提醒 2026-10-26 11:00 喝水"))
        self.assertIn("已安排", self.command("/提醒 2026-10-26 13:00 喝水"))
        row = self.memory.db.execute("SELECT * FROM tasks").fetchone()
        self.assertEqual(
            row["next_run_at"],
            daily_slot(date(2026, 10, 26), 13, 0, "America/Tijuana").isoformat(),
        )
        self.assertEqual(
            self.memory.db.execute("SELECT target FROM proactive_policies").fetchone()[
                0
            ],
            "trusted-event-target",
        )

    def test_cancel_is_scoped_and_old_backup_cannot_restore_cancelled_task(self):
        self.command("/允许主动")
        self.command("/提醒 2026-10-26 13:00 喝水")
        task = self.memory.db.execute("SELECT id FROM tasks").fetchone()[0]
        old = self.root / "old.sqlite"
        self.memory.backup(old)
        self.assertIn("没有找到", self.command("/取消提醒 " + task, scope=self.other))
        self.assertIn("已取消", self.command("/取消提醒 " + task[:12]))
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(
                DeliveryStore(restored).begin_due(self.current + timedelta(hours=1)), []
            )
            self.assertEqual(
                restored.db.execute("SELECT content FROM tasks").fetchone()[0], ""
            )
        finally:
            restored.close()

    def test_stop_is_available_even_when_global_switch_is_off(self):
        self.command("/允许主动")
        self.command("/提醒 2026-10-26 13:00 喝水")
        reply = self.command("/停止主动", enabled=False, capable=False)
        self.assertIn("可能无法撤回", reply)
        self.assertEqual(
            self.memory.db.execute("SELECT enabled FROM ledger.proactive").fetchone()[
                0
            ],
            0,
        )
        self.assertEqual(
            self.memory.db.execute("SELECT state FROM tasks").fetchone()[0], "cancelled"
        )

    def test_dst_gap_is_explained_with_the_normalized_local_time(self):
        current = daily_slot(date(2026, 3, 7), 12, 0, "America/Tijuana")
        self.command("/允许主动", current=current)
        reply = self.command("/提醒 2026-03-08 02:30 喝水", current=current)
        self.assertIn("2026-03-08 03:00", reply)
