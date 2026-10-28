"""Summarize the preregistered window without inventing missing observations."""

import argparse
import json
import math
from datetime import datetime, timedelta, timezone

from local_paths import PLUGIN_DATA

WINDOW_START = datetime.fromisoformat("2026-10-27T02:14:06.266877+00:00")
SLOT_SECONDS = 30
WINDOW_HOURS = 72


def classify(row, layer):
    if layer in ("process", "dashboard"):
        value = row.get("process_alive" if layer == "process" else "dashboard_http_ok")
        return "healthy" if value is True else "failed" if value is False else "unknown"
    if layer == "qq_adapter_report":
        channel = (row.get("channels") or {}).get("companion-qq")
        if not isinstance(channel, dict):
            return "unknown"
    else:
        channel = row.get("channel") or {}
    values = [
        row.get("process_alive"),
        row.get("dashboard_http_ok"),
        channel.get("adapter_running"),
        channel.get("configured"),
    ]
    if any(v is False for v in values) or channel.get("has_reported_error") is True:
        return "failed"
    if all(v is True for v in values) and channel.get("has_reported_error") is False:
        return "healthy"
    return "unknown"


def summarize(rows, current, *, start=WINDOW_START, hours=WINDOW_HOURS):
    if current.tzinfo is None or start.tzinfo is None:
        raise ValueError("Observation times must include timezone")
    expected = int(hours * 3600 / SLOT_SECONDS)
    elapsed = min(
        expected, max(0, math.floor((current - start).total_seconds() / SLOT_SECONDS))
    )
    bins = {
        layer: {}
        for layer in ("process", "dashboard", "adapter_report", "qq_adapter_report")
    }
    rank = {"healthy": 0, "unknown": 1, "failed": 2}
    ignored = 0
    model_probes = 0
    for original in rows:
        for row in (original, original.get("after_recovery") or {}):
            if not row:
                continue
            try:
                at = datetime.fromisoformat(row["at"])
                if at.tzinfo is None:
                    raise ValueError("Missing timezone")
                index = math.floor((at - start).total_seconds() / SLOT_SECONDS)
            except (KeyError, ValueError, TypeError):
                ignored += 1
                continue
            if not 0 <= index < elapsed:
                continue
            if row.get("model_probe") not in (None, "not_run"):
                model_probes += 1
            for layer, slots in bins.items():
                value = classify(row, layer)
                if index not in slots or rank[value] > rank[slots[index]]:
                    slots[index] = value
    layers = {}
    for layer, slots in bins.items():
        counts = {key: sum(v == key for v in slots.values()) for key in rank}
        counts["unknown"] += elapsed - len(slots)
        layers[layer] = dict(
            **counts,
            minimum_available_percent=round(100 * counts["healthy"] / elapsed, 4)
            if elapsed
            else None,
            maximum_available_percent=round(
                100 * (counts["healthy"] + counts["unknown"]) / elapsed, 4
            )
            if elapsed
            else None,
        )
    return {
        "window_start": start.isoformat(),
        "window_end_exclusive": (start + timedelta(hours=hours)).isoformat(),
        "checked_at": current.isoformat(),
        "slot_seconds": SLOT_SECONDS,
        "expected_slots": expected,
        "completed_slots": elapsed,
        "not_elapsed_slots": expected - elapsed,
        "window_status": "complete" if elapsed == expected else "in_progress",
        "layers": layers,
        "invalid_timestamps": ignored,
        "recorded_model_probe_samples": model_probes,
        "model_available_percent": None,
        "device_delivery_percent": None,
        "legacy_adapter_scope": "adapter_report is companion-weixin only; older records have no QQ observation and remain unknown",
        "notes": "Worst state per completed slot; missing is unknown; open/future slots not scored. Local HTTP/adapter state does not prove model or device availability.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    rows, malformed = [], 0
    for path in sorted(
        (PLUGIN_DATA / "observations").glob("availability-*.local.jsonl")
    ):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Not an observation")
                rows.append(row)
            except ValueError:
                malformed += 1
    report = summarize(rows, datetime.now(timezone.utc))
    report["malformed_lines"] = malformed
    if args.save:
        (PLUGIN_DATA / "availability-summary.local.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
