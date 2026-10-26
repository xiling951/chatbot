"""Request an official QR and save owner-confirmed login only on this machine."""

import asyncio
import json
import os
import sys
import time

import qrcode
from aiohttp import ClientError
from local_paths import CODE_ROOT, PLUGIN_DATA, RUNTIME_ROOT

os.environ["ASTRBOT_ROOT"] = str(RUNTIME_ROOT)
sys.path.insert(0, str(CODE_ROOT))

from astrbot.core.platform.sources.weixin_oc.login_registration import (  # noqa: E402
    poll_weixin_oc_login_once,
    request_weixin_oc_login_qr,
)


async def main():
    home = PLUGIN_DATA
    platform = {"id": "companion-weixin", "type": "weixin_oc", "enable": True}
    qr = await request_weixin_oc_login_qr(platform)
    image_path = home / "weixin-login.local.png"
    qrcode.make(qr.qrcode_img_content).save(image_path)
    print("QR ready:", image_path, flush=True)
    deadline = time.monotonic() + 600
    last_status = None
    while time.monotonic() < deadline:
        try:
            result = await poll_weixin_oc_login_once(
                platform_config=platform, qrcode=qr.qrcode
            )
        except (TimeoutError, ConnectionError, ClientError):
            print(
                "QR poll timeout; retrying within the original 10-minute limit.",
                flush=True,
            )
            await asyncio.sleep(2)
            continue
        status = result["status"]
        if status == "created":
            platform.update(
                {k: v for k, v in result.items() if k.startswith("weixin_oc_")}
            )
            # Persist a candidate; activation occurs only after a controlled restart.
            (home / "weixin-bound.local.json").write_text(
                json.dumps(platform), encoding="utf-8"
            )
            print(
                "Confirmed by owner; private candidate saved. No message sent.",
                flush=True,
            )
            return
        if status in ("expired", "denied", "error"):
            print("QR status:", status, flush=True)
            return
        if result.get("qr_status", status) != last_status:
            last_status = result.get("qr_status", status)
            print("QR status:", last_status, flush=True)
        await asyncio.sleep(max(qr.interval, 2))
    print("QR polling stopped after 10 minutes; no login assumed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
