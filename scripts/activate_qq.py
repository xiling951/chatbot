"""Enable a scanned QQ adapter with no sender allowed until a one-shot claim."""

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from local_paths import DATA, PLUGIN_DATA
from runtime_watchdog import read_json, process_start_ticks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmed-owner-test", action="store_true")
    args = parser.parse_args()
    if not args.confirmed_owner_test:
        raise SystemExit(
            "Explicit authorization for this owner's QQ private chat is required"
        )
    process_file = DATA / "companion-process.local.json"
    if process_file.exists():
        process = read_json(process_file)
        if process_start_ticks(int(process["id"])) == int(process["started_ticks"]):
            raise SystemExit(
                "Stop the owned instance before editing adapter configuration"
            )
    candidate = json.loads(
        (PLUGIN_DATA / "qq-bound.local.json").read_text(encoding="utf-8")
    )
    if (
        candidate.get("type") != "qq_official"
        or candidate.get("id") != "companion-qq"
        or not candidate.get("appid")
        or not candidate.get("secret")
    ):
        raise SystemExit("A confirmed official QQ QR candidate is required")
    settings_path = PLUGIN_DATA / "settings.local.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    identifier = candidate["id"]
    if settings.get("allowed_senders", {}).get(identifier):
        raise SystemExit("QQ already has an owner; no replacement or new claim issued")
    code = secrets.token_hex(8).upper()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    claim = {
        "platform_id": identifier,
        "appid": candidate["appid"],
        "owner_test_authorized": True,
        "code_hash": hashlib.sha256(code.encode()).hexdigest(),
        "expires_at": expires.isoformat(),
        "used": False,
    }
    (PLUGIN_DATA / "qq-owner-claim.local.json").write_text(
        json.dumps(claim, indent=2), encoding="utf-8"
    )
    settings.setdefault("bot_account_ids", {})[identifier] = candidate["appid"]
    settings.setdefault("allowed_senders", {})[identifier] = []
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    config_path = DATA / "cmd_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config["platform"] = [
        p for p in config["platform"] if p.get("id") != identifier
    ] + [dict(candidate, enable=True)]
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "configured": True,
                "owner_enrolled": False,
                "claim_command": "/验证本人 " + code,
                "expires_at": expires.isoformat(),
                "no_messages_sent": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
