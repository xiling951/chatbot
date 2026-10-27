"""Summarize an owned-process drill from existing observations; never restarts."""

import argparse
import json
from datetime import datetime, timezone

from local_paths import DATA, PLUGIN_DATA
from runtime_watchdog import read_json, process_start_ticks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    path = PLUGIN_DATA / "recovery-drill.local.json"
    report = read_json(path)
    started = datetime.fromisoformat(report["started_at"])
    saved = read_json(DATA / "companion-process.local.json")
    changed = (int(saved["id"]), int(saved["started_ticks"])) != (
        int(report["old_id"]),
        int(report["old_started_ticks"]),
    )
    alive = process_start_ticks(int(saved["id"])) == int(saved["started_ticks"])
    evidence = []
    for file in (PLUGIN_DATA / "observations").glob("availability-*.local.jsonl"):
        if file.name[13:23] < started.date().isoformat():
            continue
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if datetime.fromisoformat(row["at"]) < started:
                continue
            after = row.get("after_recovery") or {}
            if (
                row.get("recovery", {}).get("exit_code") == 0
                and after.get("process_alive")
                and after.get("dashboard_http_ok")
            ):
                evidence.append(row)
    if changed and alive and evidence:
        first = min(evidence, key=lambda r: r["after_recovery"]["at"])
        ended = datetime.fromisoformat(first["after_recovery"]["at"])
        report.update(
            status="recovered",
            recovered_at=ended.isoformat(),
            dashboard_recovery_seconds=round((ended - started).total_seconds(), 3),
            new_process_verified=True,
            recovery_owner="registered background watchdog; no manual start issued",
            automated_operation_seconds=first["recovery"]["seconds"],
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
        if args.save:
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "status",
                    "dashboard_recovery_seconds",
                    "target_rto_seconds",
                    "new_process_verified",
                    "recovery_owner",
                    "automated_operation_seconds",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
