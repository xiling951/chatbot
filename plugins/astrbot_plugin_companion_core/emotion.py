"""Small explainable simulated style state, independent of facts and permission."""

import math
import re
import uuid
from datetime import datetime

from .delivery import instant, stamp
from .memory import MemoryStore, Ticket


class EmotionState:
    def __init__(self, memory: MemoryStore, policy: dict | None = None):
        self.memory, self.db = memory, memory.db
        self.policy = policy or {}
        self.baseline = [
            float(self.policy.get(k, v))
            for k, v in [
                ("baseline_valence", 0),
                ("baseline_arousal", 0.3),
                ("baseline_social_energy", 0.7),
            ]
        ]
        self.half_life = float(self.policy.get("half_life_hours", 6))
        if (
            not math.isfinite(self.half_life)
            or self.half_life <= 0
            or not all(math.isfinite(v) for v in self.baseline)
            or not -1 <= self.baseline[0] <= 1
            or not all(0 <= v <= 1 for v in self.baseline[1:])
        ):
            raise ValueError("Invalid emotion policy")

    def read(self, scope: str, current: datetime) -> dict | None:
        enabled = self.db.execute(
            "SELECT enabled FROM scopes WHERE scope=?", (scope,)
        ).fetchone()
        if not self.policy.get("enabled", True) or not enabled or not enabled[0]:
            return None
        row = self.db.execute(
            "SELECT * FROM emotions WHERE scope=?", (scope,)
        ).fetchone()
        if not row:
            return None
        if row["revision"] != self.memory.begin(scope, "revision").revision:
            return None
        hours = max(
            0,
            (instant(stamp(current)) - instant(row["updated_at"])).total_seconds()
            / 3600,
        )
        decay = math.exp(-math.log(2) * hours / self.half_life)
        values = [
            base + (row[key] - base) * decay
            for key, base in zip(("valence", "arousal", "social_energy"), self.baseline)
        ]
        return dict(zip(("valence", "arousal", "social_energy"), values))

    def observe(self, ticket: Ticket, text: str, current: datetime) -> str | None:
        if not self.policy.get("enabled", True):
            return None
        if re.search(r'假设|如果|角色扮演|他说|她说|[“”"「」『』?？]', text):
            return None
        plain = text.strip().rstrip("。！!")
        patterns = [
            (
                r"我(?:今天|现在)?(?:有点累|很累|很难过|有点难过|有点烦躁)",
                "supportive",
                (-0.15, -0.10, -0.05),
            ),
            (
                r"我(?:今天|现在)?(?:很开心|好开心|很高兴)",
                "celebrate",
                (0.30, 0.10, 0.10),
            ),
            (r"(?:谢谢你|谢谢你听我说|很高兴认识你)", "warm", (0.15, -0.05, 0.05)),
            (r"我想安静(?:一会儿|会儿)", "quiet", (0, -0.25, -0.15)),
        ]
        for pattern, reason, deltas in patterns:
            if re.fullmatch(pattern, plain):
                break
        else:
            return None
        with self.db:
            self.memory._check_ticket(ticket)
            if self.db.execute(
                "SELECT 1 FROM emotion_events WHERE scope=? AND source=?",
                (ticket.scope, ticket.source),
            ).fetchone():
                return None
            values = self.read(ticket.scope, current) or dict(
                zip(("valence", "arousal", "social_energy"), self.baseline)
            )
            current_values = [
                values[key] for key in ("valence", "arousal", "social_energy")
            ]
            changed = [
                max(-1 if i == 0 else 0, min(1, value + delta))
                for i, (value, delta) in enumerate(zip(current_values, deltas))
            ]
            previous = self.db.execute(
                "SELECT updated_at FROM emotions WHERE scope=?", (ticket.scope,)
            ).fetchone()
            updated_at = max(
                stamp(current), previous[0] if previous else stamp(current)
            )
            self.db.execute(
                "INSERT INTO emotions VALUES(?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET valence=excluded.valence,arousal=excluded.arousal,social_energy=excluded.social_energy,updated_at=excluded.updated_at,revision=excluded.revision",
                (ticket.scope, *changed, updated_at, ticket.revision),
            )
            self.db.execute(
                "INSERT INTO emotion_events VALUES(?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    ticket.scope,
                    ticket.source,
                    reason,
                    ticket.revision,
                    stamp(current),
                ),
            )
        return reason

    def style(self, scope: str, current: datetime) -> str:
        values = self.read(scope, current)
        if not values:
            return ""
        if values["arousal"] < 0.15:
            tone = "安静、简短，尊重对方想独处的边界"
        elif values["valence"] < -0.05:
            tone = "平静陪伴，承接感受，少建议、少追问"
        elif values["valence"] > 0.1:
            tone = "温暖轻快，适度分享喜悦"
        else:
            tone = "自然平和"
        return (
            "模拟互动风格："
            + tone
            + "。这是程序模拟的语气状态，不是现实感受或用户事实；不作心理诊断，不改变身份、事实、授权或发送规则，不以情绪要求用户回应。"
        )
