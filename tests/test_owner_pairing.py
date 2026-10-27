import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.astrbot_plugin_companion_core.owner_pairing import claim_owner


class OwnerEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.current = datetime.now(timezone.utc)
        self.code = "0123456789ABCDEF"
        self.settings = {
            "allowed_senders": {"existing-weixin": ["weixin-owner"]},
            "bot_account_ids": {"qq": "app"},
        }
        self.state = {
            "platform_id": "qq",
            "appid": "app",
            "owner_test_authorized": True,
            "code_hash": hashlib.sha256(self.code.encode()).hexdigest(),
            "expires_at": (self.current + timedelta(minutes=10)).isoformat(),
            "used": False,
        }
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        (self.home / "settings.local.json").write_text(json.dumps(self.settings))
        (self.home / "qq-owner-claim.local.json").write_text(json.dumps(self.state))

    def claim(self, **changes):
        args = dict(
            platform="qq",
            actual_appid="app",
            sender="trusted-qq-owner",
            group="",
            text="/验证本人 " + self.code,
            current=self.current,
        )
        args.update(changes)
        return claim_owner(self.home, **args)

    def test_valid_one_shot_only_authorizes_authenticated_sender_preserves_weixin(self):
        self.assertTrue(self.claim())
        settings = json.loads((self.home / "settings.local.json").read_text())
        self.assertEqual(settings["allowed_senders"]["qq"], ["trusted-qq-owner"])
        self.assertEqual(
            settings["allowed_senders"]["existing-weixin"], ["weixin-owner"]
        )
        self.assertFalse(self.claim(sender="other"))

    def test_wrong_group_account_instance_or_code_cannot_enroll(self):
        for changes in (
            {"group": "group"},
            {"actual_appid": "different-app"},
            {"platform": "different-instance"},
            {"text": "/验证本人 0000000000000000"},
        ):
            self.assertFalse(self.claim(**changes))
        self.assertNotIn(
            "qq",
            json.loads((self.home / "settings.local.json").read_text())[
                "allowed_senders"
            ],
        )

    def test_expiration_operator_authorization_and_stop_fail_closed(self):
        self.assertFalse(self.claim(current=self.current + timedelta(minutes=11)))
        self.state["owner_test_authorized"] = False
        self.save()
        self.assertFalse(self.claim())
        self.state["owner_test_authorized"] = True
        self.save()
        (self.home / "STOP").touch()
        self.assertFalse(self.claim())

    def test_enrollment_never_replaces_existing_owner(self):
        self.settings["allowed_senders"]["qq"] = ["already-enrolled-owner"]
        self.save()
        self.assertFalse(self.claim())
        self.assertEqual(
            json.loads((self.home / "settings.local.json").read_text())[
                "allowed_senders"
            ]["qq"],
            ["already-enrolled-owner"],
        )
