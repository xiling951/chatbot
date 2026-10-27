"""Owner-only two-reminder test; enabling requires explicit operator confirmation.

Inspect is read-only. No target or contact ID can be supplied on the command line.
The owner must match the QR-confirmed binding, allowlist and existing trusted scope.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from filelock import FileLock
from local_paths import DATA, PLUGIN_DATA

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore
from plugins.astrbot_plugin_companion_core.memory import MemoryStore, digest


def owner_scope(db, settings, platform, persona):
    owner = platform.get("weixin_oc_user_id")
    platform_id = platform.get("id")
    account = platform.get("weixin_oc_account_id")
    if (
        not owner
        or not account
        or not platform.get("weixin_oc_token")
        or settings.get("allowed_senders", {}).get(platform_id) != [owner]
    ):
        raise ValueError("Confirmed binding must match the sole owner allowlist")
    rows = db.execute(
        "SELECT s.scope FROM scope_metadata s JOIN identities i ON s.person=i.person WHERE i.platform=? AND i.bot=? AND i.sender=? AND s.persona=? AND s.visibility='private'",
        (platform_id, f"{platform_id}:{account}", owner, persona),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("Exactly one trusted current owner scope is required")
    return rows[0][0], f"{platform_id}:FriendMessage:{owner}"


def update_settings(mutator):
    path = PLUGIN_DATA / "settings.local.json"
    with (
        FileLock(str(path) + ".lock", timeout=5),
        path.open("r+", encoding="utf-8") as stream,
    ):
        settings = json.load(stream)
        mutator(settings)
        stream.seek(0)
        json.dump(settings, stream, ensure_ascii=False, indent=2)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--inspect", action="store_true")
    action.add_argument("--enable", action="store_true")
    action.add_argument("--disable", action="store_true")
    parser.add_argument("--confirmed-owner-test", action="store_true")
    parser.add_argument("--override-quiet-hours", action="store_true")
    parser.add_argument("--minutes", type=int, default=10)
    args = parser.parse_args()
    plugin = DATA / "plugins/astrbot_plugin_companion_core"
    settings = json.loads(
        (PLUGIN_DATA / "settings.local.json").read_text(encoding="utf-8")
    )
    platform = json.loads(
        (PLUGIN_DATA / "weixin-bound.local.json").read_text(encoding="utf-8")
    )
    persona = yaml.safe_load((plugin / "persona.yaml").read_text(encoding="utf-8"))
    defaults = yaml.safe_load(
        (plugin / "messaging_policy.yaml").read_text(encoding="utf-8")
    )
    version = yaml.safe_load((plugin / "metadata.yaml").read_text(encoding="utf-8"))[
        "version"
    ]
    database = PLUGIN_DATA / "memory.sqlite"
    with closing(
        sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    ) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 4:
            raise SystemExit("Schema 4 must be deployed first; no migration performed")
        scope, target = owner_scope(db, settings, platform, persona["id"])
    current = datetime.now(timezone.utc)
    if args.inspect:
        print(
            json.dumps(
                {
                    "deployed_version": version,
                    "trusted_owner_scope_matches": True,
                    "timezone": defaults["timezone"],
                    "proactive_enabled_setting": bool(
                        settings.get("proactive_enabled")
                    ),
                    "planned_reminders": 2,
                    "test_window_minutes": args.minutes,
                    "no_messages_sent": True,
                },
                ensure_ascii=False,
            )
        )
        return
    if args.enable and (
        version != "v0.4.1"
        or not args.confirmed_owner_test
        or not 8 <= args.minutes <= 15
    ):
        raise SystemExit(
            "Deploy v0.4.1, obtain explicit owner confirmation, and use an 8–15 minute test window"
        )
    memory = MemoryStore(database, PLUGIN_DATA / "deletions.sqlite")
    core = DeliveryStore(memory, defaults)
    try:
        if args.disable:

            def disable(config):
                config["proactive_enabled"] = False
                config.pop("proactive_test_until", None)

            update_settings(disable)
            core.authorize(
                scope,
                target,
                False,
                current,
                zone=defaults["timezone"],
                maximum=defaults["maximum_per_local_day"],
                quiet_start=defaults["quiet_start_hour"],
                quiet_end=defaults["quiet_end_hour"],
            )
            print(
                "Owner test stopped; default quiet hours restored. Already submitted messages may still arrive."
            )
            return
        if settings.get("proactive_enabled"):
            raise SystemExit(
                "An active proactive configuration already exists; inspect it instead of replacing it"
            )
        pending = core.db.execute(
            "SELECT count(*) FROM tasks WHERE scope=? AND state IN ('pending','running')",
            (scope,),
        ).fetchone()[0]
        if (
            pending
            or not core.db.execute(
                "SELECT enabled FROM scopes WHERE scope=?", (scope,)
            ).fetchone()[0]
        ):
            raise SystemExit(
                "Existing pending tasks or paused memory require review; nothing scheduled"
            )
        from plugins.astrbot_plugin_companion_core.scheduling import in_quiet_hours

        if (
            in_quiet_hours(
                current,
                defaults["timezone"],
                defaults["quiet_start_hour"],
                defaults["quiet_end_hour"],
            )
            and not args.override_quiet_hours
        ):
            raise SystemExit(
                "This is quiet time; explicit temporary exception is required"
            )
        quiet_start, quiet_end = (
            defaults["quiet_start_hour"],
            defaults["quiet_end_hour"],
        )
        if args.override_quiet_hours:
            quiet_start = (
                current.astimezone(ZoneInfo(defaults["timezone"])).hour + 12
            ) % 24
            quiet_end = (quiet_start + 1) % 24
        run_id = uuid.uuid4().hex
        until = current + timedelta(minutes=args.minutes)
        jobs = []
        try:
            core.authorize(
                scope,
                target,
                True,
                current,
                zone=defaults["timezone"],
                maximum=2,
                quiet_start=quiet_start,
                quiet_end=quiet_end,
            )
            for index, seconds in enumerate((30, 90), 1):
                scheduled = current + timedelta(seconds=seconds)
                task = core.create(
                    memory.begin(scope, f"owner-confirmed-test:{run_id}:{index}"),
                    f"【定时测试 {index}/2】看到这条请回复“收到{index}”。",
                    scheduled,
                    scheduled + timedelta(seconds=300),
                    current,
                    key=f"{run_id}:{index}",
                )
                jobs.append(
                    {
                        "id": task,
                        "scheduled_at": scheduled.isoformat(),
                        "device_observation": "not_observed",
                        "read_state": "unknown",
                    }
                )

            def enable(config):
                config["proactive_enabled"] = True
                config["proactive_test_until"] = until.isoformat()
                targets = config.setdefault("proactive_verified_targets", [])
                if target not in targets:
                    targets.append(target)

            update_settings(enable)
        except Exception:
            core.authorize(
                scope,
                target,
                False,
                current,
                zone=defaults["timezone"],
                maximum=2,
                quiet_start=defaults["quiet_start_hour"],
                quiet_end=defaults["quiet_end_hour"],
            )
            update_settings(lambda config: config.update(proactive_enabled=False))
            raise
        report = {
            "run_id": run_id,
            "scope_hash": scope,
            "target_hash": digest(target),
            "authorization": "Explicit owner confirmation in active Codex chat; QR binding and allowlist matched",
            "timezone": defaults["timezone"],
            "started_at": current.isoformat(),
            "hard_stop_at": until.isoformat(),
            "quiet_override": args.override_quiet_hours,
            "tasks": jobs,
        }
        output = PLUGIN_DATA / f"proactive-test-{run_id}.local.json"
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "scheduled": 2,
                    "local_times": [
                        datetime.fromisoformat(j["scheduled_at"])
                        .astimezone(ZoneInfo(defaults["timezone"]))
                        .strftime("%H:%M:%S")
                        for j in jobs
                    ],
                    "timezone": defaults["timezone"],
                    "hard_stop_at": until.isoformat(),
                    "private_evidence": str(output),
                },
                ensure_ascii=False,
            )
        )
    finally:
        memory.close()


if __name__ == "__main__":
    main()
