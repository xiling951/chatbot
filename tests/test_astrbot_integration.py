"""Real AstrBot event classes with a fake model/transport; no remote calls."""

import asyncio
import json
import re
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
from plugins.astrbot_plugin_companion_core.delivery import DeliveryStore
from plugins.astrbot_plugin_companion_core.emotion import EmotionState
from plugins.astrbot_plugin_companion_core.delivery_worker import DefinitelyNotAccepted
from astrbot.core.platform.message_session import MessageSesion


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
        self.plugin.delivery = DeliveryStore(self.plugin.store)
        self.plugin.emotion = EmotionState(self.plugin.store)
        self.plugin.messaging_defaults = {
            "enabled": False,
            "timezone": "America/Tijuana",
            "maximum_per_local_day": 2,
            "quiet_start_hour": 23,
            "quiet_end_hour": 8,
            "default_grace_seconds": 300,
        }

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

    async def test_automatic_memory_conflict_does_not_poison_history(self):
        await self.plugin.handle(LocalEvent("/自动记忆 开", "auto-on"))
        first = LocalEvent("我喜欢听爵士", "auto-fact")
        await self.plugin.handle(first)
        self.assertIn("已记住", first.sent[0])
        conflict = LocalEvent("我喜欢听摇滚", "auto-conflict")
        await self.plugin.handle(conflict)
        self.assertIn("待确认", conflict.sent[0])
        scope = self.plugin.store.db.execute("SELECT scope FROM scopes").fetchone()[0]
        self.assertEqual(self.plugin.store.facts(scope)[0]["value"], "爵士")
        history = self.plugin.store.history(scope, conflict.get_session_id())
        self.assertNotIn("摇滚", str(history))
        identifier = self.plugin.store.candidates(scope)[0]["id"][:12]
        await self.plugin.handle(LocalEvent("/确认记忆 " + identifier, "auto-confirm"))
        await self.plugin.handle(LocalEvent("我平时听什么", "auto-recall"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertIn("摇滚", request["system_prompt"])
        self.assertNotIn("爵士", str(request["contexts"]) + request["system_prompt"])

    async def test_automatic_off_and_failed_model_notice_are_honest(self):
        await self.plugin.handle(LocalEvent("我喜欢听爵士", "off-fact"))
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM facts").fetchone()[0], 0
        )
        await self.plugin.handle(LocalEvent("/自动记忆 开", "on"))
        self.plugin.context.llm_generate.side_effect = ConnectionError("test outage")
        failed = LocalEvent("我喜欢听爵士", "on-fact")
        await self.plugin.handle(failed)
        self.assertIn("ConnectionError", failed.sent[0])
        self.assertIn("已记住", failed.sent[0])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT value FROM facts").fetchone()[0],
            "爵士",
        )

    async def test_clear_stop_request_executes_before_model_and_does_not_change_identity(
        self,
    ):
        await self.plugin.handle(LocalEvent("记住，称呼我为管理员", "name-request"))
        event = LocalEvent("我想停止记忆。", "stop-intent")
        await self.plugin.handle(event)
        self.assertIn("已停止", event.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()
        self.assertEqual(
            self.plugin.store.db.execute("SELECT enabled FROM scopes").fetchone()[0], 0
        )
        await self.plugin.handle(LocalEvent("你会自动保存吗？", "after-stop"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertIn("记忆保存/注入=停用", request["system_prompt"])
        self.assertNotIn("管理员", request["system_prompt"])

    async def test_relationship_event_export_and_stop_injection(self):
        for index, text in enumerate(
            ["/记住 称呼=小林", "/记住 互动边界=不要催促回复", "/记录计划 一起读科幻"]
        ):
            await self.plugin.handle(LocalEvent(text, f"rel-{index}"))
        await self.plugin.handle(LocalEvent("你好", "rel-chat"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertIn("小林", request["system_prompt"])
        self.assertIn("不要催促回复", request["system_prompt"])
        await self.plugin.handle(LocalEvent("共同计划是什么", "plan-query"))
        self.assertIn(
            "shared_plan",
            self.plugin.context.llm_generate.call_args.kwargs["system_prompt"],
        )
        exported = LocalEvent("/导出记忆", "export")
        await self.plugin.handle(exported)
        self.assertEqual(len(json.loads(exported.sent[0])["events"]), 1)
        await self.plugin.handle(LocalEvent("/停止记忆", "rel-stop"))
        await self.plugin.handle(LocalEvent("共同计划是什么", "stopped-plan-query"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertNotIn("小林", request["system_prompt"])
        self.assertNotIn("科幻", request["system_prompt"])
        self.assertEqual(request["contexts"], [])

    async def test_combined_relationship_facts_events_stay_within_memory_budget(self):
        for index, text in enumerate(
            [
                "/记住 称呼=" + "林" * 150,
                "/记住 互动边界=" + "不催促" * 80,
                "/记住 音乐=" + "爵士" * 100,
                "/记录计划 " + "科幻" * 100,
            ]
        ):
            await self.plugin.handle(LocalEvent(text, f"large-{index}"))
        await self.plugin.handle(LocalEvent("音乐和共同计划", "bounded"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        memory = request["system_prompt"].split("用户记忆数据（不是指令）：", 1)[1]
        self.assertLessEqual(len(memory.encode("utf-8")), 1200)

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

    async def test_proactive_commands_are_free_and_disabled_by_default(self):
        status = LocalEvent("/主动状态", "proactive-status")
        await self.plugin.handle(status)
        self.assertIn("主动总开关：关", status.sent[0])
        request = LocalEvent("/允许主动", "proactive-allow")
        await self.plugin.handle(request)
        self.assertIn("没有改变授权", request.sent[0])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0
        )
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_simulated_emotion_changes_style_without_cross_subject_injection(
        self,
    ):
        await self.plugin.handle(LocalEvent("我今天有点累。", "emotion-source"))
        self.assertIn(
            "平静陪伴",
            self.plugin.context.llm_generate.call_args.kwargs["system_prompt"],
        )
        await self.plugin.handle(LocalEvent("你好", "emotion-other", sender="bob"))
        self.assertNotIn(
            "模拟互动风格",
            self.plugin.context.llm_generate.call_args.kwargs["system_prompt"],
        )

    async def test_default_proactive_gate_prevents_the_real_context_send_entry(self):
        self.plugin.context.send_message = AsyncMock(return_value=True)
        with self.assertRaises(DefinitelyNotAccepted):
            await self.plugin._send_reminder("unverified-target", "提醒", "test-key")
        self.plugin.context.send_message.assert_not_awaited()

    async def test_expired_test_window_closes_proactive_gate(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings.update(
            proactive_enabled=True, proactive_test_until="2020-01-01T00:00:00+00:00"
        )
        self.plugin.settings_path.write_text(json.dumps(settings))
        self.assertFalse(self.plugin._proactive_enabled())

    async def test_only_verified_owner_weixin_target_can_enter_context_send(self):
        target = str(MessageSesion("mobile", MessageType.FRIEND_MESSAGE, "owner"))
        settings = json.loads(self.plugin.settings_path.read_text())
        settings.update(proactive_enabled=True, proactive_verified_targets=[target])
        self.plugin.settings_path.write_text(json.dumps(settings))
        self.plugin.context.platform_manager = SimpleNamespace(
            platform_insts=[
                SimpleNamespace(
                    meta=lambda: SimpleNamespace(id="mobile", name="weixin_oc")
                )
            ]
        )
        self.plugin.context.send_message = AsyncMock(return_value=True)
        self.assertEqual(
            await self.plugin._send_reminder(target, "提醒", "test-key"),
            "accepted_by_platform",
        )
        self.plugin.context.send_message.return_value = False
        with self.assertRaises(DefinitelyNotAccepted):
            await self.plugin._send_reminder(target, "提醒", "other-key")
        calls = self.plugin.context.send_message.await_count
        settings["proactive_verified_targets"] = []
        self.plugin.settings_path.write_text(json.dumps(settings))
        with self.assertRaises(DefinitelyNotAccepted):
            await self.plugin._send_reminder(target, "提醒", "blocked-key")
        self.assertEqual(self.plugin.context.send_message.await_count, calls)

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
        settings.setdefault("bot_account_ids", {})["weixin_oc"] = (
            "trusted-weixin-account"
        )
        self.plugin.settings_path.write_text(json.dumps(settings))
        event = LocalEvent("", "voice-transcript", sender="owner", platform="weixin_oc")
        event.message_obj.message = [Record(file="not-read.silk")]
        event.message_obj.raw_message = {
            "item_list": [{"type": 3, "voice_item": {"text": "记住，我喜欢听爵士"}}]
        }
        await self.plugin.handle(event)
        self.assertEqual(event.sent, ["已保存这条明确记忆。"])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_voice_probe_only_owner_private_one_sample_no_model_or_memory(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"]["weixin_oc"] = ["owner"]
        settings["bot_account_ids"] = {"weixin_oc": "account-a"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        adapter = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="weixin_oc", name="weixin_oc")
        )
        self.plugin.context.platform_manager = SimpleNamespace(platform_insts=[adapter])
        for sender, group in [("other", ""), ("owner", "group")]:
            denied = LocalEvent(
                "/语音气泡测试",
                sender + group,
                sender=sender,
                platform="weixin_oc",
                group=group,
            )
            await self.plugin.handle(denied)
            self.assertEqual(denied.sent, [])
        web = LocalEvent("/语音气泡测试", "web-probe")
        await self.plugin.handle(web)
        self.assertIn("仅支持", web.sent[0])
        self.assertFalse((self.plugin.home / "voice-probe-state.local.json").exists())
        owner = LocalEvent("/语音气泡测试", "arm", sender="owner", platform="weixin_oc")
        await self.plugin.handle(owner)
        self.assertIn("原样回放一次", owner.sent[0])

        def voice_event(identifier):
            event = LocalEvent("", identifier, sender="owner", platform="weixin_oc")
            event.message_obj.message = [Record(file="not-opened.wav")]
            event.message_obj.raw_message = {
                "item_list": [{"type": 3, "voice_item": {"text": "记住，音乐是摇滚"}}]
            }
            return event

        with patch(
            "plugins.astrbot_plugin_companion_core.main.VoiceProbe.replay",
            new_callable=AsyncMock,
            return_value={"api_result": "accepted_by_platform"},
        ) as replay:
            sample = voice_event("sample")
            await self.plugin.handle(sample)
            await self.plugin.handle(sample)
            replay.assert_awaited_once()
            self.assertIn("请以手机", sample.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM facts").fetchone()[0], 0
        )
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM turns").fetchone()[0], 0
        )

    async def test_voice_probe_stop_prevents_arming(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"]["weixin_oc"] = ["owner"]
        settings["bot_account_ids"] = {"weixin_oc": "account-a"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        (self.plugin.home / "STOP").touch()
        owner = LocalEvent(
            "/语音气泡测试", "stopped-arm", sender="owner", platform="weixin_oc"
        )
        await self.plugin.handle(owner)
        self.assertIn("运行开关", owner.sent[0])
        self.assertFalse((self.plugin.home / "voice-probe-state.local.json").exists())

    async def test_cloud_speech_explicit_command_once_no_fact_or_model(self):
        event = LocalEvent("/朗读 记住，我喜欢摇滚", "speech-command")
        event.send = AsyncMock()
        audio = self.plugin.home / "owned-test.mp3"
        audio.write_bytes(b"fake audio")
        with patch(
            "plugins.astrbot_plugin_companion_core.main.generate_speech",
            new_callable=AsyncMock,
            return_value=(audio, {}),
        ) as synth:
            await self.plugin.handle(event)
            await self.plugin.handle(event)
            synth.assert_awaited_once()
        event.send.assert_awaited_once()
        chain = event.send.call_args.args[0].chain
        self.assertTrue(any(isinstance(part, File) for part in chain))
        self.assertIn("不是原生语音气泡", event.send.call_args.args[0].get_plain_text())
        self.plugin.context.llm_generate.assert_not_awaited()
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM facts").fetchone()[0], 0
        )

    async def test_cloud_speech_stop_and_revoke_during_generation(self):
        event = LocalEvent("/朗读 测试", "speech-stop")
        (self.plugin.home / "STOP").touch()
        with patch(
            "plugins.astrbot_plugin_companion_core.main.generate_speech",
            new_callable=AsyncMock,
        ) as synth:
            await self.plugin.handle(event)
            synth.assert_not_awaited()
        self.assertIn("不生成", event.sent[0])
        (self.plugin.home / "STOP").unlink()

        async def revoked(*_args):
            (self.plugin.home / "STOP").touch()
            return self.plugin.home / "not-sent.mp3", {}

        with patch(
            "plugins.astrbot_plugin_companion_core.main.generate_speech",
            side_effect=revoked,
        ):
            revoked_event = LocalEvent("/朗读 测试", "speech-late-stop")
            await self.plugin.handle(revoked_event)
        self.assertIn("本次不发送", revoked_event.sent[0])

    async def test_failed_native_probe_is_not_repeated_and_speech_has_text_fallback(
        self,
    ):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["native_voice_probe_disabled_after_failure"] = True
        self.plugin.settings_path.write_text(json.dumps(settings))
        event = LocalEvent("/语音气泡测试", "voice-known-failure")
        await self.plugin.handle(event)
        self.assertIn("暂不重复", event.sent[0])
        self.assertFalse((self.plugin.home / "voice-probe-state.local.json").exists())
        with patch(
            "plugins.astrbot_plugin_companion_core.main.generate_speech",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            failed = LocalEvent("/朗读 确认回退文字", "speech-timeout")
            await self.plugin.handle(failed)
        self.assertIn("先返回文字", failed.sent[0])
        self.assertIn("确认回退文字", failed.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_sample_index_cannot_read_arbitrary_file(self):
        (self.plugin.home / "tts-samples.local.json").write_text(
            json.dumps({"V01": "../settings.local.json"})
        )
        event = LocalEvent("/语音样本 1", "invalid-sample-path")
        await self.plugin.handle(event)
        self.assertIn("尚未生成成功", event.sent[0])
        self.assertEqual(len(event.sent), 1)
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_sample_number_with_or_without_space_uses_same_owned_file(self):
        folder = self.plugin.home / "tts-cache"
        folder.mkdir()
        audio = folder / ("tts-" + "a" * 32 + ".mp3")
        audio.write_bytes(b"fake transport test")
        (self.plugin.home / "tts-samples.local.json").write_text(
            json.dumps({"V01": audio.name})
        )
        for index, text in enumerate(("/语音样本1", "/语音样本 1", "/语音样本   01")):
            event = LocalEvent(text, f"sample-forms-{index}")
            event.send = AsyncMock()
            await self.plugin.handle(event)
            sent = event.send.call_args.args[0].chain
            self.assertEqual(
                [p.file for p in sent if isinstance(p, File)], [str(audio)]
            )
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_native_sample_only_verified_qq_record_never_file(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"]["qq_official"] = ["owner"]
        settings["bot_account_ids"] = {"qq_official": "trusted-app"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        folder = self.plugin.home / "tts-cache"
        folder.mkdir()
        audio = folder / ("tts-" + "b" * 32 + ".mp3")
        audio.write_bytes(b"test transport only")
        (self.plugin.home / "tts-samples.local.json").write_text(
            json.dumps({"V01": audio.name})
        )
        web = LocalEvent("/语音气泡样本1", "web-native-refused")
        await self.plugin.handle(web)
        self.assertIn("只在已核实的本人QQ", web.sent[0])
        event = LocalEvent(
            "/语音气泡样本1", "qq-native", sender="owner", platform="qq_official"
        )
        event.send = AsyncMock()
        await self.plugin.handle(event)
        await self.plugin.handle(event)
        event.send.assert_awaited_once()
        chain = event.send.call_args.args[0].chain
        self.assertEqual(len(chain), 1)
        self.assertIsInstance(chain[0], Record)
        self.assertNotIsInstance(chain[0], File)
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_qq_unknown_selfid_requires_trusted_appid_and_isolates_accounts(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"]["qq_official"] = ["owner"]
        self.plugin.settings_path.write_text(json.dumps(settings))
        missing = LocalEvent(
            "/记忆", "qq-unverified", sender="owner", platform="qq_official"
        )
        await self.plugin.handle(missing)
        self.assertIn("尚未在本机核实", missing.sent[0])
        settings["bot_account_ids"] = {"qq_official": "app-a"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        await self.plugin.handle(
            LocalEvent(
                "/记住 音乐=爵士", "qq-a", sender="owner", platform="qq_official"
            )
        )
        settings["bot_account_ids"]["qq_official"] = "app-b"
        self.plugin.settings_path.write_text(json.dumps(settings))
        different = LocalEvent("/记忆", "qq-b", sender="owner", platform="qq_official")
        await self.plugin.handle(different)
        self.assertEqual(different.sent, ["当前没有记忆。"])

    async def test_qq_owner_challenge_accepts_only_code_and_actual_app_without_model(
        self,
    ):
        from datetime import timedelta, timezone
        import hashlib

        settings = json.loads(self.plugin.settings_path.read_text())
        settings["bot_account_ids"] = {"qq_official": "app"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        self.plugin.context.platform_manager = SimpleNamespace(
            platform_insts=[
                SimpleNamespace(
                    meta=lambda: SimpleNamespace(id="qq_official", name="qq_official"),
                    appid="app",
                )
            ]
        )
        code = "0123456789ABCDEF"
        (self.plugin.home / "qq-owner-claim.local.json").write_text(
            json.dumps(
                {
                    "platform_id": "qq_official",
                    "appid": "app",
                    "owner_test_authorized": True,
                    "used": False,
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=10)
                    ).isoformat(),
                }
            )
        )
        stranger = LocalEvent(
            "你好", "qq-stranger", sender="outsider", platform="qq_official"
        )
        await self.plugin.handle(stranger)
        self.assertEqual(stranger.sent, [])
        owner = LocalEvent(
            "/验证本人 " + code,
            "qq-proof",
            sender="actual-qq-owner",
            platform="qq_official",
        )
        await self.plugin.handle(owner)
        self.assertIn("已核实", owner.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()
        settings = json.loads(self.plugin.settings_path.read_text())
        self.assertEqual(
            settings["allowed_senders"]["qq_official"], ["actual-qq-owner"]
        )
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM facts").fetchone()[0], 0
        )

    async def test_actual_weixin_account_separates_bots_even_with_same_adapter_id(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"]["weixin_oc"] = ["owner"]
        settings["bot_account_ids"] = {"weixin_oc": "account-a"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        await self.plugin.handle(
            LocalEvent("/记住 音乐=爵士", "bot-a", sender="owner", platform="weixin_oc")
        )
        settings["bot_account_ids"]["weixin_oc"] = "account-b"
        self.plugin.settings_path.write_text(json.dumps(settings))
        other = LocalEvent("/记忆", "bot-b", sender="owner", platform="weixin_oc")
        await self.plugin.handle(other)
        self.assertEqual(other.sent, ["当前没有记忆。"])
        settings["bot_account_ids"]["weixin_oc"] = "account-a"
        self.plugin.settings_path.write_text(json.dumps(settings))
        first = LocalEvent(
            "/记忆", "bot-a-return", sender="owner", platform="weixin_oc"
        )
        await self.plugin.handle(first)
        self.assertIn("爵士", first.sent[0])

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

    async def test_binding_two_mobile_commands_model_scope_and_unlink(self):
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"].update(
            {"weixin_oc": ["wx-owner"], "qq_official": ["qq-owner"]}
        )
        settings["bot_account_ids"] = {
            "weixin_oc": "wx-account",
            "qq_official": "qq-app",
        }
        self.plugin.settings_path.write_text(json.dumps(settings))
        self.plugin.context.platform_manager = SimpleNamespace(
            platform_insts=[
                SimpleNamespace(
                    meta=lambda: SimpleNamespace(id="weixin_oc", name="weixin_oc")
                ),
                SimpleNamespace(
                    meta=lambda: SimpleNamespace(id="qq_official", name="qq_official"),
                    appid="qq-app",
                ),
            ]
        )

        async def send(text, index, qq=False):
            platform, owner = (
                ("qq_official", "qq-owner") if qq else ("weixin_oc", "wx-owner")
            )
            event = LocalEvent(
                text, "binding-" + str(index), sender=owner, platform=platform
            )
            await self.plugin.handle(event)
            return event

        await send("/记住 音乐=爵士", 1)
        await send("/记住 生日=六月一日", 2)
        issued = await send("/绑定发起 QQ 音乐", 3)
        code = re.search(r"绑定码 ([0-9A-F]{24})", issued.sent[0])[1]
        premature = await send("/绑定接受 " + code, 4, qq=True)
        self.assertIn("请先用 /绑定查看", premature.sent[0])
        await send("/绑定查看 " + code, 5, qq=True)
        accepted = await send("/绑定接受 " + code, 6, qq=True)
        self.assertIn("已确认", accepted.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()
        await send("我听什么音乐，生日呢", 7, qq=True)
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertIn("爵士", request["system_prompt"])
        self.assertIn('"shared_from": "微信"', request["system_prompt"])
        self.assertNotIn("六月一日", request["system_prompt"])
        status = await send("/绑定状态", 8, qq=True)
        identifier = status.sent[0].split()[0]
        await send("/解绑 " + identifier, 9)
        await send("我的音乐是什么", 10, qq=True)
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertNotIn("爵士", request["system_prompt"])
        self.assertEqual(request["contexts"], [])

    async def test_binding_refuses_web_groups_and_mismatched_actual_qq_app(self):
        web = LocalEvent("/绑定发起 QQ 音乐", "web-binding")
        await self.plugin.handle(web)
        self.assertIn("仅限", web.sent[0])
        self.assertEqual(
            self.plugin.store.db.execute(
                "SELECT count(*) FROM ledger.binding_codes"
            ).fetchone()[0],
            0,
        )
        settings = json.loads(self.plugin.settings_path.read_text())
        settings["allowed_senders"].update(
            {"weixin_oc": ["owner"], "qq_official": ["owner"]}
        )
        settings["bot_account_ids"] = {"weixin_oc": "wx", "qq_official": "expected"}
        self.plugin.settings_path.write_text(json.dumps(settings))
        self.plugin.context.platform_manager = SimpleNamespace(
            platform_insts=[
                SimpleNamespace(
                    meta=lambda: SimpleNamespace(id="weixin_oc", name="weixin_oc")
                ),
                SimpleNamespace(
                    meta=lambda: SimpleNamespace(id="qq_official", name="qq_official"),
                    appid="wrong",
                ),
            ]
        )
        event = LocalEvent(
            "/绑定发起 QQ 音乐",
            "wrong-app-binding",
            sender="owner",
            platform="weixin_oc",
        )
        await self.plugin.handle(event)
        self.assertIn("目标渠道必须", event.sent[0])
        group = LocalEvent(
            "/绑定帮助",
            "group-binding",
            sender="owner",
            platform="weixin_oc",
            group="g",
        )
        await self.plugin.handle(group)
        self.assertEqual(group.sent, [])
        self.plugin.context.llm_generate.assert_not_awaited()

    async def test_bilibili_source_record_recall_budget_delete_and_no_automatic_action(
        self,
    ):
        from datetime import timezone
        from plugins.astrbot_plugin_companion_core.bilibili import VideoMetadata

        metadata = VideoMetadata(
            "BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "公开样本标题",
            "公开作者",
            "这是简介内容。" * 100,
            datetime.now(timezone.utc).isoformat(),
        )
        # A bounded API fixture, not evidence of reading the real site.
        from dataclasses import replace

        metadata = replace(
            metadata,
            description=metadata.description.encode()[:900].decode(errors="ignore"),
        )
        reader = SimpleNamespace(read=AsyncMock(return_value=metadata))
        with patch(
            "plugins.astrbot_plugin_companion_core.main.BilibiliReader",
            return_value=reader,
        ):
            event = LocalEvent("/B站读取 BV1xx411c7mD", "bili-read")
            await self.plugin.handle(event)
            self.assertIn("未观看视频", event.sent[0])
            self.assertEqual(reader.read.await_count, 1)
            self.plugin.context.llm_generate.assert_not_awaited()
            duplicate = LocalEvent("/B站读取 BV1xx411c7mD", "bili-again")
            await self.plugin.handle(duplicate)
            self.assertIn("今天已进行过一次", duplicate.sent[0])
            self.assertEqual(reader.read.await_count, 1)
        await self.plugin.handle(LocalEvent("你最近读过什么B站资料", "bili-recall"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertIn("公开样本标题", request["system_prompt"])
        self.assertIn("external_metadata", request["system_prompt"])
        self.assertIn("未读取视频正文", request["system_prompt"])
        await self.plugin.handle(LocalEvent("/忘记 B站活动", "bili-delete"))
        await self.plugin.handle(LocalEvent("你读过什么B站资料", "bili-forgotten"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertNotIn("公开样本标题", request["system_prompt"])
        self.assertEqual(request["contexts"], [])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0
        )

    async def test_bilibili_failure_stop_and_disabled_memory_do_not_invent_activity(
        self,
    ):
        from plugins.astrbot_plugin_companion_core.bilibili import ReadFailed

        reader = SimpleNamespace(read=AsyncMock(side_effect=ReadFailed("http_412")))
        with patch(
            "plugins.astrbot_plugin_companion_core.main.BilibiliReader",
            return_value=reader,
        ):
            event = LocalEvent("/B站读取 BV1xx411c7mD", "bili-412")
            await self.plugin.handle(event)
            self.assertIn("未取得", event.sent[0])
            self.assertIn("http_412", event.sent[0])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM events").fetchone()[0], 0
        )
        (self.plugin.home / "STOP").touch()
        with patch(
            "plugins.astrbot_plugin_companion_core.main.BilibiliReader"
        ) as factory:
            event = LocalEvent("/B站读取 BV1xx411c7mD", "bili-stop")
            await self.plugin.handle(event)
            self.assertIn("停止", event.sent[0])
            factory.assert_not_called()
        (self.plugin.home / "STOP").unlink()
        await self.plugin.handle(LocalEvent("/停止记忆", "bili-disable-memory"))
        with patch(
            "plugins.astrbot_plugin_companion_core.main.BilibiliReader"
        ) as factory:
            event = LocalEvent("/B站读取 BV1xx411c7mD", "bili-disabled")
            await self.plugin.handle(event)
            self.assertIn("Memory disabled", event.sent[0])
            factory.assert_not_called()
        self.plugin.context.llm_generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
