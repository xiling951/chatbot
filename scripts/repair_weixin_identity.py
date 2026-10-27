"""One-time correction for the pinned adapter's metadata-only self_id.

Never merges people. Preserve the existing opaque person/scope and its ledger,
but qualify the current confirmed bot with its real QR-bound account ID.
"""

import argparse
import json
import socket
import sqlite3
from contextlib import closing
from pathlib import Path

from local_paths import DATA, PLUGIN_DATA


def repair(db, settings, bound, active):
    platform = bound.get("id")
    owner = bound.get("weixin_oc_user_id")
    account = bound.get("weixin_oc_account_id")
    if (
        not platform
        or not owner
        or not account
        or not bound.get("weixin_oc_token")
        or settings.get("allowed_senders", {}).get(platform) != [owner]
        or active.get("id") != platform
        or active.get("weixin_oc_account_id") != account
        or active.get("weixin_oc_user_id") != owner
    ):
        raise ValueError(
            "Current active account, confirmed binding and sole owner must match"
        )
    legacy = f"{platform}:{platform}"
    canonical = f"{platform}:{account}"
    before = db.execute(
        "SELECT person FROM identities WHERE platform=? AND bot=? AND sender=?",
        (platform, legacy, owner),
    ).fetchone()
    existing = db.execute(
        "SELECT person FROM identities WHERE platform=? AND bot=? AND sender=?",
        (platform, canonical, owner),
    ).fetchone()
    if before and existing:
        raise ValueError("Both identity forms exist; automatic merge is forbidden")
    with db:
        if before:
            db.execute(
                "UPDATE identities SET bot=? WHERE platform=? AND bot=? AND sender=?",
                (canonical, platform, legacy, owner),
            )
    settings.setdefault("bot_account_ids", {})[platform] = account
    return int(bool(before))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    with socket.socket() as check:
        if check.connect_ex(("127.0.0.1", 6185)) == 0:
            raise SystemExit("Stop the instance before changing the identity key")
    database = PLUGIN_DATA / "memory.sqlite"
    if not args.backup.exists() or args.backup.resolve() == database.resolve():
        raise SystemExit("A separate consistent pre-change backup is required")
    settings_path = PLUGIN_DATA / "settings.local.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    bound = json.loads(
        (PLUGIN_DATA / "weixin-bound.local.json").read_text(encoding="utf-8")
    )
    config = json.loads((DATA / "cmd_config.json").read_text(encoding="utf-8-sig"))
    active = next(p for p in config["platform"] if p.get("id") == bound["id"])
    with (
        closing(sqlite3.connect(database)) as db,
        closing(
            sqlite3.connect(f"file:{args.backup.as_posix()}?mode=ro", uri=True)
        ) as backup,
    ):
        if (
            db.execute("PRAGMA user_version").fetchone()[0] != 4
            or backup.execute("PRAGMA user_version").fetchone()[0] != 4
            or db.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
            != backup.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
        ):
            raise SystemExit(
                "Schema and deletion ledger identity must match the backup"
            )
        changed = repair(db, settings, bound, active)
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Current confirmed identity key corrected: {changed} row; person/scope and deletion ledger preserved. No messages sent."
    )


if __name__ == "__main__":
    main()
