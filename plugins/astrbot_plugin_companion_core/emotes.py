"""Optional original image replies, selected by code from a finite intent set."""

from __future__ import annotations

import hashlib
import base64
import json
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger.emote_preferences(
 scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
 last_attempt TEXT, blocked_until TEXT);
CREATE TABLE IF NOT EXISTS ledger.emote_submissions(
 source TEXT NOT NULL, scope TEXT NOT NULL, asset TEXT NOT NULL,
 state TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(scope,source));
"""
COOLDOWN = timedelta(minutes=10)
FAILURE_PAUSE = timedelta(minutes=30)
SERIOUS = re.compile(
    r"自杀|自残|自伤|轻生|不想活|想死|活不下去|跳楼|杀人|伤害(?:自己|别人)|"
    r"去世|过世|离世|葬礼|丧事|丧亲|死了|(?:爸|妈|爷|奶|亲人|家人|朋友|猫|狗).{0,8}(?:没了|不在了|走了)|病危|重病|癌|急救|救命|住院|手术|"
    r"生病|发烧|疼痛|头痛|胸痛|药物|服药|诈骗|报警|暴力|家暴|性侵|骚扰|"
    r"失业|裁员|破产|欠债|崩溃|绝望|惊恐|恐慌|痛苦|抑郁|难过|伤心|害怕|"
    r"隐私|密码|密钥|身份证|删除|停止记忆|忘记|不要.{0,8}表情|别.{0,8}表情|严肃|认真说|别开玩笑|"
    r"suicid|self.harm|dying|funeral|cancer|emergency|abuse|assault",
    re.I,
)
MARKER = re.compile(r"\[\[EMOTE:([^\]\n]*)\]\]", re.I)
CONTROL_LINE = re.compile(r"\[\[EMOTE:[^\n]*", re.I)


def normalize_command(text):
    if re.fullmatch(
        r"(?:请)?(?:以后)?(?:不要|别|停止)(?:再)?(?:给我)?(?:发|发送|使用|用)?(?:图片)?表情(?:包)?[。！!]?[ \t]*",
        text,
    ):
        return "/表情 关"
    return text


def extract_intent(reply: str):
    markers = list(MARKER.finditer(reply))
    intent = None
    if len(markers) == 1:
        match = markers[0]
        # One terminal control marker; a missing line break is harmless. Paths,
        # multiple markers and control markers followed by prose remain invalid.
        if not reply[match.end() :].strip():
            if re.fullmatch(r"[a-z_]{1,24}", match[1]):
                intent = match[1]
    return CONTROL_LINE.sub("", reply).strip(), intent


class EmoteLibrary:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        if self.folder.is_symlink() or self.folder.parent.is_symlink():
            raise ValueError("Emote material must be an owned directory")
        manifest = json.loads(
            (self.folder / "manifest.json").read_text(encoding="utf-8")
        )
        entries = manifest["assets"]
        if manifest.get("format") != 1 or not 30 <= len(entries) <= 50:
            raise ValueError("Invalid emote manifest")
        self.entries = {}
        for entry in entries:
            identifier = entry["id"]
            if (
                not re.fullmatch(r"E(?:0[1-9]|[1-4][0-9]|50)", identifier)
                or identifier in self.entries
            ):
                raise ValueError("Invalid or duplicate emote identifier")
            if entry["file"] != identifier + ".png" or not re.fullmatch(
                r"[a-z_]{1,24}", entry["intent"]
            ):
                raise ValueError("Emote path/intent is not from the finite asset set")
            self.entries[identifier] = entry
        self.intents = {entry["intent"]: entry["id"] for entry in entries}

    def resolve(self, identifier):
        entry = self.entries.get(identifier)
        if not entry:
            raise ValueError("没有这个表情编号；可查看 /表情帮助。")
        target = self.folder / entry["file"]
        if target.is_symlink() or target.resolve().parent != self.folder.resolve():
            raise ValueError("Emote asset escaped its owned folder")
        data = target.read_bytes()
        if (
            not 40 < len(data) <= 128 * 1024
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Emote asset integrity failed")
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("Emote asset is not a PNG")
        width, height = struct.unpack(">II", data[16:24])
        if (
            (width, height) != (entry["width"], entry["height"])
            or not 1 <= width <= 512
            or not 1 <= height <= 512
        ):
            raise ValueError("Emote dimensions exceed platform test bound")
        return target

    def choose(self, intent, text, reply, history=()):
        recent = "\n".join(t["content"] for t in history[-6:] if t["role"] == "user")
        if SERIOUS.search(text + "\n" + reply + "\n" + recent):
            return None
        return self.intents.get(intent)

    def instruction(self):
        return (
            "\n本会话已开启可选图片表情。正常回复文字后，必须在最后单独一行给出一个表达意图标记，格式是[[EMOTE:意图]]。"
            "允许意图："
            + ",".join(
                f"{intent}={self.entries[identifier]['caption']}"
                for intent, identifier in sorted(self.intents.items())
            )
            + "。例如轻松问候用[[EMOTE:greeting]]，祝贺小进步用[[EMOTE:small_win]]。"
            "没有合适意图、普通无情绪回答、严肃/隐私/健康/危险/丧失/负面情绪或用户不想要表情时，使用[[EMOTE:none]]。"
            "标记只是供程序选择的意图，不是发送指令，不保证发图；实际开关、冷却和严肃场景检查由程序决定。"
            "标记不显示给用户，不选文件路径或编号，不声称图片已经发出。"
        )


@dataclass(frozen=True)
class ImageAttempt:
    source: str
    scope: str
    asset: str
    memory_revision: int
    preference_revision: int


class EmoteStore:
    def __init__(self, memory):
        self.memory, self.db = memory, memory.db

    def enabled(self, scope):
        row = self.db.execute(
            "SELECT enabled FROM ledger.emote_preferences WHERE scope=?", (scope,)
        ).fetchone()
        return bool(row and row[0])

    def set_enabled(self, scope, enabled):
        with self.db:
            self.db.execute(
                "INSERT INTO ledger.emote_preferences VALUES(?,?,1,NULL,NULL) ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled,revision=revision+1",
                (scope, int(enabled)),
            )

    def reserve(self, ticket, asset, current, *, manual=False):
        with self.db:
            self.memory._check_ticket(ticket)
            row = self.db.execute(
                "SELECT * FROM ledger.emote_preferences WHERE scope=?", (ticket.scope,)
            ).fetchone()
            if not manual:
                if not row or not row["enabled"]:
                    return None
                if row["blocked_until"] and current < datetime.fromisoformat(
                    row["blocked_until"]
                ):
                    return None
                if (
                    row["last_attempt"]
                    and current < datetime.fromisoformat(row["last_attempt"]) + COOLDOWN
                ):
                    return None
            if not re.fullmatch(r"E(?:0[1-9]|[1-4][0-9]|50)", asset):
                raise ValueError("Invalid emote asset")
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO ledger.emote_submissions VALUES(?,?,?,'sending',?)",
                (ticket.source, ticket.scope, asset, current.isoformat()),
            ).rowcount
            if not inserted:
                return None
            self.db.execute(
                "INSERT INTO ledger.emote_preferences VALUES(?,0,0,?,NULL) ON CONFLICT(scope) DO UPDATE SET last_attempt=excluded.last_attempt",
                (ticket.scope, current.isoformat()),
            )
            return ImageAttempt(
                ticket.source,
                ticket.scope,
                asset,
                ticket.revision,
                row["revision"] if row else 0,
            )

    def permitted(self, attempt, *, manual=False):
        row = self.db.execute(
            "SELECT enabled,revision FROM ledger.emote_preferences WHERE scope=?",
            (attempt.scope,),
        ).fetchone()
        return bool(
            row
            and (manual or row[0])
            and row[1] == attempt.preference_revision
            and self.memory.begin(attempt.scope, "check").revision
            == attempt.memory_revision
            and self.db.execute(
                "SELECT enabled FROM scopes WHERE scope=?", (attempt.scope,)
            ).fetchone()[0]
            and self.db.execute(
                "SELECT 1 FROM ledger.emote_submissions WHERE scope=? AND source=? AND asset=? AND state='sending'",
                (attempt.scope, attempt.source, attempt.asset),
            ).fetchone()
        )

    def finish(self, attempt, state, current):
        if state not in ("adapter_returned", "unknown", "cancelled"):
            raise ValueError("Image adapter return is not device delivery evidence")
        with self.db:
            self.db.execute(
                "UPDATE ledger.emote_submissions SET state=? WHERE scope=? AND source=? AND state='sending'",
                (state, attempt.scope, attempt.source),
            )
            if state == "unknown":
                self.db.execute(
                    "UPDATE ledger.emote_preferences SET blocked_until=? WHERE scope=?",
                    ((current + FAILURE_PAUSE).isoformat(), attempt.scope),
                )


async def send_qq_image_once(event, path, permitted):
    """Pinned botpy request budget: start at 2, reset recursion at 3 exits
    before network. No AstrBot retry or proactive fallback is used here.
    Only the original signed C2C event's sender and message ID are eligible.
    """
    import botpy
    from botpy.http import Route

    source = event.message_obj.raw_message
    sender = event.get_sender_id()
    if (
        not isinstance(source, botpy.message.C2CMessage)
        or event.get_group_id()
        or not sender
        or source.author.user_openid != sender
        or str(source.id) != str(event.message_obj.message_id)
    ):
        raise ValueError("Original QQ owner C2C event required")
    request = event.bot.api._http.request
    if not permitted():
        raise ValueError("Image authorization revoked before upload")
    media = await request(
        Route("POST", "/v2/users/{openid}/files", openid=sender),
        retry_time=2,
        json={
            "file_type": 1,
            "file_data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "srv_send_msg": False,
        },
    )
    if (
        not isinstance(media, dict)
        or not isinstance(media.get("file_info"), str)
        or not 0 < len(media["file_info"]) <= 16384
    ):
        raise ValueError("QQ image upload did not return a validated media receipt")
    if not permitted():
        raise ValueError("Image authorization revoked before message submission")
    result = await request(
        Route("POST", "/v2/users/{openid}/messages", openid=sender),
        retry_time=2,
        # Pinned preceding text uses seq 1..10000; keep this image's reply tuple distinct.
        json={
            "msg_id": str(source.id),
            "msg_seq": 10001,
            "msg_type": 7,
            "media": {"file_info": media["file_info"]},
        },
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise ValueError("QQ image message result unknown; no resubmission")
