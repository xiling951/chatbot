"""Explicit, revocable private-chat sharing; identities and facts stay separate."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger.binding_codes(
 hash TEXT PRIMARY KEY, source_scope TEXT NOT NULL, target_platform TEXT NOT NULL,
 target_bot TEXT NOT NULL, target_sender TEXT NOT NULL, predicates TEXT NOT NULL,
 expires_at TEXT NOT NULL, source_revision INTEGER NOT NULL, preview_scope TEXT,
 state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger.bindings(
 id TEXT PRIMARY KEY, scope_a TEXT NOT NULL, scope_b TEXT NOT NULL,
 predicates TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger.binding_audit(
 id TEXT PRIMARY KEY, link_id TEXT NOT NULL, scope TEXT NOT NULL,
 action TEXT NOT NULL, predicates TEXT NOT NULL, at TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Endpoint:
    platform: str
    bot: str
    sender: str
    channel: str


class BindingStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def identity(self, scope):
        return self.db.execute(
            "SELECT m.*,i.platform,i.bot,i.sender,s.enabled FROM scope_metadata m "
            "JOIN identities i ON m.person=i.person JOIN scopes s ON s.scope=m.scope WHERE m.scope=?",
            (scope,),
        ).fetchone()

    def trusted(self, scope, endpoints):
        row = self.identity(scope)
        return bool(
            row
            and row["visibility"] == "private"
            and row["enabled"]
            and any(
                (row["platform"], row["bot"], row["sender"])
                == (e.platform, e.bot, e.sender)
                for e in endpoints
            )
        )

    def audit(self, link_id, scope, action, predicates):
        self.db.execute(
            "INSERT INTO ledger.binding_audit VALUES(?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                link_id,
                scope,
                action,
                json.dumps(predicates, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def reconcile(self, endpoints):
        """A removed owner/account or missing metadata revokes, never transfers, a grant."""
        with self.db:
            for link in self.db.execute(
                "SELECT * FROM ledger.bindings WHERE state='active'"
            ).fetchall():
                a, b = self.identity(link["scope_a"]), self.identity(link["scope_b"])
                if not (
                    self.trusted(link["scope_a"], endpoints)
                    and self.trusted(link["scope_b"], endpoints)
                    and a["persona"] == b["persona"]
                ):
                    self._revoke(link, link["scope_a"], "identity_removed")
            for code in self.db.execute(
                "SELECT * FROM ledger.binding_codes WHERE state='pending'"
            ).fetchall():
                if not self.trusted(code["source_scope"], endpoints) or not any(
                    (e.platform, e.bot, e.sender)
                    == (
                        code["target_platform"],
                        code["target_bot"],
                        code["target_sender"],
                    )
                    for e in endpoints
                ):
                    self.db.execute(
                        "UPDATE ledger.binding_codes SET state='revoked' WHERE hash=?",
                        (code["hash"],),
                    )

    def create(self, scope, channel, predicates, endpoints, current):
        if not self.trusted(scope, endpoints):
            raise ValueError("绑定仅限已核实且启用记忆的本人私聊。")
        keys = sorted(set(p.strip() for p in predicates))
        if (
            not keys
            or len(keys) > 10
            or any(not re.fullmatch(r"[\w\u4e00-\u9fff]{1,50}", k) for k in keys)
        ):
            raise ValueError(
                "请明确列出1至10个记忆属性，用逗号分隔；不支持全部或通配符。"
            )
        for key in keys:
            if key in ("全部", "共同事件", "互动边界", "称呼边界"):
                raise ValueError(
                    "不能共享全部、事件或互动边界；请逐项指定普通事实属性。"
                )
            self.store._validate_fact(key, "共享范围")
        source = self.identity(scope)
        targets = [
            e
            for e in endpoints
            if e.channel == channel and e.platform != source["platform"]
        ]
        if len(targets) != 1:
            raise ValueError(
                "目标渠道必须恰有一个已核实的本人机器人单聊；当前不能绑定。"
            )
        target = targets[0]
        code = secrets.token_hex(12).upper()
        with self.db:
            self.db.execute(
                "UPDATE ledger.binding_codes SET state='revoked' WHERE source_scope=? AND state='pending'",
                (scope,),
            )
            self.db.execute(
                "INSERT INTO ledger.binding_codes VALUES(?,?,?,?,?,?,?,?,NULL,'pending',?)",
                (
                    hashlib.sha256(code.encode()).hexdigest(),
                    scope,
                    target.platform,
                    target.bot,
                    target.sender,
                    json.dumps(keys, ensure_ascii=False),
                    (current + timedelta(minutes=10)).isoformat(),
                    self.store.begin(scope, "binding").revision,
                    current.isoformat(),
                ),
            )
            self.audit("", scope, "code_issued", keys)
        return code, keys

    def _code(self, code, scope, endpoints, current):
        if not re.fullmatch(r"[A-Fa-f0-9]{24}", code):
            raise ValueError("绑定码无效、过期或不属于这两个本人私聊。")
        row = self.db.execute(
            "SELECT * FROM ledger.binding_codes WHERE hash=?",
            (hashlib.sha256(code.upper().encode()).hexdigest(),),
        ).fetchone()
        target = self.identity(scope)
        source = self.identity(row["source_scope"]) if row else None
        if not (
            row
            and row["state"] == "pending"
            and current < datetime.fromisoformat(row["expires_at"])
            and self.trusted(scope, endpoints)
            and self.trusted(row["source_scope"], endpoints)
            and (target["platform"], target["bot"], target["sender"])
            == (row["target_platform"], row["target_bot"], row["target_sender"])
            and source["persona"] == target["persona"]
            and source["platform"] != target["platform"]
            and self.store.begin(row["source_scope"], "binding").revision
            == row["source_revision"]
        ):
            raise ValueError("绑定码无效、过期或不属于这两个本人私聊。")
        return row

    def preview(self, scope, code, endpoints, current):
        with self.db:
            self.db.execute("UPDATE scopes SET enabled=enabled WHERE scope=?", (scope,))
            row = self._code(code, scope, endpoints, current)
            self.db.execute(
                "UPDATE ledger.binding_codes SET preview_scope=? WHERE hash=?",
                (scope, row["hash"]),
            )
            return json.loads(row["predicates"])

    def accept(self, scope, code, endpoints, current):
        with self.db:
            self.db.execute("UPDATE scopes SET enabled=enabled WHERE scope=?", (scope,))
            row = self._code(code, scope, endpoints, current)
            if row["preview_scope"] != scope:
                raise ValueError(
                    "请先用 /绑定查看 绑定码 核对双向共享范围，再明确接受。"
                )
            self.db.execute(
                "UPDATE ledger.binding_codes SET state='used' WHERE hash=?",
                (row["hash"],),
            )
            # Rebinding replaces the entire range; it cannot silently widen an old grant.
            for old in self.links(scope):
                if row["source_scope"] in (old["scope_a"], old["scope_b"]):
                    self._revoke(old, scope, "replaced")
            identifier = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO ledger.bindings VALUES(?,?,?,?,'active',?,?)",
                (
                    identifier,
                    row["source_scope"],
                    scope,
                    row["predicates"],
                    current.isoformat(),
                    current.isoformat(),
                ),
            )
            self.store._invalidate_pending(scope)
            self.audit(
                identifier,
                scope,
                "accepted_both_private_chats",
                json.loads(row["predicates"]),
            )
            return identifier

    def links(self, scope):
        return self.db.execute(
            "SELECT * FROM ledger.bindings WHERE state='active' AND (scope_a=? OR scope_b=?) ORDER BY created_at",
            (scope, scope),
        ).fetchall()

    def _revoke(self, link, actor, action):
        # Clear both ends while the edge still exists, within the ledger transaction.
        self.store._invalidate_pending(link["scope_a"])
        self.db.execute(
            "UPDATE ledger.bindings SET state='revoked',updated_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), link["id"]),
        )
        self.audit(link["id"], actor, action, json.loads(link["predicates"]))

    def unlink(self, scope, identifier):
        with self.db:
            matches = [r for r in self.links(scope) if r["id"] == identifier]
            if len(matches) != 1:
                raise ValueError("未找到本会话的有效绑定；请先查看 /绑定状态。")
            self._revoke(matches[0], scope, "unlinked")

    def forget(self, scope, predicate=None):
        """Deletion on either end also withdraws sharing for that key, without deleting the peer's original fact."""
        self.db.execute(
            "UPDATE ledger.binding_codes SET state='revoked' WHERE state='pending' AND (source_scope=? OR preview_scope=?)",
            (scope, scope),
        )
        identity = self.identity(scope)
        if identity:
            self.db.execute(
                "UPDATE ledger.binding_codes SET state='revoked' WHERE state='pending' AND target_platform=? AND target_bot=? AND target_sender=?",
                (identity["platform"], identity["bot"], identity["sender"]),
            )
        for link in self.links(scope):
            keys = json.loads(link["predicates"])
            if predicate is None:
                self._revoke(link, scope, "memory_stopped_or_forgotten")
            elif predicate in keys:
                self.store._invalidate_pending(scope)
                keys.remove(predicate)
                self.db.execute(
                    "UPDATE ledger.bindings SET predicates=?,state=?,updated_at=? WHERE id=?",
                    (
                        json.dumps(keys, ensure_ascii=False),
                        "active" if keys else "revoked",
                        datetime.now(timezone.utc).isoformat(),
                        link["id"],
                    ),
                )
                self.audit(link["id"], scope, "predicate_withdrawn", [predicate])

    def shared_facts(self, scope, endpoints):
        self.reconcile(endpoints)
        if not self.trusted(scope, endpoints):
            return []
        local_keys = {f["predicate"] for f in self.store.facts(scope)}
        grouped = {}
        for link in self.links(scope):
            peer = link["scope_b"] if scope == link["scope_a"] else link["scope_a"]
            allowed = set(json.loads(link["predicates"])) - local_keys
            info = self.identity(peer)
            channel = next(
                e.channel
                for e in endpoints
                if (e.platform, e.bot, e.sender)
                == (info["platform"], info["bot"], info["sender"])
            )
            for fact in self.store.facts(peer):
                if (
                    fact["predicate"] in allowed
                    and fact["source_type"] == "user_explicit"
                ):
                    item = dict(fact, shared_from=channel, binding_id=link["id"])
                    grouped.setdefault(fact["predicate"], []).append(item)
        # Ambiguous remote values are not injected. No inference or chained sharing.
        return [
            items[0]
            for items in grouped.values()
            if len({f["value"] for f in items}) == 1
        ]


