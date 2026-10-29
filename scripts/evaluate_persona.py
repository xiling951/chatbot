"""Actual plugin pipeline + real provider; isolated synthetic events, no IM/Web.

Reuse the existing framework fixture, replacing only its model transport and
attempt reservation. All real attempts use the live locked accounting/switches;
the fixture's old finite allowance never governs these authorized calls.
"""

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml
from openai import AsyncOpenAI

from local_paths import CODE_ROOT, DATA, PLUGIN_DATA, PROJECT_ROOT

sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "tests"), str(CODE_ROOT)]
from test_astrbot_integration import IntegrationTests, LocalEvent  # noqa: E402
from astrbot.core.provider.entities import LLMResponse, TokenUsage  # noqa: E402
from plugins.astrbot_plugin_companion_core import main as core  # noqa: E402
from plugins.astrbot_plugin_companion_core.call_policy import reserve_attempt  # noqa: E402
from plugins.astrbot_plugin_companion_core.emotion import EmotionState  # noqa: E402


def assert_enabled():
    settings = json.loads((PLUGIN_DATA / "settings.local.json").read_text())
    if (PLUGIN_DATA / "STOP").exists() or not settings.get("model_enabled"):
        raise ValueError("Live model switch is closed")


async def evaluate(args):
    case_path = (
        PROJECT_ROOT
        / "tests"
        / (
            "persona_holdout_v2.json"
            if args.set == "fresh-heldout"
            else "persona_cases.json"
        )
    )
    case_bytes = case_path.read_bytes()
    cases = [
        c
        for c in json.loads(case_bytes)
        if c["held_out"] == (args.set == "fresh-heldout")
    ]
    if args.ids:
        ids = args.ids.split(",")
        if len(ids) != len(set(ids)) or not set(ids) <= {c["id"] for c in cases}:
            raise ValueError("Select distinct IDs in the explicit scenario set")
        cases = [c for c in cases if c["id"] in ids]
    persona_path = (
        args.persona_file or DATA / "plugins/astrbot_plugin_companion_core/persona.yaml"
    )
    persona_bytes = persona_path.read_bytes()
    config = json.loads((DATA / "cmd_config.json").read_text(encoding="utf-8-sig"))
    model = next(p for p in config["provider"] if p["id"] == "companion-deepseek")
    source = next(
        p for p in config["provider_sources"] if p["id"] == model["provider_source_id"]
    )
    if (
        model["model"] != "deepseek-flash"
        or source["api_base"].rstrip("/") != "https://api.deepseek.com"
    ):
        raise ValueError("Only the verified official Flash provider is supported")
    run_id = uuid.uuid4().hex
    report = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "real plugin classes/prompt/history/guard + real provider; isolated synthetic events, no Web/IM",
        "persona_sha256": hashlib.sha256(persona_bytes).hexdigest(),
        "core_source_sha256": hashlib.sha256(
            Path(core.__file__).read_bytes()
        ).hexdigest(),
        "reply_guard_sha256": hashlib.sha256(
            (
                PROJECT_ROOT / "plugins/astrbot_plugin_companion_core/reply_guard.py"
            ).read_bytes()
        ).hexdigest(),
        "cases_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "case_set": args.set,
        "selected_ids": [c["id"] for c in cases],
        "fresh_identity_per_scenario": True,
        "human_score": None,
        "completed": False,
        "cost_basis": "USD peak upper estimate, not invoice; https://api-docs.deepseek.com/quick_start/pricing/ verified 2026-10-28",
        "scenarios": [],
    }
    output = PLUGIN_DATA / f"persona-framework-{run_id}.local.json"
    fixture = IntegrationTests()
    await fixture.asyncSetUp()
    plugin = fixture.plugin
    plugin.persona = yaml.safe_load(persona_bytes)
    plugin.emotion = EmotionState(
        plugin.store,
        yaml.safe_load(
            (PROJECT_ROOT / "config/emotion_policy.yaml").read_text(encoding="utf-8")
        ),
    )
    requests = []

    def reserve(_path, content_bytes):
        assert_enabled()
        return reserve_attempt(PLUGIN_DATA / "allowance.local.json", content_bytes)

    try:
        async with AsyncOpenAI(
            api_key=source["key"][0],
            base_url=source["api_base"],
            timeout=30,
            max_retries=0,
        ) as client:

            async def generate(**kwargs):
                assert_enabled()
                request = {
                    "system_prompt_sha256": hashlib.sha256(
                        kwargs["system_prompt"].encode()
                    ).hexdigest(),
                    "contexts": [
                        {"role": c.role, "text": "".join(p.text for p in c.content)}
                        for c in kwargs["contexts"]
                    ],
                }
                requests.append(request)
                try:
                    response = await client.chat.completions.create(
                        model="deepseek-flash",
                        messages=[
                            {"role": "system", "content": kwargs["system_prompt"]},
                            *[
                                {
                                    "role": c.role,
                                    "content": "".join(p.text for p in c.content),
                                }
                                for c in kwargs["contexts"]
                            ],
                            {"role": "user", "content": kwargs["prompt"]},
                        ],
                        max_tokens=512,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    usage = response.usage
                    cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
                    request.update(
                        observed_model=response.model,
                        raw_reply=response.choices[0].message.content or "",
                        finish_reason=response.choices[0].finish_reason,
                        usage={
                            "input_other": usage.prompt_tokens - cached,
                            "input_cached": cached,
                            "output": usage.completion_tokens,
                        },
                    )
                    return LLMResponse(
                        "assistant",
                        completion_text=request["raw_reply"],
                        usage=TokenUsage(
                            usage.prompt_tokens - cached,
                            cached,
                            usage.completion_tokens,
                        ),
                    )
                except Exception as exc:
                    request["error_type"] = type(exc).__name__
                    raise

            plugin.context.llm_generate.side_effect = generate
            with patch.object(core, "reserve_attempt", side_effect=reserve):
                for case in cases:
                    scenario = {
                        "id": case["id"],
                        "check": case["check"],
                        "turns": [],
                        "human_score": None,
                    }
                    report["scenarios"].append(scenario)
                    for index, prompt in enumerate(case["turns"]):
                        assert_enabled()
                        before = len(requests)
                        started = time.monotonic()
                        event = LocalEvent(
                            prompt,
                            f"{run_id}-{case['id']}-{index}",
                            sender=f"persona-eval-{run_id}-{case['id']}",
                        )
                        await plugin.handle(event)
                        row = {
                            "prompt": prompt,
                            "reply": event.sent,
                            "seconds": round(time.monotonic() - started, 3),
                            "provider_requests": requests[before:],
                        }
                        scenario["turns"].append(row)
                        # Copy the fixture's latest generated usage entry before its
                        # temporary directory is removed. Commands make no model call.
                        usage_path = plugin.home / "usage.local.jsonl"
                        if (
                            len(requests) > before
                            and usage_path.exists()
                            and not any(r.get("error_type") for r in requests[before:])
                        ):
                            usage_row = json.loads(
                                usage_path.read_text().splitlines()[-1]
                            )
                            row["pipeline_usage"] = usage_row
                            counts = usage_row["usage"]
                            row["estimated_usd_peak"] = (
                                counts["input_other"] * 0.30
                                + counts["input_cached"] * 0.006
                                + counts["output"] * 1.20
                            ) / 1_000_000
                            with (PLUGIN_DATA / "usage.local.jsonl").open(
                                "a", encoding="utf-8"
                            ) as stream:
                                stream.write(
                                    json.dumps(
                                        usage_row
                                        | {
                                            "source": "persona_framework_evaluation",
                                            "run_id": run_id,
                                            "case_id": case["id"],
                                        }
                                    )
                                    + "\n"
                                )
                        output.write_text(
                            json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        if len(event.sent) != 1 or any(
                            r.get("error_type") for r in requests[before:]
                        ):
                            raise RuntimeError(
                                "Persona event failed; preserve evidence without retry"
                            )
                    print("Scenario captured:", case["id"], flush=True)
            report["completed"] = True
    finally:
        await fixture.asyncTearDown()
        report["synthetic_store_removed"] = not plugin.home.exists()
        report["live_persona_changed"] = False
        report["im_sent"] = False
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        turns = [t for c in report["scenarios"] for t in c["turns"]]
        seconds = sorted(t["seconds"] for t in turns)
        report["summary"] = {
            "scenarios": len(report["scenarios"]),
            "turns": len(turns),
            "provider_attempts": len(requests),
            "estimated_usd_peak": round(
                sum(t.get("estimated_usd_peak", 0) for t in turns), 9
            ),
            "p50_seconds": seconds[math.ceil(len(seconds) * 0.5) - 1]
            if seconds
            else None,
            "p95_seconds": seconds[math.ceil(len(seconds) * 0.95) - 1]
            if seconds
            else None,
            "quantile_method": "nearest_rank; pipeline capture, not device latency",
        }
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Private evidence:", output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona-file", type=Path)
    parser.add_argument(
        "--set", choices=["development", "fresh-heldout"], default="development"
    )
    parser.add_argument("--ids", default="")
    asyncio.run(evaluate(parser.parse_args()))
