import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qq_login import active_state, save_candidate


class QQPreparationTests(unittest.TestCase):
    def test_pending_or_incomplete_credentials_never_enable(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.assertFalse(save_candidate({"status": "pending"}, home))
            with self.assertRaises(ValueError):
                save_candidate(
                    {"status": "created", "appid": "invalid", "secret": "test"}, home
                )
            self.assertFalse(list(home.iterdir()))

    def test_confirmed_candidate_disabled_and_different_account_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            result = {
                "status": "created",
                "appid": "123456789",
                "secret": "synthetic-test-only-not-real",
            }
            self.assertTrue(save_candidate(result, home))
            path = home / "qq-bound.local.json"
            before = path.read_bytes()
            self.assertFalse(json.loads(before)["enable"])
            with self.assertRaises(ValueError):
                save_candidate(dict(result, appid="987654321"), home)
            self.assertEqual(path.read_bytes(), before)

    def test_expired_and_completed_state_cannot_be_polled(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            current = datetime.now(timezone.utc)
            path = home / "qq-login-state.local.json"
            state = {
                "expires_at": (current + timedelta(minutes=5)).isoformat(),
                "completed": False,
            }
            path.write_text(json.dumps(state))
            self.assertEqual(active_state(home, current), state)
            for expired in (
                dict(state, completed=True),
                dict(state, expires_at=(current - timedelta(seconds=1)).isoformat()),
            ):
                path.write_text(json.dumps(expired))
                with self.assertRaises(ValueError):
                    active_state(home, current)
