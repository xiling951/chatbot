"""Apply host-side authorization; unlimited testing must be explicitly selected."""

import argparse
import json
from datetime import datetime, timezone
from filelock import FileLock
import os
from zoneinfo import ZoneInfo
from local_paths import PLUGIN_DATA

home = PLUGIN_DATA
parser = argparse.ArgumentParser()
parser.add_argument("--calls", type=int)
parser.add_argument("--daily-cny", type=float)
parser.add_argument("--unlimited-testing", action="store_true")
parser.add_argument(
    "--extend",
    action="store_true",
    help="Apply a newly explicit user authorization, preserving daily spending",
)
args = parser.parse_args()
allowance_path = home / "allowance.local.json"
if args.unlimited_testing:
    if args.calls is not None or args.daily_cny is not None or args.extend:
        raise SystemExit("Unlimited testing cannot be combined with a finite grant")
    with FileLock(str(allowance_path) + ".lock", timeout=5):
        old = json.loads(allowance_path.read_text(encoding="utf-8"))
        record = {
            "mode": "unlimited_testing",
            "authorized_at": datetime.now(timezone.utc).isoformat(),
            "authorization_scope": "project development and testing; no fixed DeepSeek call/day/monetary cap",
            "attempts_used": old.get("attempts_used", 0),
            "superseded_limits": old.get(
                "superseded_limits",
                {k: v for k, v in old.items() if k != "attempts_used"},
            ),
        }
        with allowance_path.open("w", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
    settings_path = home / "settings.local.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["model_enabled"] = True
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    (home / "STOP").unlink(missing_ok=True)
    print(
        "Explicit unlimited project-testing authorization enabled; attempt history preserved. No API request made."
    )
    raise SystemExit(0)
if args.calls is None or args.daily_cny is None:
    raise SystemExit(
        "Select --unlimited-testing or provide a finite --calls/--daily-cny grant"
    )
if not 0 < args.calls <= (60 if args.extend else 25) or not 0 < args.daily_cny <= 5:
    raise SystemExit("Grant exceeds this workflow's call limit or CNY 5 daily cap")
old = json.loads(allowance_path.read_text(encoding="utf-8"))
today = datetime.now(ZoneInfo("America/Tijuana")).date().isoformat()
if old.get("authorized_date") and not args.extend:
    raise SystemExit("Allowance already granted; no automatic refill allowed")
if args.extend and old.get("authorized_date") != today:
    raise SystemExit(
        "Extension requires the current authorized day; no automatic daily rollover"
    )
config = json.loads(
    (home.parents[1] / "cmd_config.json").read_text(encoding="utf-8-sig")
)
model = next(p for p in config["provider"] if p["id"] == "companion-deepseek")
source = next(
    p for p in config["provider_sources"] if p["id"] == model["provider_source_id"]
)
if (
    model["model"] != "deepseek-flash"
    or source["api_base"].rstrip("/") != "https://api.deepseek.com"
    or model["custom_extra_body"]
    != {"thinking": {"type": "disabled"}, "max_tokens": 512}
    or source.get("request_max_retries") != 1
):
    raise SystemExit(
        "Budget reservation only supports the verified capped official Flash configuration"
    )
used = old.get("attempts_used", 25 - old["remaining_calls"]) if args.extend else 0
reservation = 0.05
daily_limit = min(args.daily_cny, old.get("daily_limit_cny", args.daily_cny))
remaining_cny = round(
    daily_limit - used * reservation,
    4,
)
remaining_calls = (
    old.get("remaining_calls", 0) + args.calls if args.extend else args.calls
)
if remaining_calls * reservation > remaining_cny:
    raise SystemExit("Grant would exceed the same day's conservative CNY budget")
allowance = {
    "authorized_date": today,
    "remaining_calls": remaining_calls,
    "attempts_used": used,
    "daily_limit_cny": daily_limit,
    "reserved_cny_remaining": remaining_cny,
    "reservation_per_call_cny": reservation,
}
allowance_path.write_text(json.dumps(allowance, indent=2), encoding="utf-8")
settings_path = home / "settings.local.json"
settings = json.loads(settings_path.read_text(encoding="utf-8"))
settings["model_enabled"] = True
settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
(home / "STOP").unlink(missing_ok=True)
print("Finite authorized test allowance enabled; no API request made by this command.")