def handle_binding_command(text, binding, scope, endpoints, current, stopped=False):
    if not text.startswith(("/绑定", "/解绑")):
        return None
    if text == "/绑定帮助":
        return "两端记忆保持独立；仅双向共享明确列出的普通事实。步骤：/绑定发起 QQ 音乐,称呼（或目标微信）→在目标本人单聊 /绑定查看 绑定码 →核对后 /绑定接受 绑定码。码10分钟单次有效。/绑定状态 查看范围及编号；/解绑 编号 停止未来共享并清理两端活跃上下文。删除共享属性也会撤回该属性的共享；不删除另一端原始事实。"
    if text == "/绑定状态":
        binding.reconcile(endpoints)
        return (
            "\n".join(
                f"{r['id']} 双向共享：{','.join(json.loads(r['predicates']))}"
                for r in binding.links(scope)
            )
            or "当前没有有效绑定。"
        )
    if text.startswith("/解绑 "):
        binding.unlink(scope, text.split(" ", 1)[1].strip())
        return "已解绑，停止未来共享并清理两端相关活跃上下文；原始记忆各自保留。历史显示、旧备份或模型服务数据不在此次清理范围内。"
    if stopped:
        raise ValueError("停止开关已开启，不能建立或查看待建立的共享；仍可解绑。")
    binding.reconcile(endpoints)
    if text.startswith("/绑定发起 "):
        parts = text.split(maxsplit=2)
        if len(parts) != 3:
            raise ValueError("格式：/绑定发起 QQ 音乐,称呼；目标可选QQ或微信。")
        code, keys = binding.create(
            scope, parts[1], re.split("[,，]", parts[2]), endpoints, current
        )
        return f"待确认双向共享：{','.join(keys)}。绑定码 {code}（10分钟有效、只能使用一次）。请在目标本人单聊发送 /绑定查看 {code}；查看后还须 /绑定接受 {code}。尚未共享记忆。"
    if text.startswith("/绑定查看 "):
        code = text.split(" ", 1)[1].strip()
        keys = binding.preview(scope, code, endpoints, current)
        return f"拟双向共享的普通事实属性：{','.join(keys)}。本端同名事实优先；不共享事件、情绪、历史或主动发送权限，不合并身份。确认后发送 /绑定接受 {code}；不接受则等待10分钟失效。"
    if text.startswith("/绑定接受 "):
        identifier = binding.accept(
            scope, text.split(" ", 1)[1].strip(), endpoints, current
        )
        return f"两个本人私聊已确认指定范围共享；绑定编号 {identifier}。身份与原始记忆未合并，可用 /解绑 编号 撤销。"
    raise ValueError("请发送 /绑定帮助 查看绑定和解绑步骤。")
