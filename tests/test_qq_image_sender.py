"""Pinned actual SDK, fake HTTP: no mobile messages or account access."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import botpy
from botpy.http import BotHttp, Route

from plugins.astrbot_plugin_companion_core.emotes import send_qq_image_once


class QQImageTests(unittest.IsolatedAsyncioTestCase):
    def event(self, response):
        raw = botpy.message.C2CMessage.__new__(botpy.message.C2CMessage)
        raw.id = "original-message"
        raw.author = SimpleNamespace(user_openid="owner")
        request = AsyncMock(side_effect=response)
        return SimpleNamespace(
            message_obj=SimpleNamespace(raw_message=raw, message_id=raw.id),
            get_sender_id=lambda: "owner",
            get_group_id=lambda: "",
            bot=SimpleNamespace(
                api=SimpleNamespace(_http=SimpleNamespace(request=request))
            ),
        )

    async def test_upload_then_single_reply_no_cookie_url_or_proactive_fallback(self):
        event = self.event([{"file_info": "receipt"}, {"id": "message-result"}])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "original.png"
            path.write_bytes(b"owned fixture")
            await send_qq_image_once(event, path, lambda: True)
        calls = event.bot.api._http.request.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [c.args[0].path for c in calls],
            ["/v2/users/{openid}/files", "/v2/users/{openid}/messages"],
        )
        self.assertTrue(all(c.kwargs["retry_time"] == 2 for c in calls))
        self.assertTrue(all(c.args[0].parameters == {"openid": "owner"} for c in calls))
        self.assertFalse(calls[0].kwargs["json"]["srv_send_msg"])
        final = calls[1].kwargs["json"]
        self.assertEqual(final["msg_id"], "original-message")
        self.assertEqual(final["msg_seq"], 10001)
        self.assertEqual(final["media"], {"file_info": "receipt"})

    async def test_unknown_upload_or_final_failure_never_retries_or_drops_reply_id(
        self,
    ):
        for response, expected_calls in (
            ([None], 1),
            ([{"file_info": "receipt"}, None], 2),
            ([{"file_info": "receipt"}, OSError("uncertain")], 2),
        ):
            event = self.event(response)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "original.png"
                path.write_bytes(b"owned fixture")
                with self.assertRaises((ValueError, OSError)):
                    await send_qq_image_once(event, path, lambda: True)
            self.assertEqual(event.bot.api._http.request.await_count, expected_calls)

    async def test_owner_group_source_and_revocation_fail_closed(self):
        event = self.event([{"file_info": "receipt"}])
        event.get_sender_id = lambda: "other"
        with self.assertRaises(ValueError):
            await send_qq_image_once(event, Path("not-opened.png"), lambda: True)
        event.bot.api._http.request.assert_not_awaited()
        event = self.event([{"file_info": "receipt"}])
        gate = iter((True, False))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "original.png"
            path.write_bytes(b"owned fixture")
            with self.assertRaises(ValueError):
                await send_qq_image_once(event, path, lambda: next(gate))
        event.bot.api._http.request.assert_awaited_once()

    async def test_actual_sdk_retry_budget_two_makes_one_network_attempt_on_connection_reset(
        self,
    ):
        # This exercises real botpy recursion, rather than only asserting a mock argument.
        class ResetContext:
            async def __aenter__(self):
                raise ConnectionResetError("fixture reset")

            async def __aexit__(self, *args):
                return False

        session = SimpleNamespace(
            request=Mock(return_value=ResetContext()), closed=True
        )
        http = BotHttp(timeout=1)
        http._session = session
        http.check_session = AsyncMock()
        result = await http.request(
            Route("POST", "/v2/users/fixture/messages"),
            retry_time=2,
            json={"msg_id": "original"},
        )
        self.assertIsNone(result)
        session.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
