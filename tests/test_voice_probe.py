import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugins.astrbot_plugin_companion_core.voice_probe import VoiceProbe


class VoiceProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.probe = VoiceProbe(self.root)
        self.current = datetime.now(timezone.utc)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_one_shot_is_scoped_and_expires(self):
        self.probe.arm("owner", "target", self.current)
        self.assertIsNone(self.probe.consume("stranger", "target", self.current))
        self.assertIsNotNone(self.probe.consume("owner", "target", self.current))
        self.assertIsNone(self.probe.consume("owner", "target", self.current))

    async def test_revoke_before_submission_and_cancelled_send_are_truthful(self):
        self.probe.arm("owner", "target", self.current)
        state = self.probe.consume("owner", "target", self.current)
        platform = SimpleNamespace(
            client=SimpleNamespace(
                download_and_decrypt_media=AsyncMock(return_value=b"audio")
            ),
            _prepare_media_item=AsyncMock(
                return_value={"file_item": {"media": {"encrypted": "test"}}}
            ),
            _send_items_to_session=AsyncMock(side_effect=asyncio.CancelledError),
        )
        voice = {
            "encode_type": 6,
            "playtime": 1000,
            "media": {"encrypt_query_param": "q", "aes_key": "k"},
        }
        blocked = await self.probe.replay(
            state, platform, "owner", voice, allowed=lambda: False
        )
        self.assertEqual(blocked["api_result"], "not_attempted")
        platform._send_items_to_session.assert_not_awaited()
        with self.assertRaises(asyncio.CancelledError):
            await self.probe.replay(state, platform, "owner", voice)
        report = json.loads(
            (self.root / f"voice-probe-{state['run_id']}.local.json").read_text()
        )
        self.assertEqual(report["api_result"], "unknown")
        self.assertEqual(report["error_type"], "CancelledError")
        platform._send_items_to_session.assert_awaited_once()
        self.assertFalse(list(self.root.glob("voice-probe-*.bin")))
        self.probe.arm("owner", "target", self.current)
        self.assertIsNone(
            self.probe.consume("owner", "target", self.current + timedelta(minutes=11))
        )

    async def test_native_voice_envelope_and_no_file_fallback(self):
        self.probe.arm("owner", "target", self.current)
        state = self.probe.consume("owner", "target", self.current)
        uploaded = []

        async def prepare(sender, path, media_type, item_type, name):
            self.assertEqual(media_type, 4)
            self.assertEqual(path.read_bytes(), b"owned-voice-sample")
            uploaded.append(path)
            return {
                "file_item": {
                    "media": {
                        "encrypt_query_param": "new-upload",
                        "aes_key": "new-key",
                        "encrypt_type": 1,
                    }
                }
            }

        platform = SimpleNamespace(
            client=SimpleNamespace(
                download_and_decrypt_media=AsyncMock(return_value=b"owned-voice-sample")
            ),
            _prepare_media_item=prepare,
            _send_items_to_session=AsyncMock(return_value=True),
        )
        voice = {
            "encode_type": 6,
            "playtime": 1500,
            "sample_rate": 24000,
            "bits_per_sample": 16,
            "media": {"encrypt_query_param": "owned-input", "aes_key": "owned-key"},
        }
        result = await self.probe.replay(state, platform, "owner", voice)
        items = platform._send_items_to_session.call_args.args[1]
        self.assertEqual(items[0]["type"], 3)
        self.assertIn("voice_item", items[0])
        self.assertNotIn("file_item", items[0])
        self.assertEqual(result["api_result"], "accepted_by_platform")
        self.assertEqual(result["native_bubble_observation"], "not_observed")
        self.assertFalse(uploaded[0].exists())

    async def test_invalid_or_uncertain_attempt_never_retries(self):
        self.probe.arm("owner", "target", self.current)
        state = self.probe.consume("owner", "target", self.current)
        platform = SimpleNamespace(
            client=SimpleNamespace(
                download_and_decrypt_media=AsyncMock(
                    side_effect=TimeoutError("simulated")
                )
            ),
            _send_items_to_session=AsyncMock(),
        )
        voice = {
            "encode_type": 6,
            "playtime": 1000,
            "media": {"encrypt_query_param": "q", "aes_key": "k"},
        }
        result = await self.probe.replay(state, platform, "owner", voice)
        self.assertEqual(result["api_result"], "not_attempted")
        platform.client.download_and_decrypt_media.assert_awaited_once()
        platform._send_items_to_session.assert_not_awaited()
        self.assertIsNone(self.probe.consume("owner", "target", self.current))
