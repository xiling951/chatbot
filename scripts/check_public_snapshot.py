"""Inspect staged Git content for this instance's secrets before public backup.

This checks the actual index, rather than assuming .gitignore hides previously
tracked files. It never prints secret values and does not upload anything.
"""

import json
import subprocess
from pathlib import Path

from local_paths import DATA


def main():
    root = Path(__file__).resolve().parents[1]
    git = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    secrets = []

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {
                    "key",
                    "api_key",
                    "password",
                    "pbkdf2_password",
                    "jwt_secret",
                    "weixin_oc_token",
                    "weixin_oc_user_id",
                    "weixin_oc_account_id",
                }:
                    for text in item if isinstance(item, list) else [item]:
                        if isinstance(text, str) and len(text) > 8:
                            secrets.append(text.encode())
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for path in [
        DATA / "cmd_config.json",
        DATA / "admin.local.json",
        DATA / "plugin_data/companion_core/weixin-bound.local.json",
    ]:
        if path.exists():
            collect(json.loads(path.read_text(encoding="utf-8-sig")))
    names = [
        name
        for name in subprocess.check_output(git + ["ls-files", "-z"])
        .decode()
        .split("\0")
        if name
    ]
    if not names:
        raise SystemExit("No staged/tracked files; no snapshot was inspected")
    violations = []
    for name in names:
        content = subprocess.check_output(git + ["show", ":" + name])
        if (
            any(secret in content for secret in secrets)
            or ".local." in name
            or any(
                part in Path(name).parts for part in ("data", "__pycache__", ".venv")
            )
        ):
            violations.append(name)
    print(
        json.dumps(
            {"inspected_files": len(names), "secret_or_private_file_hits": violations},
            ensure_ascii=False,
        )
    )
    if violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
