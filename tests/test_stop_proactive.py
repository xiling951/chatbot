import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stop_proactive import revoke_pending
from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore
from plugins.astrbot_plugin_companion_core.memory import MemoryStore
from plugins.astrbot_plugin_companion_core.scheduling import daily_slot


class ShutdownTests(unittest.TestCase):
    def test_old_schema_is_not_upgraded_or_given_a_new_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with closing(sqlite3.connect(root / "old.sqlite")) as db:
                db.execute("PRAGMA user_version=3")
            self.assertEqual(
                revoke_pending(root / "old.sqlite", root / "missing-ledger.sqlite"),
                (0, 0),
            )
            self.assertFalse((root / "missing-ledger.sqlite").exists())
            with closing(sqlite3.connect(root / "old.sqlite")) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_shutdown_persists_revocation_before_process_termination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = MemoryStore(root / "memory.sqlite", root / "latest.sqlite")
            try:
                scope = memory.scope("web", "bot", "owner", "private")
                current = daily_slot(date(2026, 10, 26), 12, 0, "America/Tijuana")
                core = DeliveryStore(memory)
                core.authorize(scope, "trusted-target", True, current)
                core.create(
                    memory.begin(scope, "task"),
                    "提醒",
                    current,
                    current + timedelta(hours=1),
                    current,
                    key="task",
                )
                execution = core.begin_due(current)[0]
                core.complete_generation(execution["id"], "提醒", current)
                self.assertEqual(
                    revoke_pending(root / "memory.sqlite", root / "latest.sqlite"),
                    (1, 0),
                )
                self.assertEqual(core.queued(), [])
                self.assertEqual(
                    memory.db.execute(
                        "SELECT enabled FROM ledger.proactive"
                    ).fetchone()[0],
                    0,
                )
            finally:
                memory.close()

    def test_in_flight_is_not_falsely_reported_as_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = MemoryStore(root / "memory.sqlite", root / "latest.sqlite")
            try:
                scope = memory.scope("web", "bot", "owner", "private")
                current = daily_slot(date(2026, 10, 26), 12, 0, "America/Tijuana")
                core = DeliveryStore(memory)
                core.authorize(scope, "trusted-target", True, current)
                core.create(
                    memory.begin(scope, "task"),
                    "提醒",
                    current,
                    current + timedelta(hours=1),
                    current,
                    key="task",
                )
                execution = core.begin_due(current)[0]
                outbox = core.complete_generation(execution["id"], "提醒", current)
                core.claim_send(outbox, current, capability=True)
                self.assertEqual(
                    revoke_pending(root / "memory.sqlite", root / "latest.sqlite"),
                    (1, 1),
                )
                self.assertEqual(
                    memory.db.execute("SELECT state FROM outbox").fetchone()[0],
                    "sending",
                )
                core.recover(current + timedelta(seconds=61))
                self.assertEqual(
                    memory.db.execute("SELECT state FROM outbox").fetchone()[0],
                    "unknown",
                )
            finally:
                memory.close()
