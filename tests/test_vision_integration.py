"""Actual Companion Core event path with isolated DB and synthetic image/model."""

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_astrbot_integration as fixture
import botpy
from astrbot.api.message_components import Image, Video
from astrbot.core.provider.entities import TokenUsage
from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter,
)

from plugins.astrbot_plugin_companion_core.vision import (
    HISTORY_MARKER,
    trusted_qq_picture,
)
from test_vision import png

MODEL = "plugins.astrbot_plugin_companion_core.main.generate_vision_once"


class VisionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.IntegrationTests.asyncSetUp
    asyncTearDown = fixture.IntegrationTests.asyncTearDown

    def settings(self, **updates):
        value = json.loads(self.plugin.settings_path.read_text()) | updates
        self.plugin.settings_path.write_text(json.dumps(value))
        return value

    def event(self, text="识别图中文字", message_id="image", **kwargs):
        event = fixture.LocalEvent(text, message_id, **kwargs)
        event.message_obj.message = [Image.fromBase64(base64.b64encode(png()).decode())]
        return event

    @staticmethod
    def result(text="图中写着：我最喜欢的颜色是紫色。", changed=False):
        return SimpleNamespace(
            completion_text=text,
            usage=TokenUsage(20, 0, 10),
            model="deepseek-flash",
            finish_reason="stop",
            configuration_changed=changed,
        )

    async def test_picture_once_text_only_history_and_no_automatic_fact(self):
        self.settings(vision_enabled=True)
        await self.plugin.handle(fixture.LocalEvent("/自动记忆 开", "auto"))
        event = self.event()
        with patch(
            MODEL, new_callable=AsyncMock, return_value=self.result()
        ) as generate:
            await self.plugin.handle(event)
            await self.plugin.handle(event)
        generate.assert_awaited_once()
        self.assertEqual(len(event.sent), 1)
        self.assertIn("图片识别结果", event.sent[0])
        self.plugin.context.llm_generate.assert_not_awaited()
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM facts").fetchone()[0], 0
        )
        turn = self.plugin.store.db.execute("SELECT * FROM turns").fetchone()
        self.assertIn(HISTORY_MARKER, turn["user_text"])
        self.assertNotIn("base64", str(tuple(turn)))
        await self.plugin.handle(fixture.LocalEvent("图上的内容可靠吗？", "follow"))
        request = self.plugin.context.llm_generate.call_args.kwargs
        self.assertIn("原图", request["system_prompt"])

    async def test_off_multi_image_mixed_media_and_untrusted_chat_make_no_model_call(
        self,
    ):
        with patch(MODEL, new_callable=AsyncMock) as generate:
            off = self.event(message_id="off")
            await self.plugin.handle(off)
            self.assertIn("尚未启用", off.sent[0])
            self.settings(vision_enabled=True)
            multi = self.event(message_id="multi")
            multi.message_obj.message *= 2
            await self.plugin.handle(multi)
            self.assertIn("一张", multi.sent[0])
            mixed = self.event(message_id="mixed")
            mixed.message_obj.message.append(Video(file="https://example.com/video"))
            await self.plugin.handle(mixed)
            self.assertIn("尚未启用", mixed.sent[0])
            stranger = self.event(
                message_id="stranger", sender="stranger", platform="mobile"
            )
            await self.plugin.handle(stranger)
            self.assertEqual(stranger.sent, [])
            group = self.event(message_id="group", group="group")
            await self.plugin.handle(group)
            self.assertEqual(group.sent, [])
            generate.assert_not_awaited()

    async def test_stop_before_request_and_revocation_after_model(self):
        self.settings(vision_enabled=True)
        (self.plugin.home / "STOP").touch()
        with patch(MODEL, new_callable=AsyncMock) as generate:
            event = self.event(message_id="stopped")
            await self.plugin.handle(event)
            generate.assert_not_awaited()
        (self.plugin.home / "STOP").unlink()

        async def revoke(**kwargs):
            self.assertTrue(kwargs["allowed"]())
            self.settings(vision_enabled=False)
            self.assertFalse(kwargs["allowed"]())
            return self.result()

        with patch(MODEL, side_effect=revoke):
            event = self.event(message_id="revoked")
            await self.plugin.handle(event)
        self.assertEqual(event.sent, [])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM turns").fetchone()[0], 0
        )

    async def test_deleted_scope_revision_and_provider_change_discard_late_reply(self):
        self.settings(vision_enabled=True)

        async def delete(**kwargs):
            scope = self.plugin.store.db.execute("SELECT scope FROM scopes").fetchone()[
                0
            ]
            self.plugin.store.forget(scope, "全部")
            self.assertFalse(kwargs["allowed"]())
            return self.result()

        with patch(MODEL, side_effect=delete):
            event = self.event(message_id="delete-late")
            await self.plugin.handle(event)
        self.assertEqual(event.sent, [])
        with patch(
            MODEL, new_callable=AsyncMock, return_value=self.result(changed=True)
        ):
            event = self.event(message_id="route-changed")
            await self.plugin.handle(event)
        self.assertEqual(event.sent, [])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM turns").fetchone()[0], 0
        )

    async def test_unknown_delivery_and_provider_failure_never_resubmit(self):
        self.settings(vision_enabled=True)
        event = self.event(message_id="send-unknown")
        event.send = AsyncMock(side_effect=TimeoutError)
        with patch(
            MODEL, new_callable=AsyncMock, return_value=self.result()
        ) as generate:
            await self.plugin.handle(event)
            await self.plugin.handle(event)
            generate.assert_awaited_once()
        event.send.assert_awaited_once()
        with patch(MODEL, new_callable=AsyncMock, side_effect=TimeoutError) as generate:
            event = self.event(message_id="model-timeout")
            await self.plugin.handle(event)
            await self.plugin.handle(event)
            generate.assert_awaited_once()
        self.assertEqual(len(event.sent), 1)
        self.assertIn("没有自动重试", event.sent[0])

    async def test_memory_disabled_has_no_history_or_facts(self):
        self.settings(vision_enabled=True)
        await self.plugin.handle(fixture.LocalEvent("/记住 颜色=青玉绿", "remember"))
        await self.plugin.handle(fixture.LocalEvent("/停止记忆", "memory-off"))
        with patch(
            MODEL, new_callable=AsyncMock, return_value=self.result()
        ) as generate:
            event = self.event("颜色是什么？", "disabled-memory")
            await self.plugin.handle(event)
        request = generate.call_args.kwargs
        self.assertEqual(request["history"], [])
        self.assertNotIn("青玉绿", request["system_prompt"])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM turns").fetchone()[0], 0
        )

    async def test_busy_decoder_keeps_worker_slot(self):
        blocked = asyncio.create_task(asyncio.sleep(60))
        self.plugin._image_prepare_task = blocked
        try:
            with self.assertRaisesRegex(ValueError, "image_prepare_busy"):
                await self.plugin._prepare_image(
                    self.event().message_obj.message[0], "webchat"
                )
        finally:
            blocked.cancel()
            await asyncio.gather(blocked, return_exceptions=True)

    async def test_real_preprocessor_materialized_weixin_path_reaches_model(self):
        self.settings(
            vision_enabled=True,
            allowed_senders={"weixin_oc": ["owner"]},
            bot_account_ids={"weixin_oc": "trusted-account"},
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            image = root / ("weixin_oc_img_" + "a" * 32 + "_image.png")
            image.write_bytes(png())
            component = Image.fromFileSystem(str(image))
            event = self.event(
                message_id="preprocessed", sender="owner", platform="weixin_oc"
            )
            event.message_obj.message = [component]
            stage = PreProcessStage.__new__(PreProcessStage)
            await stage._normalize_image_component(event, component)
            self.assertEqual(component.file, str(image))
            self.assertEqual(component.url, str(image))
            with (
                patch(
                    "plugins.astrbot_plugin_companion_core.main.get_astrbot_temp_path",
                    return_value=str(root),
                ),
                patch(
                    MODEL, new_callable=AsyncMock, return_value=self.result()
                ) as generate,
            ):
                await self.plugin.handle(event)
            generate.assert_awaited_once()
            self.assertEqual(len(event.sent), 1)
            self.assertIn("图片识别结果", event.sent[0])

    def qq_event(self, message_id="qq-image"):
        raw = botpy.message.C2CMessage.__new__(botpy.message.C2CMessage)
        raw.id = message_id
        raw.author = SimpleNamespace(user_openid="owner")
        raw.attachments = [
            {
                "content_type": "image/jpeg",
                "url": "https://multimedia.nt.qq.com.cn/download?synthetic=true",
            }
        ]
        event = self.event(
            message_id=message_id, sender="owner", platform="qq_official"
        )
        event.message_obj.raw_message = raw
        return event

    async def test_original_qq_event_adapter_and_preprocessor_reach_model(self):
        self.settings(
            vision_enabled=True,
            allowed_senders={"qq_official": ["owner"]},
            bot_account_ids={"qq_official": "trusted-app"},
        )
        event = self.qq_event()
        components = []
        await QQOfficialPlatformAdapter._append_attachments(
            components, event.message_obj.raw_message.attachments
        )
        event.message_obj.message = components
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()

            async def download(url, destination):
                self.assertEqual(
                    url, event.message_obj.raw_message.attachments[0]["url"]
                )
                Path(destination).write_bytes(png())

            with (
                patch(
                    "astrbot.core.utils.media_utils.get_astrbot_temp_path",
                    return_value=str(root),
                ),
                patch(
                    "astrbot.core.utils.media_utils.download_file", side_effect=download
                ) as network,
            ):
                stage = PreProcessStage.__new__(PreProcessStage)
                await stage._normalize_image_component(event, components[0])
            network.assert_awaited_once()
            self.assertTrue(Path(components[0].path).name.startswith("media_image_"))
            with (
                patch(
                    "plugins.astrbot_plugin_companion_core.main.get_astrbot_temp_path",
                    return_value=str(root),
                ),
                patch(
                    MODEL, new_callable=AsyncMock, return_value=self.result()
                ) as generate,
            ):
                await self.plugin.handle(event)
            generate.assert_awaited_once()
            self.assertIn("图片识别结果", event.sent[0])

    async def test_qq_original_sender_message_mime_and_private_scope_required(self):
        self.assertTrue(trusted_qq_picture(self.qq_event()))
        for mutation in ("sender", "id", "mime", "group", "multiple", "raw"):
            event = self.qq_event()
            raw = event.message_obj.raw_message
            if mutation == "sender":
                raw.author.user_openid = "stranger"
            elif mutation == "id":
                raw.id = "other-message"
            elif mutation == "mime":
                raw.attachments[0]["content_type"] = "application/pdf"
            elif mutation == "group":
                event.get_group_id = lambda: "group"
            elif mutation == "multiple":
                raw.attachments *= 2
            else:
                event.message_obj.raw_message = SimpleNamespace(
                    id=raw.id, author=raw.author, attachments=raw.attachments
                )
            with self.subTest(mutation=mutation):
                self.assertFalse(trusted_qq_picture(event))

    async def test_bounded_image_arriving_after_forget_cannot_capture_a_new_ticket(
        self,
    ):
        self.settings(vision_enabled=True, qq_bounded_images=True)
        await self.plugin.handle(
            fixture.LocalEvent("/记住 颜色=青玉绿", "before-download")
        )
        scope = self.plugin.store.db.execute("SELECT scope FROM scopes").fetchone()[0]
        revisions = {scope: self.plugin.store.begin(scope, "network-start").revision}
        event = self.event(message_id="old-download-arrives")
        event.set_extra("companion_qq_image_source", "bounded_owned_download")
        event.set_extra("companion_qq_image_revisions", revisions)
        await self.plugin.handle(
            fixture.LocalEvent("/忘记 颜色", "delete-overtakes-download")
        )
        with patch(MODEL, new_callable=AsyncMock) as generate:
            await self.plugin.handle(event)
            generate.assert_not_awaited()
        self.assertEqual(event.sent, [])
        self.assertEqual(
            self.plugin.store.db.execute("SELECT count(*) FROM turns").fetchone()[0], 0
        )

    async def test_current_bounded_ticket_is_allowed_until_its_host_switch_is_closed(
        self,
    ):
        self.settings(vision_enabled=True, qq_bounded_images=True)
        await self.plugin.handle(fixture.LocalEvent("/记忆", "scope-before-bounded"))
        scope = self.plugin.store.db.execute("SELECT scope FROM scopes").fetchone()[0]
        revisions = {scope: self.plugin.store.begin(scope, "download").revision}
        with patch(
            MODEL, new_callable=AsyncMock, return_value=self.result()
        ) as generate:
            first = self.event(message_id="fresh-bounded")
            first.set_extra("companion_qq_image_source", "bounded_owned_download")
            first.set_extra("companion_qq_image_revisions", revisions)
            await self.plugin.handle(first)
            self.assertIn("图片识别结果", first.sent[0])
            self.settings(qq_bounded_images=False)
            later = self.event(message_id="closed-bounded")
            later.set_extra("companion_qq_image_source", "bounded_owned_download")
            later.set_extra("companion_qq_image_revisions", revisions)
            await self.plugin.handle(later)
            self.assertEqual(later.sent, [])
            generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
