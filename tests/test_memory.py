import json
import tempfile
import unittest
from pathlib import Path

from plugins.astrbot_plugin_companion_core.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.scope = self.store.scope("webchat", "bot", "alice", "private")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def put(self, key, value, source="message-1", **kwargs):
        return self.store.put(
            self.store.begin(self.scope, source), key, value, **kwargs
        )

    def test_twenty_facts_survive_restart(self):
        cases = json.loads(
            Path(__file__).with_name("memory_cases.json").read_text(encoding="utf-8")
        )
        for i, case in enumerate(cases):
            self.put(case["key"], case["value"], f"sample-{i}")
        self.store.close()
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        for case in cases:
            with self.subTest(case=case["key"]):
                found = self.store.recall(self.scope, case["query"])
                self.assertIn(
                    (case["key"], case["value"]),
                    [(f["predicate"], f["value"]) for f in found],
                )

    def test_conflict_requires_explicit_correction(self):
        self.put("音乐", "摇滚")
        with self.assertRaises(ValueError):
            self.put("音乐", "爵士", "conflict")
        self.put("音乐", "爵士", "correction", correction=True)
        rows = self.store.facts(self.scope)
        self.assertEqual([(r["value"], r["version"]) for r in rows], [("爵士", 2)])
        self.assertNotIn("摇滚", json.dumps(rows, ensure_ascii=False))

    def test_unknown_and_negative_facts(self):
        self.assertEqual(self.store.recall(self.scope, "我的生日是哪天"), [])
        self.put("饮料", "不喝咖啡")
        self.assertEqual(
            self.store.recall(self.scope, "我喝什么")[0]["value"], "不喝咖啡"
        )

    def test_short_chinese_and_synonyms(self):
        self.put("音乐", "爵士")
        for query in ["爵士", "我听什么", "我喜欢什么曲风"]:
            self.assertEqual(self.store.recall(self.scope, query)[0]["value"], "爵士")

    def test_topic_outranks_newer_incidental_characters(self):
        self.put("音乐", "爵士")
        self.put("工作时段", "上午", "newer-a")
        self.put("作息", "早起", "newer-b")
        self.assertEqual(
            self.store.recall(self.scope, "我平时听什么")[0]["predicate"], "音乐"
        )
        self.assertEqual(self.store.recall(self.scope, "我的生日是哪天"), [])

    def test_subject_persona_bot_and_group_isolation(self):
        self.put("称呼", "同名测试")
        for args in [
            ("webchat", "bot", "bob", "private"),
            ("webchat", "bot", "alice", "group:1"),
            ("webchat", "other", "alice", "private"),
            ("qq", "bot", "alice", "private"),
        ]:
            other = self.store.scope(*args)
            self.assertEqual(self.store.recall(other, "称呼"), [])
        self.assertEqual(
            self.store.facts(
                self.store.scope("webchat", "bot", "alice", "private", "other-persona")
            ),
            [],
        )

    def test_untrusted_sources_cannot_write(self):
        for kind in ["model_inference", "roleplay", "external", "assistant"]:
            with self.assertRaises(ValueError):
                self.put("称呼", "篡改", source_type=kind)

    def test_credential_filter(self):
        with self.assertRaises(ValueError):
            self.put("密码", "do-not-store")

    def test_delete_and_late_extraction(self):
        self.put("音乐", "爵士")
        ticket = self.store.begin(self.scope, "late")
        self.store.forget(self.scope, "音乐")
        with self.assertRaises(ValueError):
            self.store.put(ticket, "音乐", "爵士")
        with self.assertRaises(ValueError):
            self.put("音乐", "爵士")
        self.put("音乐", "摇滚", "fresh-authorization")
        self.assertEqual(self.store.facts(self.scope)[0]["value"], "摇滚")

    def test_correction_rejects_old_ticket(self):
        self.put("音乐", "摇滚")
        ticket = self.store.begin(self.scope, "queued")
        self.put("音乐", "爵士", "new", correction=True)
        with self.assertRaises(ValueError):
            self.store.put(ticket, "音乐", "摇滚", correction=True)

    def test_stop_resume_and_pending_invalidation(self):
        self.put("音乐", "爵士")
        ticket = self.store.begin(self.scope, "pending")
        self.store.set_enabled(self.scope, False)
        self.assertEqual(self.store.recall(self.scope, "音乐"), [])
        with self.assertRaises(ValueError):
            self.put("饮料", "茶")
        self.store.set_enabled(self.scope, True)
        with self.assertRaises(ValueError):
            self.store.put(ticket, "饮料", "茶")
        self.assertEqual(len(self.store.facts(self.scope)), 1)

    def test_restore_old_backup_replays_deletions_and_stop(self):
        self.put("音乐", "爵士")
        self.put("饮料", "茶", "other-source")
        snapshot = self.root / "old.sqlite"
        self.store.backup(snapshot)
        self.store.forget(self.scope, "音乐")
        self.store.set_enabled(self.scope, False)
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(
                [r["predicate"] for r in restored.facts(self.scope)], ["饮料"]
            )
            self.assertEqual(restored.recall(self.scope, "饮料"), [])
            self.assertNotIn(
                "爵士", json.dumps(restored.facts(self.scope), ensure_ascii=False)
            )
        finally:
            restored.close()

    def test_backup_restores_versions_sources_and_identity(self):
        self.put("音乐", "摇滚")
        self.put("音乐", "爵士", "new", correction=True)
        snapshot = self.root / "good.sqlite"
        self.store.backup(snapshot)
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(
                restored.scope("webchat", "bot", "alice", "private"), self.scope
            )
            self.assertEqual(restored.facts(self.scope), self.store.facts(self.scope))
        finally:
            restored.close()

    def test_old_backup_cannot_restore_corrected_value(self):
        self.put("音乐", "摇滚")
        snapshot = self.root / "old.sqlite"
        self.store.backup(snapshot)
        self.put("音乐", "爵士", "correction", correction=True)
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(restored.facts(self.scope), [])
        finally:
            restored.close()

    def test_missing_and_foreign_ledger_refused(self):
        with self.assertRaises(ValueError):
            MemoryStore.restore(
                self.root / "memory.sqlite",
                self.root / "restore.sqlite",
                self.root / "missing.sqlite",
            )
        with self.assertRaises(RuntimeError):
            MemoryStore(self.root / "memory.sqlite", self.root / "missing.sqlite")
        other = MemoryStore(self.root / "other.sqlite", self.root / "foreign.sqlite")
        other.close()
        with self.assertRaises(RuntimeError):
            MemoryStore(self.root / "memory.sqlite", self.root / "foreign.sqlite")

    def test_twenty_message_ids_and_duplicate_deliveries(self):
        for i in range(20):
            self.assertTrue(self.store.claim("web", "bot", "conversation", str(i)))
            self.assertFalse(self.store.claim("web", "bot", "conversation", str(i)))
        self.assertTrue(self.store.claim("web", "other-bot", "conversation", "1"))
        with self.assertRaises(ValueError):
            self.store.claim("web", "bot", "conversation", "")

    def test_memory_budget_is_bounded(self):
        self.put("音乐", "爵士")
        self.assertEqual(self.store.recall(self.scope, "音乐", budget=1), [])

    def test_short_history_scope_retention_and_deletion(self):
        for index in range(5):
            self.store.append_turn(
                self.store.begin(self.scope, f"turn-{index}"),
                "chat-a",
                f"user-{index}",
                f"reply-{index}",
            )
        history = self.store.history(self.scope, "chat-a")
        self.assertEqual(len(history), 6)
        self.assertEqual(history[0]["content"], "user-2")
        self.assertEqual(self.store.history(self.scope, "chat-b"), [])
        self.assertEqual(self.store.history(self.scope, "chat-a", budget=1), [])
        ticket = self.store.begin(self.scope, "late-reply")
        self.store.forget(self.scope)
        self.assertEqual(self.store.history(self.scope, "chat-a"), [])
        with self.assertRaises(ValueError):
            self.store.append_turn(ticket, "chat-a", "stale", "stale")

    def test_old_backup_cannot_revive_short_history(self):
        self.put("音乐", "爵士")
        self.store.append_turn(
            self.store.begin(self.scope, "chat-msg"), "chat", "我喜欢爵士", "收到"
        )
        snapshot = self.root / "old.sqlite"
        self.store.backup(snapshot)
        self.store.forget(self.scope, "音乐")
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertEqual(restored.history(self.scope, "chat"), [])
        finally:
            restored.close()


if __name__ == "__main__":
    unittest.main()
