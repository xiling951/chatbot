"""One-shot owner-requested native voice replay, not TTS or voice cloning.

API acceptance is never treated as a rendered/playable client voice bubble.
No automatic retry and no inference/long-term fact write occurs here.
"""

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filelock import FileLock


class VoiceProbe:
    def __init__(self, home: Path):
        self.home = home
        self.path = home / "voice-probe-state.local.json"

    def arm(self, scope: str, target: str, current: datetime):
        state = {
            "run_id": uuid.uuid4().hex,
            "scope": scope,
            "target_hash": hashlib.sha256(target.encode()).hexdigest(),
            "expires_at": (current + timedelta(minutes=10)).isoformat(),
            "used": False,
        }
        with FileLock(str(self.path) + ".lock", timeout=3):
            self.path.write_text(json.dumps(state), encoding="utf-8")
        return state["run_id"]

    def consume(self, scope: str, target: str, current: datetime) -> dict | None:
        if not self.path.exists():
            return None
        with FileLock(str(self.path) + ".lock", timeout=3):
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if (
                state.get("used")
                or state.get("scope") != scope
                or state.get("target_hash")
                != hashlib.sha256(target.encode()).hexdigest()
            ):
                return None
            try:
                until = datetime.fromisoformat(state["expires_at"])
                if until.tzinfo is None or current >= until:
                    return None
            except (KeyError, ValueError, TypeError):
                return None
            state["used"] = True
            with self.path.open("w", encoding="utf-8") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            return state

    async def replay(
        self, state: dict, platform, sender: str, voice: dict, *, allowed=lambda: True
    ) -> dict:
        report = {
            "run_id": state["run_id"],
            "scope": state["scope"],
            "at": datetime.now(timezone.utc).isoformat(),
            "purpose": "owner-requested original sample replay; no TTS/voice cloning",
            "api_result": "not_attempted",
            "native_bubble_observation": "not_observed",
            "read_state": "unknown",
        }
        temporary = None
        submitted = False
        try:
            codec = voice.get("encode_type")
            duration = voice.get("playtime")
            media = voice.get("media") or {}
            if (
                type(codec) is not int
                or not 1 <= codec <= 8
                or type(duration) is not int
                or not 1 <= duration <= 10000
                or not media.get("encrypt_query_param")
                or not media.get("aes_key")
            ):
                raise ValueError(
                    "Voice sample lacks bounded codec/duration/CDN metadata"
                )
            content = await asyncio.wait_for(
                platform.client.download_and_decrypt_media(
                    media["encrypt_query_param"], media["aes_key"]
                ),
                timeout=15,
            )
            if not content or len(content) > 1024 * 1024:
                raise ValueError("Voice sample exceeds test size")
            descriptor, name = tempfile.mkstemp(
                prefix="voice-probe-", suffix=".bin", dir=self.home
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            # The pinned SDK uploads any media type, but only formats image,
            # video and file envelopes. Reuse its VOICE(4) CDN upload and build
            # the VOICE(3) message envelope separately; never send a file item.
            prepared = await asyncio.wait_for(
                platform._prepare_media_item(
                    sender, temporary, 4, 4, "voice-probe.bin"
                ),
                timeout=15,
            )
            outgoing = {
                "media": prepared["file_item"]["media"],
                "encode_type": codec,
                "playtime": duration,
            }
            for key in ("sample_rate", "bits_per_sample"):
                if type(voice.get(key)) is int:
                    outgoing[key] = voice[key]
            if not allowed():
                raise PermissionError("Local stop or authorization changed")
            submitted = True
            accepted = await asyncio.wait_for(
                platform._send_items_to_session(
                    sender,
                    [{"type": 3, "voice_item": outgoing}],
                    cache_components=[],
                    cache_message_str="[本人授权的语音气泡回放测试]",
                ),
                timeout=15,
            )
            report.update(
                api_result="accepted_by_platform" if accepted else "not_accepted",
                codec=codec,
                playtime_ms=duration,
                byte_count=len(content),
                sample_sha256=hashlib.sha256(content).hexdigest(),
            )
        except (Exception, asyncio.CancelledError) as exc:
            report.update(
                api_result="unknown" if submitted else "not_attempted",
                error_type=type(exc).__name__,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            (self.home / f"voice-probe-{state['run_id']}.local.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return report
