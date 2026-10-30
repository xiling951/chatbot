"""One verified Flash vision request, without the framework's text-only fallback.

Use the existing private AstrBot provider configuration. Never log credentials,
image bodies or raw provider exceptions. The caller disables image analysis by
default and rechecks private-chat authorization before and after this request.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .bilibili import loopback_proxy
from .vision import ImageRejected, PreparedImage


@dataclass(frozen=True)
class VisionResult:
    completion_text: str
    usage: Any
    model: str
    finish_reason: str | None
    configuration_changed: bool


def _route(path: Path, provider_id: str):
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        model = next(p for p in config["provider"] if p["id"] == provider_id)
        source = next(
            p for p in config["provider_sources"]
            if p["id"] == model["provider_source_id"]
        )
        if (
            model.get("enable") is not True
            or model.get("model") != "deepseek-flash"
            or source.get("type") != "openai_chat_completion"
            or not isinstance(source.get("api_base"), str)
            or source.get("api_base", "").rstrip("/") != "https://api.deepseek.com"
            or not isinstance(source.get("key"), list)
            or not source["key"]
            or not isinstance(source["key"][0], str)
            or not source["key"][0]
        ):
            raise ValueError()
        proxy = source.get("proxy") or None
        if proxy is not None:
            proxy = loopback_proxy(proxy)
        signature = hashlib.sha256(json.dumps(
            {"model": model, "source": source}, sort_keys=True
        ).encode()).hexdigest()
        return source["key"][0], proxy, signature
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        raise ImageRejected("vision_provider_unavailable") from None


async def generate_once(
    *, config_path: Path, provider_id: str, system_prompt: str, prompt: str,
    history: list[dict], image: PreparedImage, allowed: Callable[[], bool],
) -> VisionResult:
    from openai import AsyncOpenAI, _base_client
    from astrbot.core.provider.entities import TokenUsage

    if not allowed():
        raise ImageRejected("image_authorization_changed")
    key, proxy, signature = _route(config_path, provider_id)
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        if turn.get("role") not in ("user", "assistant") or not isinstance(turn.get("content"), str):
            raise ImageRejected("invalid_image_context")
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image.data_url(), "detail": "low"}},
        ],
    })
    # The pinned SDK can use an httpx compatibility module distinct from the
    # globally imported httpx package. Construct its expected client type.
    http = getattr(_base_client, "httpx", None) or _base_client.httpx2
    async with http.AsyncClient(
        proxy=proxy, trust_env=False, verify=True, timeout=30,
    ) as transport:
        async with AsyncOpenAI(
            api_key=key, base_url="https://api.deepseek.com", http_client=transport,
            timeout=30, max_retries=0,
        ) as client:
            if not allowed():
                raise ImageRejected("image_authorization_changed")
            response = await client.chat.completions.create(
                model="deepseek-flash", messages=messages, max_tokens=512,
                extra_body={"thinking": {"type": "disabled"}},
            )
    usage = response.usage
    cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    token_usage = TokenUsage(
        usage.prompt_tokens - cached, cached, usage.completion_tokens,
    ) if usage else None
    try:
        changed = _route(config_path, provider_id)[2] != signature
    except ImageRejected:
        changed = True
    return VisionResult(
        completion_text=response.choices[0].message.content or "",
        usage=token_usage, model=response.model,
        finish_reason=response.choices[0].finish_reason,
        configuration_changed=changed,
    )
