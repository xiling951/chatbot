"""One anonymous metadata GET, with evidence; no discovery task or IM send."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from local_paths import PLUGIN_DATA, PROJECT_ROOT
from qq_transport import resolve_proxy

sys.path.insert(0, str(PROJECT_ROOT))
from plugins.astrbot_plugin_companion_core.bilibili import (  # noqa: E402
    BilibiliReader,
    ReadFailed,
    read_bounded,
    video_id,
)


async def check(value, proxy):
    identifier = video_id(value)
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "bvid": identifier,
        "mode": "explicit_loopback_connect" if proxy else "direct_public_dns",
        "successful": False,
        "discovery_activity_created": False,
        "account_credentials_used": False,
        "im_sent": False,
        "system_network_changed": False,
    }
    if (PLUGIN_DATA / "STOP").exists():
        report["failure"] = "locally_stopped"
    else:
        try:
            endpoint = resolve_proxy(proxy) if proxy else None
            metadata = await read_bounded(BilibiliReader(proxy=endpoint), identifier)
            report.update(successful=True, metadata=metadata.data())
        except ReadFailed as exc:
            report["failure"] = str(exc)
        except (ValueError, OSError):
            report["failure"] = "local_proxy_unavailable"
    path = PLUGIN_DATA / "bilibili-transport-preflight.local.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.write_text(
        json.dumps({"runs": previous.get("runs", []) + [report]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bvid", default="BV1xx411c7mD")
    parser.add_argument("--proxy", help="Explicit loopback URL, or windows for its current static proxy")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args.bvid, args.proxy)))
