"""Prepare official QQ QR credentials; never enable an adapter or send messages."""

import argparse
import asyncio
import json
import os
import sys
import uuid
import time
from datetime import datetime, timedelta, timezone

import qrcode
from local_paths import CODE_ROOT, PLUGIN_DATA, RUNTIME_ROOT

os.environ["ASTRBOT_ROOT"] = str(RUNTIME_ROOT)
sys.path.insert(0, str(CODE_ROOT))
from astrbot.core.platform.sources.qqofficial.login_registration import (
    request_qqofficial_login_qr,
    poll_qqofficial_login_once,
)

PLATFORM = {
    "id": "companion-qq",
    "type": "qq_official",
    "enable": False,
    "qqofficial_bind_host": "q.qq.com",
    "qqofficial_api_timeout_ms": 10000,
    "enable_group_c2c": True,
    "enable_guild_direct_message": False,
    "use_markdown": False,
}


def save_candidate(result, home=PLUGIN_DATA):
    if result.get("status") != "created":
        return False
    appid, secret = result.get("appid"), result.get("secret")
    if (
        not isinstance(appid, str)
        or not appid.isdigit()
        or not isinstance(secret, str)
        or len(secret) < 10
    ):
        raise ValueError("Incomplete QQ candidate credentials")
    path = home / "qq-bound.local.json"
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("appid") != appid:
            raise ValueError("A different QQ candidate already exists; no replacement")
    path.write_text(
        json.dumps(
            dict(PLATFORM, appid=appid, secret=secret), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return True


def active_state(home, current):
    value = json.loads((home / "qq-login-state.local.json").read_text(encoding="utf-8"))
    deadline = datetime.fromisoformat(value["expires_at"])
    if deadline.tzinfo is None or current >= deadline or value.get("completed"):
        raise ValueError("QQ login expired or already completed; no poll performed")
    return value


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "poll"])
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Poll this same task until terminal or ten-minute limit",
    )
    args = parser.parse_args()
    home = PLUGIN_DATA
    state_path = home / "qq-login-state.local.json"
    current = datetime.now(timezone.utc)
    try:
        if args.action == "create":
            qr = await request_qqofficial_login_qr(PLATFORM)
            state = {
                "task_id": qr.task_id,
                "bind_key": qr.bind_key,
                "expires_at": (current + timedelta(minutes=10)).isoformat(),
                "completed": False,
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            output = home / f"qq-login-{uuid.uuid4().hex}.local.png"
            qrcode.make(qr.qrcode).save(output)
            print(
                json.dumps(
                    {
                        "status": "qr_ready",
                        "image": str(output),
                        "adapter_enabled": False,
                        "no_messages_sent": True,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if not args.watch:
                return
        started = time.monotonic()
        last_status = None
        while time.monotonic() - started < 600:
            state = active_state(home, datetime.now(timezone.utc))
            try:
                result = await poll_qqofficial_login_once(
                    platform_config=PLATFORM,
                    task_id=state["task_id"],
                    bind_key=state["bind_key"],
                )
            except Exception:
                if not args.watch:
                    raise
                await asyncio.sleep(2)
                continue
            stored = save_candidate(result)
            if stored:
                state["completed"] = True
                state_path.write_text(json.dumps(state), encoding="utf-8")
            if result.get("status") != last_status:
                print(
                    json.dumps(
                        {
                            "status": result.get("status"),
                            "candidate_saved": stored,
                            "adapter_enabled": False,
                            "no_messages_sent": True,
                        }
                    ),
                    flush=True,
                )
                last_status = result.get("status")
            if (
                not args.watch
                or stored
                or last_status in ("expired", "error", "denied")
            ):
                return
            await asyncio.sleep(2)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "adapter_enabled": False,
                    "no_messages_sent": True,
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    asyncio.run(main())
