"""Read-only QQ SDK transport preflight; no identify frame or IM message."""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

from local_paths import DATA, PLUGIN_DATA
from qq_transport import install_proxy, uninstall_proxy


async def check(use_proxy):
    import botpy.http
    import botpy.gateway

    config = json.loads((DATA / "cmd_config.json").read_text(encoding="utf-8-sig"))
    qq = next(p for p in config["platform"] if p.get("id") == "companion-qq")
    settings = json.loads(
        (PLUGIN_DATA / "settings.local.json").read_text(encoding="utf-8")
    )
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "proxy": use_proxy,
        "im_sent": False,
        "websocket_identified": False,
    }
    installation = None
    http = None
    try:
        if use_proxy:
            installation = install_proxy(
                settings.get("qq_loopback_proxy")
                or os.environ.get("HTTPS_PROXY")
                or os.environ.get("https_proxy")
            )
        http = botpy.http.BotHttp(timeout=8, app_id=qq["appid"], secret=qq["secret"])
        record["phase"] = "token"
        await asyncio.wait_for(http._token.check_token(), timeout=12)
        record["token_obtained"] = bool(http._token.access_token)
        record["phase"] = "self_api"
        robot = await asyncio.wait_for(
            http.request(botpy.http.Route("GET", "/users/@me"), retry_time=2),
            timeout=10,
        )
        record["self_identity_returned"] = isinstance(robot, dict) and bool(
            robot.get("id")
        )
        record["phase"] = "gateway_api"
        gateway = await asyncio.wait_for(
            http.request(botpy.http.Route("GET", "/gateway/bot"), retry_time=2),
            timeout=10,
        )
        address = gateway.get("url", "") if isinstance(gateway, dict) else ""
        parsed = urlsplit(address)
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or not parsed.hostname.endswith(".qq.com")
        ):
            raise ValueError("QQ did not return an official gateway origin")
        record["gateway_metadata"] = True
        record["phase"] = "websocket_hello"
        async with botpy.gateway.ClientSession() as session:
            async with session.ws_connect(address) as websocket:
                hello = await asyncio.wait_for(websocket.receive(), timeout=5)
                record["hello_received"] = (
                    hello.type.name == "TEXT" and json.loads(hello.data).get("op") == 10
                )
                await websocket.close()
        record["phase"] = "finished"
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        if getattr(exc, "host", None):
            record["connector_host"] = exc.host
    finally:
        if http:
            record["token_obtained"] = bool(http._token.access_token)
            await http.close()
        if installation:
            uninstall_proxy(installation)
        path = PLUGIN_DATA / "qq-transport-preflight.local.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        runs = previous.get("runs", [previous] if previous else [])
        path.write_text(
            json.dumps({"runs": runs + [record]}, indent=2), encoding="utf-8"
        )
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", action="store_true")
    args = parser.parse_args()
    logging.getLogger("botpy").setLevel(logging.WARNING)
    asyncio.run(check(args.proxy))
