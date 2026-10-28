"""SDK-scoped loopback compatibility; no network calls or credentials."""

import sys
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qq_transport import install_proxy, uninstall_proxy, validate_proxy


class QQTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_qq_token_http_and_gateway_sessions_use_explicit_local_proxy(
        self,
    ):
        import aiohttp
        import botpy.robot
        import botpy.http
        import botpy.gateway

        original = aiohttp.ClientSession
        installation = install_proxy("http://127.0.0.1:7890")
        try:
            self.assertIs(aiohttp.ClientSession, original)
            sessions = [
                botpy.robot.aiohttp.ClientSession(),
                botpy.http.aiohttp.ClientSession(),
                botpy.gateway.ClientSession(),
            ]
            try:
                self.assertTrue(all(not s.trust_env for s in sessions))
                self.assertTrue(
                    all(
                        str(s._default_proxy) == "http://127.0.0.1:7890"
                        for s in sessions
                    )
                )
                self.assertIs(botpy.robot.aiohttp.ClientTimeout, aiohttp.ClientTimeout)
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                connector = aiohttp.TCPConnector(ssl=context, force_close=True)
                secure = installation.factory(connector=connector)
                try:
                    self.assertEqual(connector._ssl.verify_mode, ssl.CERT_REQUIRED)
                    self.assertTrue(connector._ssl.check_hostname)
                    self.assertFalse(connector.force_close)
                    self.assertEqual(connector._keepalive_timeout, 15.0)
                finally:
                    await secure.close()
                self.assertIs(install_proxy("http://127.0.0.1:7890/"), installation)
                with self.assertRaises(ValueError):
                    install_proxy("http://127.0.0.1:7891")
                with self.assertRaises(ValueError):
                    installation.factory(proxy="http://127.0.0.1:7891")
            finally:
                for session in sessions:
                    await session.close()
        finally:
            uninstall_proxy(installation)
        self.assertIs(botpy.gateway.ClientSession, original)
        self.assertIs(botpy.robot.aiohttp, aiohttp)
        self.assertIs(botpy.http.aiohttp, aiohttp)

    async def test_rejects_external_authenticated_or_ambiguous_proxy_without_patching(
        self,
    ):
        import aiohttp
        import botpy.gateway

        original = botpy.gateway.ClientSession
        for value in (
            "http://example.com:7890",
            "http://localhost:7890",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "https://127.0.0.1:7890",
            "http://user:secret@127.0.0.1:7890",
            "http://127.0.0.1:7890/path",
            "http://127.0.0.1:7890?redirect=other",
            "http://127.0.0.1:7890#x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                install_proxy(value)
        self.assertIs(botpy.gateway.ClientSession, original)
        self.assertIs(original, aiohttp.ClientSession)
        self.assertEqual(validate_proxy("http://[::1]:7890"), "http://[::1]:7890")

    async def test_only_get_receives_one_extra_connect_attempt_post_keeps_original_budget(
        self,
    ):
        import botpy.http

        calls = []

        async def underlying(client, route, retry_time=0, **kwargs):
            calls.append((route.method, retry_time))
            if len(calls) == 1:
                raise TimeoutError("simulated connect failure")
            return {"id": "fixture"}

        with (
            patch.object(botpy.http.BotHttp, "request", new=underlying),
            patch("qq_transport.asyncio.sleep", new=AsyncMock()),
        ):
            installation = install_proxy("http://127.0.0.1:7890")
            try:
                http = botpy.http.BotHttp(timeout=1)
                value = await http.request(botpy.http.Route("GET", "/gateway/bot"))
                self.assertEqual(value, {"id": "fixture"})
                self.assertEqual(calls, [("GET", 2), ("GET", 2)])
                calls.clear()
                with self.assertRaises(TimeoutError):
                    await http.request(
                        botpy.http.Route("POST", "/v2/users/owner/messages"),
                        retry_time=2,
                    )
                self.assertEqual(calls, [("POST", 2)])
                calls.clear()
                self.assertIsNone(
                    await http.request(
                        botpy.http.Route("GET", "/gateway/bot"), retry_time=3
                    )
                )
                self.assertEqual(calls, [])
            finally:
                uninstall_proxy(installation)


if __name__ == "__main__":
    unittest.main()
