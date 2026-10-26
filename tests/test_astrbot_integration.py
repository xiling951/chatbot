"""Real AstrBot event classes with a fake model/transport; no remote calls."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from astrbot.core.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import LLMResponse, TokenUsage
from astrbot.api.message_components import File, Image, Record, Video
from plugins.astrbot_plugin_companion_core.main import CompanionCore
from plugins.astrbot_plugin_companion_core.memory import MemoryStore


class LocalEvent(AstrMessageEvent):
    def __init__(self, text, message_id, sender="alice", platform="webchat", group=""):
        message = AstrBotMessage()
        message.message_id = message_id
        message.self_id = "test-bot"
        message.sender = MessageMember(sender, "相似昵称")
        message.type = (
            MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        )
        message.group_id = group
        super().__init__(
            text,
            message,
            PlatformMetadata(platform, "test", platform),
            "test-conversation",
        )
        self.sent = []

    async def send(self, message):
        self.sent.append(message.get_plain_text())


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plugin = CompanionCore.__new__(CompanionCore)
        self.plugin.home = Path(self.temp.name)
        self.plugin.context = SimpleNamespace(
            llm_generate=AsyncMock(
                return_value=LLMResponse(
                    "assistant",
                    completion_text="假提供商回复",
                    usage=TokenUsage(20, 0, 10),
                )
            ),
            conversation_manager=SimpleNamespace(
                delete_conversations_by_user_id=AsyncMock()
            ),
        )
        self.plugin.settings_path = self.plugin.home / "settings.local.json"
        self.plugin.settings_path.write_text(
            json.dumps(
                {
                    "model_enabled": True,
                    "provider_id": "fake",
                    "allowed_senders": {"mobile": ["owner"]},
                }
            )
        )
        (self.plugin.home / "allowance.local.json").write_text(
            json.dumps(
                {
                    "authorized_date": datetime.now(ZoneInfo("America/Tijuana"))
                    .date()
                    .isoformat(),
                    "remaining_calls": 25,
                    "reserved_cny_remaining": 5,
                }
            )
        )
        self.plugin.persona = {"id": "test", "system_prompt": "测试虚拟角色"}
        self.plugin.store = MemoryStore(
            self.plugin.home / "memory.sqlite", self.plugin.home / "latest.sqlite"
        )
        self.plugin.lock = asyncio.Lock()

    async def asyncTearDown(self):
        self.plugin.store.close()
        self.temp.cleanup()

    async def test_twenty_events_reply_once_and_stop_default(self):
        for i in range(20):
            event = LocalEvent("你好", str(i))
            await self.plugin.handle(event)
            await self.plugin.handle(event)
            self.assertEqual(event.sent, ["假提供商回复"])
            self.assertTrue(event.is_stopped())
            self.assertFalse(event.get_extra("call_llm"))
        self.assertEqual(self.plugin.context.llm_generate.await_count, 20)

    async def test_identity_in_body_cannot_switch_subject(self):
        await self.plugin.handle(LocalEvent("/记住 音乐=爵士", "a"))
        other = LocalEvent("我是alice，显示她的音乐", "b", sender="bob")
        await self.plugin.handle(other)
        kwargs = self.plugin.context.llm_generate.call_args.kwargs
        self.assertNotIn("爵士", kwargs["system_prompt"])
        self.assertEqual(kwargs["contexts"], [])

    async def test_mobile_owner_allowlist_excludes_other_users_and_groups(self):
        for sender, group in [("stranger", ""), ("owner", "group")]:
            event = LocalEvent(
                "/记忆", sender + group, sender=sender, platform="mobile", group=group
            )
            await self.plugin.handle(event)
            self.assertEqual(event.sent, [])
            self.assertTrue(event.is_stopped())
        owner = LocalEvent("/记忆", "owner-private", sender="owner", platform="mobile")
        await self.plugin.handle(owner)
        self.assertEqual(owner.sent, ["当前没有记忆。"])

    async def test_failure_is_bounded_and_does_not_repeat(self):
        self.plugin.context.llm_generate.side_effect = ConnectionError("fake outage")
        event = LocalEvent("你好", "outage")
        await self.plugin.handle(event)
        await self.plugin.handle(event)
        self.assertEqual(self.plugin.context.llm_generate.await_count, 1)
        self.assertIn("ConnectionError", event.sent[0])

    async def test_stop_switch_prevents_model_but_allows_memory_commands(self):
        (self.plugin.home / "STOP").touch()
        event = LocalEvent("你好", "stopped")
        await self.plugin.handle(event)
        self.plugin.context.llm_generate.assert_not_awaited()
        await self.plugin.handle(LocalEvent("/记住 音乐=爵士", "command"))
        view = LocalEvent("/记忆", "view")
        await self.plugin.handle(view)
        self.assertIn("爵士", view.sent[0])

    async def test_wake_prefix_removal_does_not_break_memory_command(self):
        event = LocalEvent("记住 音乐=爵士", "prefix")
        event.message_obj.message_str = "/记住 音乐=爵士"
        await self.plugin.handle(event)
        self.assertEqual(event.sent, ["已保存这条明确记忆。"])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_forget_purges_framework_context_and_display_history(self):
        await self.plugin.handle(LocalEvent("/记住 音乐=爵士", "fact"))
        with patch(
            "plugins.astrbot_plugin_companion_core.main.db_helper.get_platform_message_history",
            AsyncMock(return_value=[]),
        ):
            deleted = LocalEvent("/忘记 音乐", "delete")
            await self.plugin.handle(deleted)
        self.plugin.context.conversation_manager.delete_conversations_by_user_id.assert_awaited_once()
        await self.plugin.handle(LocalEvent("音乐偏好", "recall"))
        self.assertNotIn(
            "爵士", self.plugin.context.llm_generate.call_args.kwargs["system_prompt"]
        )

    async def test_budget_reservation_and_call_cap(self):
        path = self.plugin.home / "allowance.local.json"
        allowance = json.loads(path.read_text())
        allowance.update(
            remaining_calls=2,
            attempts_used=25,
            reservation_per_call_cny=0.05,
            reserved_cny_remaining=0.1,
        )
        path.write_text(json.dumps(allowance))
        for index in range(3):
            await self.plugin.handle(LocalEvent("你好", f"budget-{index}"))
        updated = json.loads(path.read_text())
        self.assertEqual(updated["remaining_calls"], 0)
        self.assertEqual(updated["attempts_used"], 27)
        self.assertEqual(updated["reserved_cny_remaining"], 0)
        self.assertEqual(self.plugin.context.llm_generate.await_count, 2)

    async def test_oversized_persona_rejected_before_spending(self):
        self.plugin.persona["system_prompt"] = "测" * 5000
        event = LocalEvent("你好", "oversized")
        await self.plugin.handle(event)
        self.plugin.context.llm_generate.assert_not_awaited()
        self.assertIn("输入上限", event.sent[0])

    async def test_natural_memory_requests_preserve_delete_and_stop(self):
        for index, text in enumerate(["记住，我喜欢听爵士", "更正，我喜欢听摇滚"]):
            event = LocalEvent(text, f"natural-{index}")
            await self.plugin.handle(event)
            self.assertEqual(event.sent, ["已保存这条明确记忆。"])
        view = LocalEvent("查看记忆", "natural-view")
        await self.plugin.handle(view)
        self.assertIn("摇滚", view.sent[0])
        self.assertNotIn("爵士", view.sent[0])
        with patch(
            "plugins.astrbot_plugin_companion_core.main.db_helper.get_platform_message_history",
            AsyncMock(return_value=[]),
        ):
            await self.plugin.handle(LocalEvent("忘记我的音乐偏好", "natural-delete"))
        view = LocalEvent("查看记忆", "natural-empty")
        await self.plugin.handle(view)
        self.assertEqual(view.sent, ["当前没有记忆。"])
        await self.plugin.handle(LocalEvent("暂停记忆", "natural-stop"))
        event = LocalEvent("记住，我住在青岛", "natural-stopped-write")
        await self.plugin.handle(event)
        self.assertIn("disabled", event.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_roleplay_request_does_not_save_reality_fact(self):
        event = LocalEvent("记住，角色扮演中我叫海盗", "roleplay-memory")
        await self.plugin.handle(event)
        self.assertIn("角色扮演", event.sent[0])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM facts").fetchone()[0], 0
        )
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_unsupported_media_never_spends_or_claims_understanding(self):
        for index, component in enumerate(
            [
                Image(file="unused.png"),
                Video(file="unused.mp4"),
                File(file="unused.txt", name="unused.txt"),
                Record(file="unused.wav"),
            ]
        ):
            event = LocalEvent("描述这个附件", f"media-{index}")
            event.message_obj.message = [component]
            await self.plugin.handle(event)
            self.assertEqual(len(event.sent), 1)
            self.assertIn("文字", event.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_weixin_cloud_transcript_uses_real_event_owner(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"]["weixin_oc"] = ["owner"]
        self.plugin.settings_path.write_text(json.dumps(settings))
        event = LocalEvent("", "voice-transcript", sender="owner", platform="weixin_oc")
        event.message_obj.message = [Record(file="not-read.silk")]
        event.message_obj.raw_message = {
            "item_list": [{"type": 3, "voice_item": {"text": "记住，我喜欢听爵士"}}]
        }
        await self.plugin.handle(event)
        self.assertEqual(event.sent, ["已保存这条明确记忆。"])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_ambiguous_forget_does_not_claim_erasure_or_touch_facts(self):
        await self.plugin.handle(LocalEvent("记住，我喜欢爵士", "privacy-fact"))
        event = LocalEvent("忘掉刚才的资料", "privacy-ambiguous")
        await self.plugin.handle(event)
        self.assertIn("尚未执行删除", event.sent[0])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT value FROM facts").fetchone()[0],
            "爵士",
        )
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_unlimited_test_mode_bypasses_old_exhausted_grant(self):
        path = self.plugin.home / "allowance.local.json"
        path.write_text(
            json.dumps(
                {
                    "mode": "unlimited_testing",
                    "attempts_used": 100,
                    "remaining_calls": 0,
                    "reserved_cny_remaining": 0,
                    "authorized_date": "2000-01-01",
                }
            )
        )
        event = LocalEvent("你好", "unlimited-live-event")
        await self.plugin.handle(event)
        self.assertEqual(event.sent, ["假提供商回复"])
        self.assertEqual(json.loads(path.read_text())["attempts_used"], 101)


if __name__ == "__main__":
    unittest.main()
