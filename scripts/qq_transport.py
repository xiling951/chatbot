"""Optional, process-local proxy compatibility for the pinned QQ SDK only.

Never change aiohttp itself or other AstrBot adapters. The configured proxy must
be the owner's existing unauthenticated loopback HTTP CONNECT service.
"""

import asyncio
import inspect
import ssl
from types import SimpleNamespace
from urllib.parse import urlsplit


def validate_proxy(value):
    url = urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname not in ("127.0.0.1", "::1")
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or not url.port
        or not 1 <= url.port <= 65535
    ):
        raise ValueError("QQ proxy must be a configured loopback HTTP service")
    return value.rstrip("/")


def parse_windows_proxy(enabled, server):
    """Accept an explicitly enabled static loopback proxy; never evaluate PAC."""
    if enabled != 1 or not isinstance(server, str) or not server:
        raise ValueError("Windows has no enabled static proxy")
    if any(character.isspace() for character in server):
        raise ValueError("Ambiguous Windows proxy endpoint")
    if "=" in server or ";" in server:
        endpoints = {}
        for part in server.split(";"):
            if not part:
                continue
            scheme, separator, endpoint = part.partition("=")
            if not separator or scheme in endpoints:
                raise ValueError("Ambiguous Windows proxy mapping")
            endpoints[scheme] = endpoint
        server = endpoints.get("https", "")
        if not server:
            raise ValueError("Windows has no explicit HTTPS proxy")
    return validate_proxy(server if "://" in server else "http://" + server)


def resolve_proxy(value):
    if value != "windows":
        return validate_proxy(value)
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
    ) as key:
        enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
        server = winreg.QueryValueEx(key, "ProxyServer")[0]
    return parse_windows_proxy(enabled, server)


class AiohttpView:
    def __init__(self, original, factory):
        self.original, self.ClientSession = original, factory

    def __getattr__(self, name):
        return getattr(self.original, name)


def install_proxy(value):
    proxy = resolve_proxy(value)
    import aiohttp
    import botpy.gateway
    import botpy.http
    import botpy.robot

    old = getattr(botpy.http, "_companion_proxy_installation", None)
    if old:
        if old.proxy != proxy:
            raise ValueError("Restart QQ runtime before changing its loopback proxy")
        return old
    original = aiohttp.ClientSession
    if "proxy" not in inspect.signature(original).parameters:
        raise RuntimeError("Pinned aiohttp session proxy option unavailable")

    def session(*args, **kwargs):
        if kwargs.get("proxy") not in (None, proxy):
            raise ValueError("QQ session cannot select another proxy")
        kwargs["proxy"] = proxy
        kwargs["trust_env"] = False
        connector = kwargs.get("connector")
        context = getattr(connector, "_ssl", None)
        if isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_NONE:
            # The pinned SDK creates a generic SSLContext for API and WSS.
            # Secure these newly owned connectors with a verified client context.
            connector._ssl = ssl.create_default_context()
            if connector.force_close:
                # Reuse the verified QQ API tunnel instead of making a fresh
                # proxy/TLS connection for every GET in the login sequence.
                connector._force_close = False
                connector._keepalive_timeout = 15.0
        return original(*args, **kwargs)

    installation = SimpleNamespace(
        proxy=proxy,
        factory=session,
        original=original,
        robot_view=botpy.robot.aiohttp,
        http_view=botpy.http.aiohttp,
        gateway_factory=botpy.gateway.ClientSession,
        original_request=botpy.http.BotHttp.request,
    )

    async def request(client, route, retry_time=0, **kwargs):
        if retry_time > 2:
            return None
        if route.method != "GET":
            return await installation.original_request(
                client, route, retry_time=retry_time, **kwargs
            )
        # A read can tolerate one fresh connection after a connect failure.
        # Never introduce an additional POST/upload/message attempt.
        for attempt in range(2):
            try:
                result = await installation.original_request(
                    client, route, retry_time=2, **kwargs
                )
                if result is not None or attempt:
                    return result
            except (aiohttp.ClientConnectorError, TimeoutError):
                if attempt:
                    raise
            await asyncio.sleep(0.5)
        return None

    botpy.robot.aiohttp = AiohttpView(aiohttp, session)
    botpy.http.aiohttp = AiohttpView(aiohttp, session)
    botpy.gateway.ClientSession = session
    botpy.http.BotHttp.request = request
    botpy.http._companion_proxy_installation = installation
    return installation


def uninstall_proxy(installation):
    """For isolated verification; normal production changes require restart."""
    import botpy.gateway
    import botpy.http
    import botpy.robot

    if getattr(botpy.http, "_companion_proxy_installation", None) is not installation:
        raise ValueError("Not the owned QQ compatibility installation")
    botpy.robot.aiohttp = installation.robot_view
    botpy.http.aiohttp = installation.http_view
    botpy.gateway.ClientSession = installation.gateway_factory
    botpy.http.BotHttp.request = installation.original_request
    del botpy.http._companion_proxy_installation
