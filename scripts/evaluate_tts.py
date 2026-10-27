"""Generate ten public, synthetic Chinese samples; no IM send or model call."""

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

from local_paths import PLUGIN_DATA, PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))
from plugins.astrbot_plugin_companion_core.cloud_tts import generate


async def main():
    cases = json.loads(
        (PROJECT_ROOT / "tests/media_cases.json").read_text(encoding="utf-8")
    )["tts_cases"]
    report = {
        "run_id": uuid.uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cases": [],
        "device_playback": "not_observed",
        "native_bubble": "not_tested",
        "omitted_words_human_check": None,
    }
    path = PLUGIN_DATA / f"tts-evaluation-{report['run_id']}.local.json"
    for case in cases:
        result = {"id": case["id"]}
        try:
            audio, metadata = await generate(case["text"], PLUGIN_DATA / "tts-cache")
            result.update(metadata, generated=True, file=audio.name)
        except Exception as exc:
            result.update(generated=False, error_type=type(exc).__name__)
        report["cases"].append(result)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: result.get(k)
                    for k in (
                        "id",
                        "generated",
                        "error_type",
                        "generation_seconds",
                        "duration_seconds",
                    )
                }
            ),
            flush=True,
        )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence:", path.name)
    (PLUGIN_DATA / "tts-samples.local.json").write_text(
        json.dumps(
            {
                case["id"]: case["file"]
                for case in report["cases"]
                if case.get("generated")
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
