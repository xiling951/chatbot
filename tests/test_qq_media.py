"""Pinned QQ preprocessing with fake HTTP; no account or IM traffic."""

import asyncio
import json
import os
import ssl
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import aiohttp
import botpy
from astrbot.api.message_components import Image
from astrbot.core.platform.astr_message_event import event_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qq_media import (
    MAX_BYTES,
    MediaRefused,
    QQImages,
    image_url,
    install,
    uninstall,
    prune_cache,
)
from test_astrbot_integration import LocalEvent
from test_vision import png
from plugins.astrbot_plugin_companion_core.vision import prepare_component

URL = "https://multimedia.nt.qq.com.cn/download?fileid=synthetic&rkey=not-a-key"


class Response:
    def __init__(self, chunks=None, status=200, headers=None, size=None, on_chunk=None):
        self.status = status
        self.headers = headers or {"Content-Type": "image/png"}
        self.content_length = size

        async def iterate(_size):
            for chunk in chunks if chunks is not None else [png()]:
                if on_chunk:
                    on_chunk()
                yield chunk

        self.content = SimpleNamespace(iter_chunked=iterate)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class QQMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name).resolve()
        self.home = self.data / "plugin_data" / "companion_core"
        self.home.mkdir(parents=True)
        self.cache = self.data / "temp"
        self.cache.mkdir()
        self.settings = {
            "model_enabled": True,
            "vision_enabled": True,
            "qq_bounded_images": True,
            "qq_loopback_proxy": "http://127.0.0.1:7890",
            "allowed_senders": {"qq_official": ["owner"]},
            "bot_account_ids": {"qq_official": "verified-app"},
        }
        self.save()
        with closing(sqlite3.connect(self.home / "deletions.sqlite")) as ledger:
            ledger.execute(
                "CREATE TABLE revisions(scope TEXT PRIMARY KEY,revision INTEGER)"
            )
            ledger.execute("INSERT INTO revisions VALUES('synthetic-scope',0)")
            ledger.commit()
        (self.data / "cmd_config.json").write_text(
            json.dumps(
                {
                    "platform": [
                        {
                            "id": "qq_official",
                            "type": "qq_official",
                            "enable": True,
                            "appid": "verified-app",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.original = AsyncMock()
        self.normalizer = QQImages(self.home, self.data, self.cache, self.original)

    def save(self):
        (self.home / "settings.local.json").write_text(
            json.dumps(self.settings), encoding="utf-8"
        )

    def event(self, message_id="qq-owned"):
        raw = botpy.message.C2CMessage.__new__(botpy.message.C2CMessage)
        raw.id = message_id
        raw.author = SimpleNamespace(user_openid="owner")
        raw.attachments = [{"content_type": "image/png", "url": URL}]
        event = LocalEvent("", message_id, sender="owner", platform="qq_official")
        event.message_obj.raw_message = raw
        event.message_obj.message = [Image.fromURL(URL)]
        return event, event.message_obj.message[0]

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def invoke(self, response, event=None):
        event, component = event or self.event()
        session = Session(response)
        with patch("aiohttp.ClientSession", return_value=session) as factory:
            await self.normalizer.normalize(object(), event, component)
        return event, component, session, factory

    async def test_single_verified_bounded_download_and_real_event_cleanup(self):
        event, component = self.event()
        unrelated = self.cache / "unrelated-original.png"
        unrelated.write_bytes(png())
        with event_context(event):
            _, _, session, factory = await self.invoke(Response(), (event, component))
            args, kwargs = session.calls[0]
            self.assertEqual(args, (URL,))
            self.assertEqual(kwargs["proxy"], "http://127.0.0.1:7890")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(kwargs["ssl"].check_hostname)
            self.assertFalse(factory.call_args.kwargs["trust_env"])
            self.assertFalse(factory.call_args.kwargs["auto_decompress"])
            self.assertIsInstance(
                factory.call_args.kwargs["cookie_jar"], aiohttp.DummyCookieJar
            )
            self.assertLessEqual(factory.call_args.kwargs["timeout"].total, 15)
            self.assertEqual(len(session.calls), 1)
            created = Path(component.path)
            self.assertTrue(created.is_file())
            self.assertTrue(created.name.startswith("companion_qq_img_"))
            prepared = prepare_component(
                file_ref=component.file,
                url_ref=component.url,
                local_path=component.path,
                platform_name="qq_official",
                temp_root=self.cache,
                qq_inbound=True,
            )
            self.assertEqual(prepared.width, 80)
        self.assertFalse(created.exists())
        self.assertTrue(unrelated.exists())
        self.original.assert_not_awaited()

    async def test_refusals_do_not_retry_or_enter_the_legacy_downloader(self):
        for response in (
            Response(status=302),
            Response(status=500),
            Response(size=MAX_BYTES + 1),
            Response(chunks=[b"x" * (MAX_BYTES + 1)]),
            Response(headers={"Content-Type": "image/png", "Content-Encoding": "gzip"}),
            Response(headers={"Content-Type": "text/html"}),
            Response(chunks=[]),
            RuntimeError("signed-url-secret must never be copied"),
        ):
            with self.subTest(response=type(response).__name__):
                event, component, session, _ = await self.invoke(response)
                self.assertEqual(component.file, URL)
                self.assertEqual(len(session.calls), 1)
                self.assertTrue(event.get_extra("companion_qq_image_error"))
                self.assertNotIn(
                    "signed-url-secret",
                    str(event.get_extra("companion_qq_image_error")),
                )
                self.assertEqual(list(self.cache.iterdir()), [])
        self.original.assert_not_awaited()

    async def test_owner_stop_account_and_switch_are_checked_before_http(self):
        for mutation in ("owner", "stop", "account", "off", "group", "id"):
            event, component = self.event()
            saved = json.loads(json.dumps(self.settings))
            if mutation == "owner":
                self.settings["allowed_senders"]["qq_official"] = ["other"]
            elif mutation == "stop":
                (self.home / "STOP").touch()
            elif mutation == "account":
                self.settings["bot_account_ids"]["qq_official"] = "other-app"
            elif mutation == "off":
                self.settings["vision_enabled"] = False
            elif mutation == "group":
                event.get_group_id = lambda: "group"
            else:
                event.message_obj.raw_message.id = "wrong-message"
            self.save()
            with patch.object(
                self.normalizer, "download", new_callable=AsyncMock
            ) as download:
                await self.normalizer.normalize(object(), event, component)
                download.assert_not_awaited()
            self.settings = saved
            self.save()
            (self.home / "STOP").unlink(missing_ok=True)
        self.original.assert_not_awaited()

    async def test_source_host_and_current_attachment_are_not_chosen_by_body(self):
        for url in (
            "http://multimedia.nt.qq.com.cn/download?a=1",
            "https://127.0.0.1/download",
            "https://multimedia.nt.qq.com.cn.evil.example/download",
            "https://user@multimedia.nt.qq.com.cn/download",
            "https://multimedia.nt.qq.com.cn:444/download",
            "https://multimedia.nt.qq.com.cn/other",
        ):
            with self.subTest(url=url), self.assertRaises(MediaRefused):
                image_url(url)
        event, component = self.event()
        component.file = "https://multimedia.nt.qq.com.cn/download?different"
        _, _, session, _ = await self.invoke(Response(), (event, component))
        self.assertEqual(session.calls, [])
        self.assertEqual(list(self.cache.iterdir()), [])

    async def test_revoked_result_and_cancellation_leave_no_cache(self):
        def revoke():
            self.settings["vision_enabled"] = False
            self.save()

        event, _, _, _ = await self.invoke(Response(on_chunk=revoke))
        self.assertEqual(
            event.get_extra("companion_qq_image_error"),
            "qq_image_authorization_changed",
        )
        self.assertEqual(list(self.cache.iterdir()), [])
        self.settings["vision_enabled"] = True
        self.save()
        event, component = self.event()
        with patch.object(
            self.normalizer, "download", side_effect=asyncio.CancelledError
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.normalizer.normalize(object(), event, component)
        self.assertFalse(self.normalizer.busy)
        self.assertEqual(list(self.cache.iterdir()), [])

    async def test_busy_and_other_platforms_do_not_start_a_qq_request(self):
        event, component = self.event()
        self.normalizer.busy = True
        await self.normalizer.normalize(object(), event, component)
        self.assertEqual(event.get_extra("companion_qq_image_error"), "qq_image_busy")
        other = LocalEvent("", "web", platform="webchat")
        stage = object()
        await self.normalizer.normalize(stage, other, component)
        self.original.assert_awaited_once_with(stage, other, component)

    async def test_hook_installation_is_idempotent_and_reversible(self):
        from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage

        original = PreProcessStage._normalize_image_component
        installation = install(self.home, self.data, self.cache)
        try:
            self.assertIs(install(self.home, self.data, self.cache), installation)
            self.assertIs(
                PreProcessStage._normalize_image_component, installation.replacement
            )
            event, component = self.event()
            session = Session(Response())
            with (
                event_context(event),
                patch("aiohttp.ClientSession", return_value=session),
            ):
                stage = PreProcessStage.__new__(PreProcessStage)
                await stage._normalize_image_component(event, component)
                created = Path(component.path)
                self.assertTrue(created.is_file())
                self.assertEqual(len(session.calls), 1)
            self.assertFalse(created.exists())
        finally:
            uninstall(installation)
        self.assertIs(PreProcessStage._normalize_image_component, original)

    async def test_existing_file_is_never_removed_when_exclusive_creation_fails(self):
        existing = self.cache / ("companion_qq_img_" + "a" * 32 + "_image.jpg")
        existing.write_bytes(b"existing unrelated fixture")
        with patch("qq_media.uuid.uuid4", return_value=SimpleNamespace(hex="a" * 32)):
            event, component, _, _ = await self.invoke(Response())
        self.assertEqual(existing.read_bytes(), b"existing unrelated fixture")
        self.assertEqual(component.file, URL)
        self.assertEqual(
            event.get_extra("companion_qq_image_error"), "qq_image_download_failed"
        )

    async def test_two_concurrent_events_only_one_download_and_timeout_has_no_retry(
        self,
    ):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(*_args):
            entered.set()
            await release.wait()
            return png()

        first, part = self.event("first")
        second, second_part = self.event("second")
        with (
            event_context(first),
            patch.object(self.normalizer, "download", side_effect=blocked) as download,
        ):
            task = asyncio.create_task(self.normalizer.normalize(object(), first, part))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await self.normalizer.normalize(object(), second, second_part)
                self.assertEqual(
                    second.get_extra("companion_qq_image_error"), "qq_image_busy"
                )
                self.assertEqual(download.await_count, 1)
            finally:
                release.set()
                await task
        event, component = self.event("deadline")
        release.clear()
        with (
            patch("qq_media.TOTAL_SECONDS", 0.01),
            patch.object(self.normalizer, "download", side_effect=blocked) as download,
        ):
            await self.normalizer.normalize(object(), event, component)
        self.assertEqual(
            event.get_extra("companion_qq_image_error"), "qq_image_timeout"
        )
        download.assert_awaited_once()
        self.assertFalse(self.normalizer.busy)
        self.assertEqual(list(self.cache.iterdir()), [])

    async def test_deletion_during_download_discards_payload_before_file_creation(self):
        def delete_metadata():
            with closing(sqlite3.connect(self.home / "deletions.sqlite")) as ledger:
                ledger.execute("UPDATE revisions SET revision=revision+1")
                ledger.commit()

        event, _, _, _ = await self.invoke(Response(on_chunk=delete_metadata))
        self.assertEqual(
            event.get_extra("companion_qq_image_error"),
            "qq_image_authorization_changed",
        )
        self.assertEqual(list(self.cache.iterdir()), [])


class QQImageCacheTests(unittest.TestCase):
    # Controlled file mtime tests, not evidence of 24 hours of real observation.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2026, 10, 30, 17, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def owned(self, letter, hours):
        path = self.root / ("companion_qq_img_" + letter * 32 + "_image.jpg")
        path.write_bytes(b"synthetic image cache")
        moment = (self.now - timedelta(hours=hours)).timestamp()
        os.utime(path, (moment, moment))
        return path

    def test_only_expired_owned_regular_files_are_removed(self):
        old = self.owned("a", 25)
        fresh = self.owned("b", 1)
        boundary = self.owned("c", 24)
        directory = self.root / ("companion_qq_img_" + "d" * 32 + "_image.jpg")
        directory.mkdir()
        others = []
        for name in (
            "original.jpg",
            "media_image_20261030000000123_abcd.jpg",
            "weixin_oc_img_" + "e" * 32 + "_image.jpg",
            "companion_qq_img_invalid_image.jpg",
        ):
            path = self.root / name
            path.write_bytes(b"keep existing or other namespace")
            moment = (self.now - timedelta(days=3)).timestamp()
            os.utime(path, (moment, moment))
            others.append(path)
        self.assertEqual(prune_cache(self.root, self.now), 1)
        self.assertFalse(old.exists())
        self.assertTrue(
            all(path.exists() for path in (fresh, boundary, directory, *others))
        )
        missing = self.root / "missing"
        self.assertEqual(prune_cache(missing, self.now), 0)
        self.assertFalse(missing.exists())

    def test_file_updated_after_first_inspection_is_kept(self):
        path = self.owned("a", 25)
        original = Path.lstat
        calls = 0

        def changed(candidate, *args, **kwargs):
            nonlocal calls
            if candidate == path:
                calls += 1
                if calls == 2:
                    path.write_bytes(b"fresh replacement")
                    moment = self.now.timestamp()
                    os.utime(path, (moment, moment))
            return original(candidate, *args, **kwargs)

        with patch.object(Path, "lstat", changed):
            self.assertEqual(prune_cache(self.root, self.now), 0)
        self.assertEqual(path.read_bytes(), b"fresh replacement")

    def test_locked_file_is_retained_and_naive_time_cannot_delete(self):
        path = self.owned("a", 25)
        with self.assertRaises(ValueError):
            prune_cache(self.root, self.now.replace(tzinfo=None))
        with patch.object(Path, "unlink", side_effect=PermissionError):
            self.assertEqual(prune_cache(self.root, self.now), 0)
        self.assertTrue(path.exists())
        self.assertEqual(prune_cache(self.root, self.now), 1)

    def test_reparse_metadata_is_refused_without_following_a_target(self):
        path = self.owned("a", 25)
        original = Path.lstat
        before = path.lstat()
        info = SimpleNamespace(
            st_mode=before.st_mode, st_mtime=before.st_mtime, st_file_attributes=0x400
        )

        def reparse(candidate, *args, **kwargs):
            return info if candidate == path else original(candidate, *args, **kwargs)

        with patch.object(Path, "lstat", reparse):
            self.assertEqual(prune_cache(self.root, self.now), 0)
        self.assertTrue(path.exists())

    def test_expiry_is_24_elapsed_hours_across_daylight_saving_change(self):
        current = datetime(2026, 11, 1, 16, tzinfo=timezone.utc).astimezone(
            ZoneInfo("America/Tijuana")
        )
        path = self.owned("a", 25)
        stamp = current.timestamp() - 24 * 3600 - 1800
        os.utime(path, (stamp, stamp))
        self.assertEqual(prune_cache(self.root, current), 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
