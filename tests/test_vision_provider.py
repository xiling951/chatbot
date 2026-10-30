"""Exercise the actual pinned SDK against an in-process HTTP transport."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai import _base_client
from openai import BadRequestError

from plugins.astrbot_plugin_companion_core.vision import ImageRejected, PreparedImage
from plugins.astrbot_plugin_companion_core.vision_provider import _route, generate_once

HTTP = getattr(_base_client, "httpx", None) or _base_client.httpx2


class VisionProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.config = {
            "provider": [{"id": "test", "enable": True, "model": "deepseek-flash", "provider_source_id": "source"}],
            "provider_sources": [{"id": "source", "type": "openai_chat_completion", "api_base": "https://api.deepseek.com", "key": ["synthetic-key"]}],
        }
        self.save()
        self.requests = []

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def call(self, handler, allowed=lambda: True):
        http = HTTP
        original = http.AsyncClient

        test = self

        class LocalClient(original):
            def __init__(self, **kwargs):
                test.assertFalse(kwargs["trust_env"])
                test.assertTrue(kwargs["verify"])
                super().__init__(**kwargs, transport=http.MockTransport(handler))

        with patch.object(http, "AsyncClient", LocalClient):
            return await generate_once(
                config_path=self.path, provider_id="test", system_prompt="trusted",
                prompt="describe", history=[{"role": "user", "content": "old text"}],
                image=PreparedImage(1, 1, 3, "source", "payload", b"jpg"), allowed=allowed,
            )

    def response(self, request):
        self.requests.append(request)
        return HTTP.Response(200, json={
            "id": "vision-test", "object": "chat.completion", "created": 1, "model": "deepseek-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "绿色"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 30, "prompt_cache_hit_tokens": 5, "completion_tokens": 3, "total_tokens": 33},
        })

    async def test_single_multimodal_request_and_actual_usage(self):
        result = await self.call(self.response)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(str(self.requests[0].url), "https://api.deepseek.com/chat/completions")
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 512)
        self.assertTrue(body["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(result.completion_text, "绿色")
        self.assertFalse(result.configuration_changed)
        self.assertEqual(result.usage.input_other, 25)

    async def test_bad_request_never_retries_without_image(self):
        def refused(request):
            self.requests.append(request)
            return HTTP.Response(400, json={"error": {"message": "unsupported image", "type": "invalid_request_error"}})

        with self.assertRaises(BadRequestError):
            await self.call(refused)
        self.assertEqual(len(self.requests), 1)

    async def test_revocation_before_http_and_changed_route_after_response(self):
        with self.assertRaisesRegex(ImageRejected, "image_authorization_changed"):
            await self.call(self.response, allowed=lambda: False)
        self.assertEqual(self.requests, [])

        def changed(request):
            response = self.response(request)
            self.config["provider"][0]["enable"] = False
            self.save()
            return response

        self.assertTrue((await self.call(changed)).configuration_changed)

    async def test_only_configured_official_flash_route(self):
        for key, value in (("api_base", "https://other.example"), ("key", []), ("proxy", "http://other.example:80")):
            original = self.config["provider_sources"][0].copy()
            self.config["provider_sources"][0][key] = value
            self.save()
            with self.subTest(key=key), self.assertRaisesRegex(ImageRejected, "vision_provider_unavailable"):
                _route(self.path, "test")
            self.config["provider_sources"][0] = original
        self.config["provider"][0]["model"] = "other"
        self.save()
        with self.assertRaises(ImageRejected):
            _route(self.path, "test")


if __name__ == "__main__":
    unittest.main()
