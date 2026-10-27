"""Deploy the local plugin without modifying the pinned AstrBot source."""

import argparse
import copy
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

import yaml
from local_paths import CODE_ROOT, DATA, RUNTIME_ROOT

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
os.environ["ASTRBOT_ROOT"] = str(RUNTIME_ROOT)

from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402
from astrbot.core.utils.auth_password import hash_dashboard_password  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-dsh", action="store_true")
    parser.add_argument("--dsh-home", type=Path)
    parser.add_argument("--reset-local-admin", action="store_true")
    args = parser.parse_args()
    data = DATA
    data.mkdir(exist_ok=True)
    config_path = data / "cmd_config.json"
    config = (
        json.loads(config_path.read_text(encoding="utf-8-sig"))
        if config_path.exists()
        else copy.deepcopy(DEFAULT_CONFIG)
    )
    config["dashboard"]["host"] = "127.0.0.1"
    config["dashboard"]["port"] = 6185
    if args.reset_local_admin or not config["dashboard"].get("pbkdf2_password"):
        password = secrets.token_urlsafe(24) + "aB3"
        config["dashboard"].update(
            password="",
            pbkdf2_password=hash_dashboard_password(password),
            password_storage_upgraded=True,
            jwt_secret=secrets.token_hex(32),
        )
        (data / "admin.local.json").write_text(
            json.dumps(
                {
                    "url": "http://127.0.0.1:6185",
                    "username": "astrbot",
                    "password": password,
                }
            ),
            encoding="utf-8",
        )
    if args.reuse_dsh:
        desktop_home = Path.home() / "AppData" / "Roaming" / "dsh-desktop" / "harness"
        dsh_home = args.dsh_home or Path(os.environ.get("DSH_HOME") or desktop_home)
        credentials = yaml.safe_load(
            (dsh_home / ".credentials.yaml").read_text(encoding="utf-8")
        )
        key = credentials.get("refs", {}).get("DEEPSEEK_API_KEY")
        if not isinstance(key, str) or not key:
            raise RuntimeError("DSH DeepSeek credential unavailable")
        source = {
            "id": "companion-deepseek_source",
            "type": "openai_chat_completion",
            "key": [key],
            "api_base": "https://api.deepseek.com",
            "timeout": 30,
            "request_max_retries": 1,
        }
        config["provider_sources"] = [
            p for p in config["provider_sources"] if p.get("id") != source["id"]
        ] + [source]
        provider = {
            "id": "companion-deepseek",
            "provider_source_id": source["id"],
            "enable": True,
            "model": "deepseek-flash",
            "custom_extra_body": {"thinking": {"type": "disabled"}, "max_tokens": 512},
        }
        config["provider"] = [
            p for p in config["provider"] if p.get("id") != provider["id"]
        ] + [provider]
    config["provider_settings"].update(
        enable=False,
        web_search=False,
        streaming_response=False,
        default_provider_id="companion-deepseek",
    )
    config["log_level"] = "WARNING"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    plugin = data / "plugins" / "astrbot_plugin_companion_core"
    shutil.copytree(
        PROJECT / "plugins" / "astrbot_plugin_companion_core",
        plugin,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(PROJECT / "config" / "persona.yaml", plugin / "persona.yaml")
    shutil.copy2(
        PROJECT / "config" / "messaging_policy.yaml", plugin / "messaging_policy.yaml"
    )
    shutil.copy2(
        PROJECT / "config" / "emotion_policy.yaml", plugin / "emotion_policy.yaml"
    )
    home = data / "plugin_data" / "companion_core"
    home.mkdir(parents=True, exist_ok=True)
    settings = home / "settings.local.json"
    if not settings.exists():
        settings.write_text(
            json.dumps(
                {
                    "model_enabled": False,
                    "provider_id": "companion-deepseek",
                    "allowed_origins": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    allowance = home / "allowance.local.json"
    if not allowance.exists():
        allowance.write_text(json.dumps({"remaining_calls": 0}), encoding="utf-8")
    print(
        "Deployed plugin; existing model allowance and enabled state preserved. No API calls made."
    )
    (PROJECT / "config" / "runtime_root.local.json").write_text(
        json.dumps(
            {"runtime_root": str(config_path.resolve().parent.parent)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
