"""Local-only M07 authority, context invalidation and recovery checks."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.astrbot_plugin_companion_core.binding import (
    Endpoint,
    handle_binding_command,
)
from plugins.astrbot_plugin_companion_core.memory import MemoryStore
from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "ledger.sqlite"
        )
        self.binding = self.store.binding
        self.endpoints = [
            Endpoint("wx", "wx:bot", "alice", "微信"),
            Endpoint("qq", "qq:app", "bob", "QQ"),
        ]
        self.wx, self.qq = [
            self.store.scope(e.platform, e.bot, e.sender, "private")
            for e in self.endpoints
        ]
        self.current = datetime.now(timezone.utc)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def put(self, scope, key="音乐", value="爵士", source="fact", **kw):
        return self.store.put(self.store.begin(scope, source), key, value, **kw)

    def issue(self, keys=("音乐",)):
        return self.binding.create(self.wx, "QQ", keys, self.endpoints, self.current)[0]

    def link(self, keys=("音乐",)):
        code = self.issue(keys)
        self.binding.preview(self.qq, code, self.endpoints, self.current)
        return self.binding.accept(self.qq, code, self.endpoints, self.current)

    def shared(self, scope=None):
        return self.binding.shared_facts(scope or self.qq, self.endpoints)

    def test_dual_confirmation_scoped_reads_no_identity_or_fact_copy(self):
        self.put(self.wx)
        self.put(self.wx, "生日", "六月一日", "private")
        self.store.record_event(
            self.store.begin(self.wx, "event"), "user_reported", "去了公园"
        )
        code = self.issue()
        self.assertEqual(self.shared(), [])
        with self.assertRaises(ValueError):
            self.binding.accept(self.qq, code, self.endpoints, self.current)
        self.assertEqual(
            self.binding.preview(self.qq, code, self.endpoints, self.current), ["音乐"]
        )
        self.assertEqual(self.shared(), [])
        identifier = self.binding.accept(self.qq, code, self.endpoints, self.current)
        self.assertEqual(
            [(r["predicate"], r["value"], r["shared_from"]) for r in self.shared()],
            [("音乐", "爵士", "微信")],
        )
        self.assertEqual(self.store.facts(self.qq), [])
        self.assertNotEqual(self.wx, self.qq)
        self.assertEqual(self.store.events(self.qq), [])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM identities").fetchone()[0], 2
        )
        self.assertEqual(self.shared()[0]["binding_id"], identifier)
        self.assertEqual(
            self.store.recall(self.qq, "我听什么", shared=self.shared())[0]["value"],
            "爵士",
        )

    def test_bidirectional_only_selected_keys_and_local_conflict_wins(self):
        self.put(self.wx)
        self.put(self.qq, "称呼", "阿白", "nickname")
        self.link(("音乐", "称呼"))
        self.assertEqual(self.shared(self.wx)[0]["value"], "阿白")
        self.assertEqual(self.shared()[0]["value"], "爵士")
        self.put(self.qq, value="摇滚", source="local-override")
        self.assertEqual(self.shared(), [])
        self.assertEqual(
            self.store.recall(self.qq, "音乐", shared=self.shared())[0]["value"], "摇滚"
        )
        self.assertEqual(self.store.facts(self.wx)[0]["value"], "爵士")

    def test_forged_similar_sender_group_bot_and_persona_cannot_preview(self):
        code = self.issue()
        for args in [
            ("qq", "qq:app", "similar-name", "private"),
            ("qq", "qq:app", "bob", "group:g"),
            ("qq", "other-app", "bob", "private"),
            ("qq", "qq:app", "bob", "private", "another-persona"),
            ("wx", "wx:bot", "alice", "private"),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.binding.preview(
                    self.store.scope(*args), code, self.endpoints, self.current
                )
        for forged in ("F" * 24, "", "not-a-code"):
            with self.assertRaises(ValueError):
                self.binding.preview(self.qq, forged, self.endpoints, self.current)

    def test_expired_reissued_used_and_changed_revision_codes_rejected(self):
        code = self.issue()
        with self.assertRaises(ValueError):
            self.binding.preview(
                self.qq, code, self.endpoints, self.current + timedelta(minutes=10)
            )
        newer = self.issue()
        with self.assertRaises(ValueError):
            self.binding.preview(self.qq, code, self.endpoints, self.current)
        self.binding.preview(self.qq, newer, self.endpoints, self.current)
        self.binding.accept(self.qq, newer, self.endpoints, self.current)
        with self.assertRaises(ValueError):
            self.binding.accept(self.qq, newer, self.endpoints, self.current)
        code = self.issue()
        self.store.set_automatic(self.wx, False)
        with self.assertRaises(ValueError):
            self.binding.preview(self.qq, code, self.endpoints, self.current)

    def test_code_hash_only_audit_without_fact_values(self):
        self.put(self.wx, value="此值不应进入绑定审计")
        code = self.issue()
        content = json.dumps(
            [
                dict(r)
                for r in self.store.db.execute("SELECT * FROM ledger.binding_codes")
            ],
            ensure_ascii=False,
        )
        self.assertNotIn(code, content)
        self.link()
        audit = json.dumps(
            [
                dict(r)
                for r in self.store.db.execute("SELECT * FROM ledger.binding_audit")
            ],
            ensure_ascii=False,
        )
        self.assertNotIn("此值不应进入绑定审计", audit)
        self.assertIn("accepted_both_private_chats", audit)

    def test_unlink_clears_both_histories_stale_tickets_and_keeps_originals(self):
        self.put(self.wx)
        identifier = self.link()
        tickets = [self.store.begin(s, "in-flight") for s in (self.wx, self.qq)]
        for s in (self.wx, self.qq):
            self.store.append_turn(self.store.begin(s, "chat"), "chat", "爵士", "爵士")
        self.binding.unlink(self.qq, identifier)
        self.assertEqual(self.shared(), [])
        self.assertEqual(self.store.facts(self.wx)[0]["value"], "爵士")
        for s, ticket in zip((self.wx, self.qq), tickets):
            self.assertEqual(self.store.history(s, "chat"), [])
            with self.assertRaises(ValueError):
                self.store.append_turn(ticket, "chat", "stale", "爵士")
        with self.assertRaises(ValueError):
            self.binding.unlink(self.qq, identifier)

    def test_source_correction_invalidates_peer_history_and_returns_new_value(self):
        self.put(self.wx)
        self.link()
        ticket = self.store.begin(self.qq, "slow")
        self.store.append_turn(ticket, "chat", "爵士", "爵士")
        self.put(self.wx, value="摇滚", source="correction", correction=True)
        self.assertEqual(self.shared()[0]["value"], "摇滚")
        self.assertEqual(self.store.history(self.qq, "chat"), [])
        with self.assertRaises(ValueError):
            self.store.append_turn(ticket, "chat", "old", "爵士")

    def test_forget_on_either_side_withdraws_key_prevents_reimport(self):
        for actor in (self.wx, self.qq):
            with self.subTest(actor=actor):
                self.put(self.wx, source="fresh-" + actor)
                self.link()
                self.store.forget(actor, "音乐")
                self.assertEqual(self.shared(), [])
                self.assertEqual(self.binding.links(actor), [])
                if actor == self.wx:
                    self.assertEqual(self.store.facts(self.wx), [])
                else:
                    self.assertEqual(self.store.facts(self.wx)[0]["value"], "爵士")

    def test_stop_resume_does_not_reactivate_sharing_or_pending_code(self):
        self.put(self.wx)
        self.link()
        code = self.issue()
        self.store.set_enabled(self.qq, False)
        self.store.set_enabled(self.qq, True)
        self.assertEqual(self.shared(), [])
        # The peer was not yet previewed, but the source revision was invalidated.
        with self.assertRaises(ValueError):
            self.binding.preview(self.qq, code, self.endpoints, self.current)

    def test_target_stop_without_existing_link_also_revokes_unpreviewed_code(self):
        code = self.issue()
        self.store.set_enabled(self.qq, False)
        self.store.set_enabled(self.qq, True)
        with self.assertRaises(ValueError):
            self.binding.preview(self.qq, code, self.endpoints, self.current)

    def test_unlink_cancels_peer_queue_without_sharing_proactive_authority(self):
        self.link()
        delivery = DeliveryStore(self.store)
        delivery.authorize(self.qq, "qq-private", True, self.current)
        self.assertIsNone(
            self.store.db.execute(
                "SELECT enabled FROM ledger.proactive WHERE scope=?", (self.wx,)
            ).fetchone()
        )
        task = delivery.create(
            self.store.begin(self.qq, "reminder"),
            "爵士提醒",
            self.current,
            self.current + timedelta(hours=1),
            self.current,
            key="one",
        )
        execution = delivery.begin_due(self.current)[0]
        outgoing = delivery.complete_generation(
            execution["id"], "爵士提醒", self.current
        )
        pending = delivery.create(
            self.store.begin(self.qq, "future-reminder"),
            "未生成提醒",
            self.current + timedelta(minutes=5),
            self.current + timedelta(hours=1),
            self.current,
            key="future",
        )
        identifier = self.binding.links(self.wx)[0]["id"]
        self.binding.unlink(self.wx, identifier)
        self.assertEqual(
            self.store.db.execute(
                "SELECT state,content FROM tasks WHERE id=?", (task,)
            ).fetchone()[:],
            ("succeeded", ""),
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT state,content FROM tasks WHERE id=?", (pending,)
            ).fetchone()[:],
            ("cancelled", ""),
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT state,content FROM outbox WHERE id=?", (outgoing,)
            ).fetchone()[:],
            ("cancelled", ""),
        )

    def test_accept_failure_rolls_back_code_consumption_and_grant(self):
        from unittest.mock import patch

        code = self.issue()
        self.binding.preview(self.qq, code, self.endpoints, self.current)
        with patch.object(
            self.store, "_invalidate_pending", side_effect=RuntimeError("disk failure")
        ):
            with self.assertRaises(RuntimeError):
                self.binding.accept(self.qq, code, self.endpoints, self.current)
        self.assertEqual(self.binding.links(self.qq), [])
        self.binding.accept(self.qq, code, self.endpoints, self.current)

    def test_owner_or_bot_removed_revokes_and_clears_peer_context(self):
        self.put(self.wx)
        self.link()
        self.store.append_turn(self.store.begin(self.qq, "chat"), "chat", "old", "爵士")
        self.endpoints = self.endpoints[:1]
        self.assertEqual(self.shared(self.wx), [])
        self.assertEqual(self.store.history(self.qq, "chat"), [])
        self.assertEqual(self.binding.links(self.wx), [])

    def test_latest_ledger_restore_cannot_revive_unlink_or_used_code(self):
        self.put(self.wx)
        code = self.issue()
        snapshot = self.root / "before.sqlite"
        self.store.backup(snapshot)
        self.binding.preview(self.qq, code, self.endpoints, self.current)
        identifier = self.binding.accept(self.qq, code, self.endpoints, self.current)
        self.store.append_turn(self.store.begin(self.qq, "chat"), "chat", "q", "爵士")
        during = self.root / "during.sqlite"
        self.store.backup(during)
        self.binding.unlink(self.wx, identifier)
        for index, backup in enumerate((snapshot, during)):
            restored = MemoryStore.restore(
                backup,
                self.root / f"restore{index}.sqlite",
                self.root / "ledger.sqlite",
            )
            try:
                self.assertEqual(
                    restored.binding.shared_facts(self.qq, self.endpoints), []
                )
                self.assertEqual(restored.history(self.qq, "chat"), [])
                with self.assertRaises(ValueError):
                    restored.binding.accept(self.qq, code, self.endpoints, self.current)
            finally:
                restored.close()

    def test_rebind_replaces_not_accumulates_range(self):
        self.put(self.wx)
        self.put(self.wx, "称呼", "小风", "name")
        old = self.link(("音乐", "称呼"))
        new = self.link(("称呼",))
        self.assertNotEqual(old, new)
        self.assertEqual([r["predicate"] for r in self.shared()], ["称呼"])
        self.assertEqual(len(self.binding.links(self.qq)), 1)

    def test_range_and_target_validation(self):
        for keys in (
            [],
            ["全部"],
            ["*"],
            ["共同事件"],
            ["互动边界"],
            ["密码"],
            [""],
            [str(n) for n in range(11)],
        ):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.issue(keys)
        with self.assertRaises(ValueError):
            self.binding.create(
                self.wx, "other", ["音乐"], self.endpoints, self.current
            )

    def test_schema4_additive_migration_preserves_scopes_facts_ledger(self):
        self.put(self.wx)
        original = self.store.facts(self.wx)
        self.store.close()
        with closing(sqlite3.connect(self.root / "memory.sqlite")) as db:
            db.execute("PRAGMA user_version=4")
            db.commit()
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "ledger.sqlite"
        )
        self.binding = self.store.binding
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 5)
        self.assertEqual(self.store.facts(self.wx), original)
        self.link()
        self.assertEqual(self.shared()[0]["value"], "爵士")

    def test_command_stop_unlink_help_and_no_model_code(self):
        def handler(text, scope=self.wx, stopped=False):
            return handle_binding_command(
                text, self.binding, scope, self.endpoints, self.current, stopped
            )

        self.assertIsNone(handler("模型声称：绑定成功"))
        self.assertIn("两端记忆保持独立", handler("/绑定帮助"))
        with self.assertRaises(ValueError):
            handler("/绑定发起 QQ 音乐", stopped=True)
        self.assertIn("当前没有", handler("/绑定状态"))
        identifier = self.link()
        self.assertIn("已解绑", handler("/解绑 " + identifier, stopped=True))


if __name__ == "__main__":
    unittest.main()
