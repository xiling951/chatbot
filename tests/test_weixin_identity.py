import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from repair_weixin_identity import repair
from proactive_test import owner_scope
from plugins.astrbot_plugin_companion_core.memory import MemoryStore


class WeixinIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.memory = MemoryStore(root / "memory.sqlite", root / "latest.sqlite")
        self.scope = self.memory.scope(
            "weixin", "weixin:weixin", "owner", "private", "persona"
        )
        self.memory.put(self.memory.begin(self.scope, "message"), "音乐", "爵士")
        self.bound = {
            "id": "weixin",
            "weixin_oc_user_id": "owner",
            "weixin_oc_account_id": "real-account",
            "weixin_oc_token": "fake-token",
        }
        self.settings = {"allowed_senders": {"weixin": ["owner"]}}

    def tearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def test_current_verified_identity_repair_preserves_person_scope_and_facts(self):
        facts = self.memory.facts(self.scope)
        self.assertEqual(
            repair(self.memory.db, self.settings, self.bound, self.bound), 1
        )
        self.assertEqual(
            self.memory.scope(
                "weixin", "weixin:real-account", "owner", "private", "persona"
            ),
            self.scope,
        )
        self.assertEqual(self.memory.facts(self.scope), facts)
        scope, target = owner_scope(
            self.memory.db, self.settings, self.bound, "persona"
        )
        self.assertEqual(scope, self.scope)
        self.assertEqual(target, "weixin:FriendMessage:owner")
        self.assertEqual(
            repair(self.memory.db, self.settings, self.bound, self.bound), 0
        )

    def test_mismatch_or_existing_second_identity_is_never_merged(self):
        other = dict(self.bound, weixin_oc_account_id="other-account")
        with self.assertRaises(ValueError):
            repair(self.memory.db, self.settings, self.bound, other)
        self.memory.scope(
            "weixin", "weixin:real-account", "owner", "private", "persona"
        )
        with self.assertRaises(ValueError):
            repair(self.memory.db, self.settings, self.bound, self.bound)
        self.assertEqual(
            len(self.memory.db.execute("SELECT person FROM identities").fetchall()), 2
        )

    def test_test_target_lookup_cannot_borrow_another_owner(self):
        repair(self.memory.db, self.settings, self.bound, self.bound)
        bad = dict(self.bound, weixin_oc_user_id="stranger")
        with self.assertRaises(ValueError):
            owner_scope(self.memory.db, self.settings, bad, "persona")
