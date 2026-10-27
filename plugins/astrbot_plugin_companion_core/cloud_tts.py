"""Opt-in stock-voice cloud synthesis; bounded cache, no voice cloning."""

import asyncio
import json
import math
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

VOICE = "zh-CN-XiaoxiaoNeural"
OWNED_AUDIO = re.compile(r"tts-[0-9a-f]{32}\.mp3")


def prune_cache(folder: Path, current: datetime) -> int:
    if not folder.exists():
        return 0
    root = folder.resolve()
    removed = 0
    for path in folder.iterdir():
        if (
            not OWNED_AUDIO.fullmatch(path.name)
            or path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != root
        ):
            continue
        if datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc
        ) < current - timedelta(hours=24):
            try:
                path.unlink()
                path.with_suffix(".local.json").unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
    return removed


async def run_media_tool(*arguments):
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=8)
        if process.returncode:
            raise ValueError("Audio validation failed")
        return stdout
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise


async def generate(text: str, folder: Path, *, communicator=None) -> tuple[Path, dict]:
    if not text.strip() or len(text.encode("utf-8")) > 600:
        raise ValueError("朗读内容须为1至600 UTF-8字节（约200个汉字）")
    folder.mkdir(exist_ok=True)
    prune_cache(folder, datetime.now(timezone.utc))
    if communicator is None:
        import edge_tts

        communicator = edge_tts.Communicate
    output = folder / f"tts-{uuid.uuid4().hex}.mp3"
    started = time.monotonic()
    count = 0
    try:
        async with asyncio.timeout(25):
            with output.open("xb") as stream:
                async for event in communicator(text, voice=VOICE).stream():
                    if event["type"] != "audio":
                        continue
                    count += len(event["data"])
                    if count > 2 * 1024 * 1024:
                        raise ValueError("Generated audio exceeds 2 MiB")
                    stream.write(event["data"])
        if not count:
            raise ValueError("No generated audio received")
        info = json.loads(
            await run_media_tool(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(output),
            )
        )
        duration = float(info["format"]["duration"])
        if (
            not math.isfinite(duration)
            or not 0 < duration <= 60
            or info["streams"][0]["codec_name"] != "mp3"
        ):
            raise ValueError("Unsupported audio format or duration")
        await run_media_tool(
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(output),
            "-f",
            "null",
            "-",
        )
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": "Microsoft Edge online via edge-tts 7.3.1",
            "voice": VOICE,
            "generation_seconds": round(time.monotonic() - started, 3),
            "duration_seconds": duration,
            "bytes": count,
            "decoder_check": "passed",
            "platform_acceptance": "not_tested",
            "device_playback": "not_observed",
            "native_bubble": "not_tested",
        }
        output.with_suffix(".local.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return output, report
    except BaseException:
        output.unlink(missing_ok=True)
        output.with_suffix(".local.json").unlink(missing_ok=True)
        raise
