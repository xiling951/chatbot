import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from plugins.astrbot_plugin_companion_core.memory import MemoryStore


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.scope = self.store.scope("web", "bot", "alice", "private", "test")
        self.other = self.store.scope("web", "bot", "bob", "private", "test")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def put(self, key, value, source="a", **kwargs):
        return self.store.put(
            self.store.begin(self.scope, source), key, value, **kwargs
        )

    def propose(self, value, source="pending", **kwargs):
        return self.store.apply_statement(
            self.store.begin(self.scope, source), "音乐", value, **kwargs
        )

    def test_automatic_requires_opt_in_and_separate_scope(self):
        self.assertFalse(self.store.automatic_enabled(self.scope))
        with self.assertRaises(ValueError):
            self.propose("爵士")
        self.store.set_automatic(self.scope, True)
        self.assertFalse(self.store.automatic_enabled(self.other))
        self.assertEqual(self.propose("爵士")[0], "saved")
        self.assertEqual(
            self.store.facts(self.scope)[0]["source_type"], "user_explicit"
        )

    def test_conflict_confirmation_is_scoped_and_versioned(self):
        self.put("音乐", "摇滚")
        self.store.set_automatic(self.scope, True)
        state, identifier = self.propose("爵士")
        self.assertEqual(state, "pending")
        self.assertEqual(self.store.facts(self.scope)[0]["value"], "摇滚")
        self.assertNotIn("爵士", str(self.store.recall(self.scope, "音乐")))
        with self.assertRaises(ValueError):
            self.store.confirm(self.other, identifier[:12])
        self.store.confirm(self.scope, identifier[:12])
        self.assertEqual(self.store.candidates(self.scope), [])
        self.assertEqual(
            [(r["value"], r["version"]) for r in self.store.facts(self.scope)],
            [("爵士", 2)],
        )
        with self.assertRaises(ValueError):
            self.store.confirm(self.scope, identifier)

    def test_time_change_invalidates_in_flight_ticket(self):
        self.put("音乐", "摇滚")
        self.store.set_automatic(self.scope, True)
        ticket = self.store.begin(self.scope, "late")
        self.propose("爵士", correction=True)
        with self.assertRaises(ValueError):
            self.store.apply_statement(ticket, "音乐", "摇滚", correction=True)
        self.assertEqual(self.store.facts(self.scope)[0]["value"], "爵士")

    def test_delete_stop_auto_off_and_correction_invalidate_pending(self):
        for action in ("delete", "stop", "off", "correct"):
            with self.subTest(action=action):
                self.store.set_enabled(self.scope, True)
                self.store.set_automatic(self.scope, True)
                self.put("音乐", "摇滚", action + "-fact", correction=True)
                _, identifier = self.propose("爵士", source=action + "-candidate")
                ticket = self.store.begin(self.scope, action + "-queued")
                if action == "delete":
                    self.store.forget(self.scope, "音乐")
                elif action == "stop":
                    self.store.set_enabled(self.scope, False)
                elif action == "off":
                    self.store.set_automatic(self.scope, False)
                else:
                    self.put("音乐", "民谣", action + "-new", correction=True)
                with self.assertRaises(ValueError):
                    self.store.confirm(self.scope, identifier)
                with self.assertRaises(ValueError):
                    self.store.apply_statement(ticket, "城市", "上海")
                self.assertEqual(self.store.candidates(self.scope), [])

    def test_restored_pending_events_relationships_and_policy_respect_latest_ledger(
        self,
    ):
        self.store.set_automatic(self.scope, True)
        self.put("音乐", "摇滚")
        self.put("称呼", "小林", "name")
        self.store.record_event(
            self.store.begin(self.scope, "event"), "shared_plan", "一起读科幻"
        )
        self.propose("爵士")
        old = self.root / "old.sqlite"
        self.store.backup(old)
        self.store.forget(self.scope)
        self.store.set_automatic(self.scope, False)
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(restored.facts(self.scope), [])
            self.assertEqual(restored.relationship(self.scope), [])
            self.assertEqual(restored.events(self.scope), [])
            self.assertEqual(restored.candidates(self.scope), [])
            self.assertFalse(restored.automatic_enabled(self.scope))
            self.assertNotIn(
                "小林", str(list(restored.db.execute("SELECT * FROM relationships")))
            )
        finally:
            restored.close()

    def test_replaced_candidate_cannot_return_from_backup(self):
        self.put("音乐", "摇滚")
        self.store.set_automatic(self.scope, True)
        _, old_id = self.propose("爵士")
        old = self.root / "old.sqlite"
        self.store.backup(old)
        self.propose("古典", "new-pending")
        restored = MemoryStore.restore(
            old, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(restored.candidates(self.scope), [])
            with self.assertRaises(ValueError):
                restored.confirm(self.scope, old_id)
        finally:
            restored.close()

    def test_relationship_event_isolation_and_truthful_types(self):
        self.put("称呼", "小林")
        self.put("互动边界", "不要催回复", "boundary")
        self.assertEqual(len(self.store.relationship(self.scope)), 2)
        self.assertEqual(self.store.relationship(self.other), [])
        for kind in ("user_reported", "shared_plan"):
            self.store.record_event(self.store.begin(self.scope, kind), kind, "读科幻")
        with self.assertRaises(ValueError):
            self.store.record_event(
                self.store.begin(self.scope, "fake"), "verified_ai_activity", "去过海边"
            )
        self.assertEqual(self.store.events(self.other), [])
        self.store.forget(self.scope, "共同事件")
        self.assertEqual(self.store.events(self.scope), [])
        self.assertEqual(len(self.store.relationship(self.scope)), 2)
        self.store.set_enabled(self.scope, False)
        self.assertEqual(self.store.relationship(self.scope), [])

    def test_schema_two_migration_preserves_existing_data(self):
        self.put("音乐", "爵士")
        self.store.close()
        with closing(sqlite3.connect(self.root / "memory.sqlite")) as db, db:
            db.execute("DROP VIEW relationships")
            for table in (
                "scope_metadata",
                "memory_preferences",
                "candidates",
                "events",
                "proactive_policies",
                "tasks",
                "executions",
                "outbox",
                "emotions",
                "emotion_events",
            ):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=2")
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 4)
        self.assertEqual(self.store.facts(self.scope)[0]["value"], "爵士")
        self.assertFalse(self.store.automatic_enabled(self.scope))
        self.assertEqual(
            self.store.scope("web", "bot", "alice", "private", "test"), self.scope
        )

    def test_current_backup_preserves_relationship_event_and_candidate_sources(self):
        self.store.set_automatic(self.scope, True)
        self.put("音乐", "摇滚")
        self.put("称呼", "小林", "nickname")
        self.store.record_event(
            self.store.begin(self.scope, "event"), "user_reported", "今天讨论科幻"
        )
        self.propose("爵士")
        snapshot = self.root / "good.sqlite"
        self.store.backup(snapshot)
        with self.assertRaises(ValueError):
            self.store.backup(snapshot)
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            for method in ("facts", "relationship", "events", "candidates"):
                self.assertEqual(
                    getattr(restored, method)(self.scope),
                    getattr(self.store, method)(self.scope),
                )
            self.assertTrue(restored.automatic_enabled(self.scope))
        finally:
            restored.close()

    def test_sensitive_values_and_untrusted_event_sources_rejected(self):
        for key, value in [
            ("称呼", "13812345678"),
            ("邮箱", "test@example.com"),
            ("密码", "secret"),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.put(key, value)
