"""Activate only the owner account returned by a confirmed Weixin QR login."""

import json
from local_paths import DATA

data = DATA
home = data / "plugin_data" / "companion_core"
platform = json.loads((home / "weixin-bound.local.json").read_text(encoding="utf-8"))
owner = platform.get("weixin_oc_user_id")
if (
    not owner
    or not platform.get("weixin_oc_token")
    or not platform.get("weixin_oc_account_id")
):
    raise SystemExit("Incomplete owner-confirmed binding; activation refused")
config_path = data / "cmd_config.json"
config = json.loads(config_path.read_text(encoding="utf-8-sig"))
config["platform"] = [
    p for p in config["platform"] if p.get("id") != platform["id"]
] + [platform]
settings_path = home / "settings.local.json"
settings = json.loads(settings_path.read_text(encoding="utf-8"))
settings.setdefault("allowed_senders", {})[platform["id"]] = [owner]
settings.setdefault("bot_account_ids", {})[platform["id"]] = platform[
    "weixin_oc_account_id"
]
# Set the allowlist before writing the adapter config, to fail closed on restart.
settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    "Owner-only private Weixin adapter configured; restart required. No message sent."
)
