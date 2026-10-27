"""Bounded direct-provider comparison using shared locked attempt accounting.

Compares one ordinary-chat prompt with thinking off/on. It is provider evidence,
not a phone delivery test or a complete
persona score. Keys and reasoning text are never printed or saved.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
import time
import uuid

import yaml
from openai import OpenAI
from local_paths import DATA, PLUGIN_DATA

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.astrbot_plugin_companion_core.call_policy import reserve_attempt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--persona-ids",
        default="",
        help="Comma-separated development scenarios; empty runs the original one-prompt probe",
    )
    parser.add_argument(
        "--persona-file",
        type=Path,
        help="Optional candidate profile; direct provider evidence only",
    )
    parser.add_argument(
        "--modes", choices=["both", "disabled", "enabled"], default="both"
    )
    args = parser.parse_args()
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
    persona_path = (
        args.persona_file or DATA / "plugins/astrbot_plugin_companion_core/persona.yaml"
    )
    persona_bytes = persona_path.read_bytes()
    persona = yaml.safe_load(persona_bytes)
    output = PLUGIN_DATA / ("thinking-comparison-" + uuid.uuid4().hex + ".local.json")
    results = []
    prompt = "我今天有点累，不用给我解决方案，陪我说两句就好。"
    scenarios = [{"id": "single-probe", "turns": [prompt]}]
    if args.persona_ids:
        selected = args.persona_ids.split(",")
        available = {
            c["id"]: c
            for c in json.loads(
                (
                    Path(__file__).resolve().parents[1] / "tests/persona_cases.json"
                ).read_text(encoding="utf-8")
            )
            if not c["held_out"]
        }
        if len(selected) != len(set(selected)) or not set(selected).issubset(available):
            raise SystemExit(
                "Select distinct development scenarios only; held-out cases are excluded"
            )
        scenarios = [available[key] for key in selected]
    cases = []
    for index, scenario in enumerate(scenarios):
        modes = ("disabled", "enabled") if index % 2 == 0 else ("enabled", "disabled")
        if args.modes != "both":
            modes = (args.modes,)
        for mode in modes:
            for text in scenario["turns"]:
                cases.append((mode, scenario["id"], text))
    histories = {}
    with OpenAI(
        api_key=source["key"][0], base_url=source["api_base"], timeout=30, max_retries=0
    ) as client:
        for thinking, case_id, text in cases:
            settings = json.loads(
                (PLUGIN_DATA / "settings.local.json").read_text(encoding="utf-8")
            )
            if (PLUGIN_DATA / "STOP").exists() or not settings.get("model_enabled"):
                raise SystemExit("Model calls stopped by local switch")
            history = histories.setdefault((thinking, case_id), [])
            # Match the plugin's three-pair, 4000-byte short-history boundary.
            while (
                history
                and len("".join(m["content"] for m in history).encode("utf-8")) > 4000
            ):
                del history[:2]
            allowance_path = PLUGIN_DATA / "allowance.local.json"
            attempt = reserve_attempt(
                allowance_path,
                len(
                    (
                        persona["system_prompt"]
                        + text
                        + "".join(m["content"] for m in history)
                    ).encode("utf-8")
                ),
            )
            started = time.monotonic()
            result = {
                "thinking": thinking,
                "case_id": case_id,
                "prompt": text,
                "max_tokens": 512,
                "transport": "direct official provider, no retry",
            }
            try:
                response = client.chat.completions.create(
                    model="deepseek-flash",
                    messages=[
                        {"role": "system", "content": persona["system_prompt"]},
                        *history,
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
                    estimated_usd_peak=(
                        round(
                            (
                                counts["input_other"] * 0.30
                                + counts["input_cached"] * 0.006
                                + counts["output"] * 1.20
                            )
                            / 1_000_000,
                            9,
                        )
                        if counts
                        else None
                    ),
                )
                history.extend(
                    [
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": result["reply"]},
                    ]
                )
                del history[:-6]
                with (PLUGIN_DATA / "usage.local.jsonl").open(
                    "a", encoding="utf-8"
                ) as log:
                    log.write(
                        json.dumps(
                            {
                                "trace_id": uuid.uuid4().hex,
                                "attempt": attempt,
                                "seconds": round(time.monotonic() - started, 3),
                                "usage": counts,
                                "thinking": thinking,
                                "source": "provider_comparison",
                                "case_id": case_id,
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
                    {
                        "cases": results,
                        "persona_sha256": hashlib.sha256(persona_bytes).hexdigest(),
                        "profile_source": "candidate file"
                        if args.persona_file
                        else "deployed file",
                        "persona_score": None,
                        "scope": "direct provider; no tools, persistent memory, phone or backend command execution",
                        "cost_basis": "USD peak upper estimate, not invoice; https://api-docs.deepseek.com/quick_start/pricing/ verified 2026-10-26",
                    },
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
