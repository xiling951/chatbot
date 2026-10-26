"""Bounded provider probe; run only with AstrBot stopped to own its allowance.

Compares one ordinary-chat prompt with thinking off/on. It is provider evidence,
not a phone delivery test or a complete
persona score. Keys and reasoning text are never printed or saved.
"""

import json
import os
import socket
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from openai import OpenAI
from local_paths import DATA, PLUGIN_DATA


def main():
    with socket.socket() as check:
        if check.connect_ex(("127.0.0.1", 6185)) == 0:
            raise SystemExit(
                "Stop AstrBot before this probe; allowance mutation must have a single owner"
            )
    config = json.loads((DATA / "cmd_config.json").read_text(encoding="utf-8-sig"))
    model = next(p for p in config["provider"] if p["id"] == "companion-deepseek")
    source = next(
        p for p in config["provider_sources"] if p["id"] == model["provider_source_id"]
    )
    if (
        model["model"] != "deepseek-flash"
        or source["api_base"].rstrip("/") != "https://api.deepseek.com"
    ):
        raise SystemExit(
            "This reservation supports only the verified official Flash endpoint"
        )
    persona = yaml.safe_load(
        (DATA / "plugins/astrbot_plugin_companion_core/persona.yaml").read_text(
            encoding="utf-8"
        )
    )
    output = PLUGIN_DATA / ("thinking-comparison-" + uuid.uuid4().hex + ".local.json")
    results = []
    prompt = "我今天有点累，不用给我解决方案，陪我说两句就好。"
    cases = [
        ("disabled", prompt),
        ("enabled", prompt),
    ]
    with OpenAI(
        api_key=source["key"][0], base_url=source["api_base"], timeout=30, max_retries=0
    ) as client:
        for thinking, text in cases:
            allowance_path = PLUGIN_DATA / "allowance.local.json"
            allowance = json.loads(allowance_path.read_text(encoding="utf-8"))
            today = datetime.now(ZoneInfo("America/Tijuana")).date().isoformat()
            if (
                allowance.get("authorized_date") != today
                or allowance["remaining_calls"] <= 0
                or allowance["reserved_cny_remaining"] < 0.05
            ):
                break
            if len((persona["system_prompt"] + text).encode("utf-8")) > 13000:
                raise SystemExit("Prompt exceeds the reserved request size")
            allowance["remaining_calls"] -= 1
            allowance["attempts_used"] = allowance.get("attempts_used", 0) + 1
            allowance["reserved_cny_remaining"] = round(
                allowance["reserved_cny_remaining"] - 0.05, 4
            )
            # The service is stopped, so there is no concurrent reader/writer.
            # Flush in place after this host returned EXDEV for os.replace;
            # no concurrent service reader/writer exists during this probe.
            with allowance_path.open("w", encoding="utf-8") as grant:
                json.dump(allowance, grant)
                grant.flush()
                os.fsync(grant.fileno())
            started = time.monotonic()
            result = {
                "thinking": thinking,
                "prompt": text,
                "max_tokens": 512,
                "transport": "direct official provider, no retry",
            }
            try:
                response = client.chat.completions.create(
                    model="deepseek-flash",
                    messages=[
                        {"role": "system", "content": persona["system_prompt"]},
                        {"role": "user", "content": text},
                    ],
                    max_tokens=512,
                    extra_body={"thinking": {"type": thinking}},
                    **({"reasoning_effort": "low"} if thinking == "enabled" else {}),
                )
                message = response.choices[0].message
                usage = response.usage
                cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
                counts = (
                    {
                        "input_other": usage.prompt_tokens - cached,
                        "input_cached": cached,
                        "output": usage.completion_tokens,
                    }
                    if usage
                    else None
                )
                result.update(
                    reply=message.content or "",
                    finish_reason=response.choices[0].finish_reason,
                    reasoning_present=bool(getattr(message, "reasoning_content", None)),
                    usage=counts,
                )
                with (PLUGIN_DATA / "usage.local.jsonl").open(
                    "a", encoding="utf-8"
                ) as log:
                    log.write(
                        json.dumps(
                            {
                                "trace_id": uuid.uuid4().hex,
                                "seconds": round(time.monotonic() - started, 3),
                                "usage": counts,
                                "thinking": thinking,
                                "source": "provider_comparison",
                            }
                        )
                        + "\n"
                    )
            except Exception as exc:
                result["error_type"] = type(exc).__name__
            result["seconds"] = round(time.monotonic() - started, 3)
            results.append(result)
            output.write_text(
                json.dumps(
                    {"cases": results, "persona_score": None},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result.get("error_type"):
                break
    print("Private evidence:", output, flush=True)


if __name__ == "__main__":
    main()
