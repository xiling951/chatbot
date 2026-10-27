"""Out-of-band owner enrollment for QQ; not cross-platform memory binding."""

import hashlib
import json
import re
import secrets
from datetime import datetime
from pathlib import Path

from filelock import FileLock


def claim_owner(
    home: Path,
    *,
    platform: str,
    actual_appid: str,
    sender: str,
    group: str,
    text: str,
    current: datetime,
) -> bool:
    if group or not sender:
        return False
    match = re.fullmatch(r"/验证本人\s*([A-Fa-f0-9]{16})", text.strip())
    if not match:
        return False
    state_path = home / "qq-owner-claim.local.json"
    settings_path = home / "settings.local.json"
    with FileLock(str(state_path) + ".lock", timeout=3):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(state["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if (
            state.get("used")
            or state.get("owner_test_authorized") is not True
            or expires.tzinfo is None
            or current >= expires
            or (home / "STOP").exists()
        ):
            return False
        expected = state.get("appid")
        if (
            state.get("platform_id") != platform
            or not expected
            or expected != actual_appid
            or settings.get("bot_account_ids", {}).get(platform) != expected
        ):
            return False
        if settings.get("allowed_senders", {}).get(platform):
            return False
        supplied = hashlib.sha256(match[1].upper().encode()).hexdigest()
        if not secrets.compare_digest(supplied, state.get("code_hash", "")):
            return False
        # Consume before changing authorization; interrupted enrollment fails
        # closed instead of leaving a reusable secret. Never replace an owner.
        state["used"] = True
        state["claimed_at"] = current.isoformat()
        state_path.write_text(json.dumps(state), encoding="utf-8")
        settings.setdefault("allowed_senders", {})[platform] = [sender]
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
