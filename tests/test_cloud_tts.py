import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from plugins.astrbot_plugin_companion_core.cloud_tts import (
    generate,
    prune_cache,
    run_media_tool,
)


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    @staticmethod
    def communicator(text, voice):
        class Stream:
            async def stream(self):
                yield {"type": "audio", "data": b"fake decoded by a stub"}

        return Stream()

    async def test_generation_bounded_and_format_checked_not_device_success(self):
        probe = json.dumps(
            {"format": {"duration": "2.5"}, "streams": [{"codec_name": "mp3"}]}
        ).encode()
        with patch(
            "plugins.astrbot_plugin_companion_core.cloud_tts.run_media_tool",
            new_callable=AsyncMock,
            side_effect=[probe, b""],
        ) as tool:
            audio, result = await generate(
                "专用测试", self.root, communicator=self.communicator
            )
        self.assertTrue(audio.exists())
        self.assertEqual(result["decoder_check"], "passed")
        self.assertEqual(tool.await_count, 2)
        self.assertEqual(result["device_playback"], "not_observed")
        self.assertEqual(result["native_bubble"], "not_tested")
        self.assertNotIn("专用测试", audio.with_suffix(".local.json").read_text())

    async def test_oversize_input_never_enters_provider(self):
        for text in ("", "字" * 201):
            with self.assertRaises(ValueError):
                await generate(
                    text,
                    self.root,
                    communicator=lambda *_args, **_kwargs: self.fail("provider called"),
                )
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_oversize_audio_and_cancellation_cleanup_only_their_partial(self):
        preserved = self.root / "personal-notes.txt"
        preserved.write_text("keep")

        class TooLarge:
            async def stream(self):
                yield {"type": "audio", "data": b"x" * (2 * 1024 * 1024 + 1)}

        with self.assertRaises(ValueError):
            await generate(
                "测试", self.root, communicator=lambda *_args, **_kwargs: TooLarge()
            )

        class Cancelled:
            async def stream(self):
                yield {"type": "audio", "data": b"partial"}
                raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await generate(
                "测试", self.root, communicator=lambda *_args, **_kwargs: Cancelled()
            )
        self.assertEqual(list(self.root.iterdir()), [preserved])

    async def test_invalid_audio_not_exposed_as_success(self):
        probe = json.dumps(
            {"format": {"duration": "NaN"}, "streams": [{"codec_name": "mp3"}]}
        ).encode()
        with patch(
            "plugins.astrbot_plugin_companion_core.cloud_tts.run_media_tool",
            new_callable=AsyncMock,
            return_value=probe,
        ):
            with self.assertRaises(ValueError):
                await generate("测试", self.root, communicator=self.communicator)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_cache_24_hours_only_exact_owned_regular_files(self):
        current = datetime.now(timezone.utc)
        old = self.root / ("tts-" + "a" * 32 + ".mp3")
        current_audio = self.root / ("tts-" + "b" * 32 + ".mp3")
        unrelated = self.root / "voice-owner-original.mp3"
        for file in (old, current_audio, unrelated):
            file.write_bytes(b"owned test")
        expired = (current - timedelta(hours=25)).timestamp()
        os.utime(old, (expired, expired))
        os.utime(unrelated, (expired, expired))
        self.assertEqual(prune_cache(self.root, current), 1)
        self.assertTrue(current_audio.exists() and unrelated.exists())

    async def test_validation_timeout_kills_own_process(self):
        process = type("Process", (), {})()
        process.returncode = None
        process.communicate = AsyncMock(side_effect=TimeoutError)
        process.kill = unittest.mock.Mock()
        process.wait = AsyncMock()
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ):
            with self.assertRaises(TimeoutError):
                await run_media_tool("ffprobe", "owned-test.mp3")
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
