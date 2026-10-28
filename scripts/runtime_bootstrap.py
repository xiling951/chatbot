"""Start the pinned AstrBot entry point after scoped host compatibility setup."""

import json
import os
import runpy
import sys
from contextlib import suppress
from datetime import UTC, datetime

from local_paths import CODE_ROOT, PLUGIN_DATA
from qq_transport import install_proxy


def main():
    record = {
        "at": datetime.now(UTC).isoformat(),
        "qq_loopback_proxy_installed": False,
    }
    try:
        settings = json.loads(
            (PLUGIN_DATA / "settings.local.json").read_text(encoding="utf-8")
        )
        if settings.get("qq_loopback_proxy"):
            install_proxy(settings["qq_loopback_proxy"])
            record["qq_loopback_proxy_installed"] = True
            record["qq_proxy_source"] = (
                "windows_static"
                if settings["qq_loopback_proxy"] == "windows"
                else "explicit_loopback"
            )
    except Exception as exc:  # noqa: BLE001 -- optional QQ setup must not block startup
        # An optional QQ problem must not prevent WeChat/Web from starting.
        record["error_type"] = type(exc).__name__
    with suppress(OSError):
        (PLUGIN_DATA / "qq-transport-status.local.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
    os.chdir(CODE_ROOT)
    sys.path.insert(0, str(CODE_ROOT))
    sys.argv = [str(CODE_ROOT / "main.py")]
    runpy.run_path(str(CODE_ROOT / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
