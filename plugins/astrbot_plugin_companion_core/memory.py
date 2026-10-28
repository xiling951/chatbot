"""Scoped SQLite memory; trusted messages only, with independent deletion replay."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .binding import BindingStore, LEDGER_SCHEMA
from .emotes import EmoteStore, LEDGER_SCHEMA as EMOTE_LEDGER_SCHEMA


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Ticket:
    scope: str
    source: str
    revision: int


class MemoryStore:
    def __init__(self, path: Path, ledger: Path):
        self.path, self.ledger = Path(path), Path(ledger)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # An existing memory database must never silently get a new deletion ledger.
        if self.path.exists() and not self.ledger.exists():
            raise RuntimeError("Latest deletion ledger missing; recovery refused")
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 5:
            self.db.close()
            raise RuntimeError("Unsupported newer database schema; do not downgrade")
        self.db.executescript(Path(__file__).with_name("schema.sql").read_text())
        self.db.execute("ATTACH DATABASE ? AS ledger", (str(self.ledger),))
        self.db.executescript("""
            PRAGMA synchronous=FULL;
            PRAGMA ledger.synchronous=FULL;
            CREATE TABLE IF NOT EXISTS ledger.meta(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS ledger.revisions(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger.deleted(id TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger.blocked(scope TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(scope,source));
            CREATE TABLE IF NOT EXISTS ledger.policies(scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger.automatic(scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger.proactive(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger.submissions(idempotency_key TEXT PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL, local_day TEXT NOT NULL, submitted_at TEXT NOT NULL, attempts INTEGER NOT NULL, retry_at TEXT);
            CREATE TABLE IF NOT EXISTS ledger.discovery_days(day TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL, at TEXT NOT NULL);
        """)
        self.db.executescript(LEDGER_SCHEMA)
        self.db.executescript(EMOTE_LEDGER_SCHEMA)
        self.binding = BindingStore(self)
        self.emotes = EmoteStore(self)
        row = self.db.execute("SELECT id FROM ledger.meta").fetchone()
        ledger_id = row[0] if row else uuid.uuid4().hex
        old = self.db.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
        if old and old[0] != ledger_id:
            self.db.close()
            raise RuntimeError("Deletion ledger identity mismatch")
        with self.db:
            if not row:
                self.db.execute("INSERT INTO ledger.meta VALUES(?)", (ledger_id,))
            self.db.execute(
                "INSERT OR IGNORE INTO meta VALUES('ledger_id',?)", (ledger_id,)
            )
            self.db.execute(
                "DELETE FROM facts WHERE id IN (SELECT id FROM ledger.deleted)"
            )
            self.db.execute(
                "UPDATE scopes SET enabled=(SELECT enabled FROM ledger.policies WHERE ledger.policies.scope=scopes.scope) WHERE scope IN (SELECT scope FROM ledger.policies)"
            )
            self.db.execute(
                "DELETE FROM turns WHERE revision != COALESCE((SELECT revision FROM ledger.revisions WHERE ledger.revisions.scope=turns.scope),0)"
            )
            for table in ("emotions", "emotion_events"):
                self.db.execute(
                    f"DELETE FROM {table} WHERE revision != COALESCE((SELECT revision FROM ledger.revisions WHERE ledger.revisions.scope={table}.scope),0)"
                )
            self.db.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM ledger.deleted)"
            )
            self.db.execute(
                "DELETE FROM candidates WHERE id IN (SELECT id FROM ledger.deleted) OR revision != COALESCE((SELECT revision FROM ledger.revisions WHERE ledger.revisions.scope=candidates.scope),0) OR source IN (SELECT source FROM ledger.blocked WHERE ledger.blocked.scope=candidates.scope)"
            )
            self.db.execute(
                "INSERT OR IGNORE INTO memory_preferences(scope) SELECT scope FROM scopes"
            )
            self.db.execute(
                "UPDATE memory_preferences SET automatic=(SELECT enabled FROM ledger.automatic WHERE ledger.automatic.scope=memory_preferences.scope) WHERE scope IN (SELECT scope FROM ledger.automatic)"
            )
            for table in ("tasks", "outbox"):
                waiting = "('pending','running')" if table == "tasks" else "('queued')"
                self.db.execute(
                    f"UPDATE {table} SET state=CASE WHEN state IN {waiting} THEN 'cancelled' ELSE state END,content='' WHERE memory_revision != COALESCE((SELECT revision FROM ledger.revisions WHERE ledger.revisions.scope={table}.scope),0) OR auth_revision != COALESCE((SELECT revision FROM ledger.proactive WHERE ledger.proactive.scope={table}.scope),-1)"
                )
            self.db.execute(
                "UPDATE outbox SET state=(SELECT state FROM ledger.submissions WHERE ledger.submissions.idempotency_key=outbox.idempotency_key),reason='authoritative_receipt_replayed' WHERE idempotency_key IN (SELECT idempotency_key FROM ledger.submissions WHERE state IN ('accepted_by_platform','unknown'))"
            )
            self.db.execute(
                "UPDATE tasks SET state='cancelled',content='' WHERE id IN (SELECT id FROM ledger.deleted)"
            )
            self.db.execute(
                "UPDATE executions SET state='cancelled',reason='task_cancelled' WHERE task_id IN (SELECT id FROM ledger.deleted) AND state IN ('pending','running')"
            )
            self.db.execute(
                "UPDATE outbox SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,content='' WHERE execution_id IN (SELECT id FROM executions WHERE task_id IN (SELECT id FROM ledger.deleted))"
            )

    def close(self):
        self.db.close()

    def scope(
        self,
        platform: str,
        bot: str,
        sender: str,
        visibility: str,
        persona="companion-test",
    ) -> str:
        if not all((platform, bot, sender, visibility, persona)):
            raise ValueError("Incomplete trusted identity")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO identities VALUES(?,?,?,?)",
                (platform, bot, sender, uuid.uuid4().hex),
            )
            person = self.db.execute(
                "SELECT person FROM identities WHERE platform=? AND bot=? AND sender=?",
                (platform, bot, sender),
            ).fetchone()[0]
            scope = digest(persona, person, visibility)
            self.db.execute("INSERT OR IGNORE INTO scopes(scope) VALUES(?)", (scope,))
            self.db.execute(
                "INSERT OR IGNORE INTO memory_preferences(scope) VALUES(?)", (scope,)
            )
            self.db.execute(
                "INSERT OR IGNORE INTO scope_metadata VALUES(?,?,?,?)",
                (scope, persona, person, visibility),
            )
        return scope

    def begin(self, scope: str, source: str) -> Ticket:
        revision = self.db.execute(
            "SELECT revision FROM ledger.revisions WHERE scope=?", (scope,)
        ).fetchone()
        return Ticket(scope, digest(source), revision[0] if revision else 0)

    def put(
        self,
        ticket: Ticket,
        predicate: str,
        value: str,
        *,
        correction=False,
        source_type="user_explicit",
    ) -> str:
        predicate, value = self._validate_fact(predicate, value)
        if source_type != "user_explicit":
            raise ValueError("Only explicit user facts are accepted in this MVP")
        with self.db:
            self._check_ticket(ticket)
            return self._write_fact(ticket, predicate, value, correction=correction)

    @staticmethod
    def _validate_fact(predicate: str, value: str) -> tuple[str, str]:
        predicate, value = predicate.strip(), value.strip()
        if not predicate or len(predicate) > 50 or not value or len(value) > 300:
            raise ValueError("Invalid fact length")
        if re.search(
            r"密码|密钥|身份证|银行卡|手机号|电话号码|token|api.?key|\d{7,}|[\w.+-]+@[\w.-]+\.[a-z]{2,}",
            predicate + value,
            re.I,
        ):
            raise ValueError("Sensitive credential/identifier memory is disabled")
        return predicate, value

    def _check_ticket(self, ticket: Ticket):
        # Obtain the SQLite write lock before checking permission/generation.
        self.db.execute(
            "UPDATE scopes SET enabled=enabled WHERE scope=?", (ticket.scope,)
        )
        enabled = self.db.execute(
            "SELECT enabled FROM scopes WHERE scope=?", (ticket.scope,)
        ).fetchone()
        blocked = self.db.execute(
            "SELECT 1 FROM ledger.blocked WHERE scope=? AND source=?",
            (ticket.scope, ticket.source),
        ).fetchone()
        if (
            not enabled
            or not enabled[0]
            or self.begin(ticket.scope, "unused").revision != ticket.revision
            or blocked
        ):
            raise ValueError("Memory disabled or stale/deleted source")

    def _write_fact(
        self, ticket: Ticket, predicate: str, value: str, *, correction: bool
    ) -> str:
        old = self.db.execute(
            "SELECT * FROM facts WHERE scope=? AND predicate=? AND status='active'",
            (ticket.scope, predicate),
        ).fetchone()
        if old and old["value"] == value:
            return old["id"]
        if old and not correction:
            raise ValueError("Conflicting fact; use /更正 属性=值 explicitly")
        if old:
            self._purge(ticket.scope, predicate)
        elif any(
            predicate in json.loads(link["predicates"])
            for link in self.binding.links(ticket.scope)
        ):
            # A new local fact may shadow a previously shared value in history.
            self._invalidate_pending(ticket.scope)
        fact_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                fact_id,
                ticket.scope,
                predicate,
                value,
                ticket.source,
                "user_explicit",
                old["version"] + 1 if old else 1,
                "active",
                old["id"] if old else None,
                now(),
                now(),
            ),
        )
        return fact_id

    def automatic_enabled(self, scope: str) -> bool:
        row = self.db.execute(
            "SELECT s.enabled,p.automatic FROM scopes s JOIN memory_preferences p ON s.scope=p.scope WHERE s.scope=?",
            (scope,),
        ).fetchone()
        return bool(row and row[0] and row[1])

    def set_automatic(self, scope: str, enabled: bool):
        with self.db:
            self.db.execute(
                "UPDATE memory_preferences SET automatic=? WHERE scope=?",
                (int(enabled), scope),
            )
            self.db.execute(
                "INSERT INTO ledger.automatic VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled",
                (scope, int(enabled)),
            )
            self._invalidate_pending(scope)

    def apply_statement(
        self, ticket: Ticket, predicate: str, value: str, *, correction=False
    ) -> tuple[str, str]:
        """Save a grounded statement or retain an unresolved conflict, never overwrite by recency."""
        predicate, value = self._validate_fact(predicate, value)
        with self.db:
            self._check_ticket(ticket)
            if not self.automatic_enabled(ticket.scope):
                raise ValueError("Automatic memory is disabled")
            old = self.db.execute(
                "SELECT id,value FROM facts WHERE scope=? AND predicate=? AND status='active'",
                (ticket.scope, predicate),
            ).fetchone()
            if not old or old["value"] == value or correction:
                return "saved", self._write_fact(
                    ticket, predicate, value, correction=correction
                )
            # One outstanding value per key keeps confirmation understandable and bounded.
            for row in self.db.execute(
                "SELECT id,source FROM candidates WHERE scope=? AND predicate=?",
                (ticket.scope, predicate),
            ).fetchall():
                self.db.execute(
                    "INSERT OR IGNORE INTO ledger.deleted VALUES(?,?,?,?)",
                    (row["id"], ticket.scope, row["source"], now()),
                )
            self.db.execute(
                "DELETE FROM candidates WHERE scope=? AND predicate=?",
                (ticket.scope, predicate),
            )
            candidate_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    ticket.scope,
                    predicate,
                    value,
                    ticket.source,
                    ticket.revision,
                    old["id"],
                    now(),
                ),
            )
            return "pending", candidate_id

    def candidates(self, scope: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM candidates WHERE scope=? ORDER BY created_at", (scope,)
            )
        ]

    def confirm(self, scope: str, candidate_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{12,32}", candidate_id):
            raise ValueError("请提供待确认条目的 12–32 位编号。")
        with self.db:
            self.db.execute("UPDATE scopes SET enabled=enabled WHERE scope=?", (scope,))
            rows = self.db.execute(
                "SELECT * FROM candidates WHERE scope=? AND id LIKE ?",
                (scope, candidate_id + "%"),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError("待确认条目不存在或编号不唯一。")
            row = rows[0]
            ticket = Ticket(scope, row["source"], row["revision"])
            self._check_ticket(ticket)
            if (
                not self.automatic_enabled(scope)
                or not self.db.execute(
                    "SELECT 1 FROM facts WHERE scope=? AND predicate=? AND id=?",
                    (scope, row["predicate"], row["base_id"]),
                ).fetchone()
            ):
                raise ValueError("待确认条目已失效，请重新提供事实。")
            self.db.execute("DELETE FROM candidates WHERE id=?", (row["id"],))
            return self._write_fact(
                ticket, row["predicate"], row["value"], correction=True
            )

    def _invalidate_pending(self, scope: str, _visited=None):
        visited = set() if _visited is None else _visited
        if scope in visited:
            return
        visited.add(scope)
        for link in self.binding.links(scope):
            peer = link["scope_b"] if scope == link["scope_a"] else link["scope_a"]
            self._invalidate_pending(peer, visited)
        rows = self.db.execute(
            "SELECT id,source FROM candidates WHERE scope=?", (scope,)
        ).fetchall()
        for row in rows:
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.deleted VALUES(?,?,?,?)",
                (row["id"], scope, row["source"], now()),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.blocked VALUES(?,?)",
                (scope, row["source"]),
            )
        self.db.execute("DELETE FROM candidates WHERE scope=?", (scope,))
        self.db.execute(
            "INSERT INTO ledger.revisions VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET revision=revision+1",
            (scope,),
        )
        self.db.execute("DELETE FROM turns WHERE scope=?", (scope,))
        for row in self.db.execute(
            "SELECT id,source FROM emotion_events WHERE scope=?", (scope,)
        ).fetchall():
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.deleted VALUES(?,?,?,?)",
                (row["id"], scope, row["source"], now()),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.blocked VALUES(?,?)",
                (scope, row["source"]),
            )
        self.db.execute("DELETE FROM emotion_events WHERE scope=?", (scope,))
        self.db.execute("DELETE FROM emotions WHERE scope=?", (scope,))
        for row in self.db.execute(
            "SELECT id,source FROM tasks WHERE scope=?", (scope,)
        ).fetchall():
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.deleted VALUES(?,?,?,?)",
                (row["id"], scope, row["source"], now()),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.blocked VALUES(?,?)",
                (scope, row["source"]),
            )
        self.db.execute(
            "UPDATE tasks SET state=CASE WHEN state IN ('pending','running') THEN 'cancelled' ELSE state END,content='' WHERE scope=?",
            (scope,),
        )
        self.db.execute(
            "UPDATE executions SET state='cancelled',reason='memory_changed' WHERE scope=? AND state IN ('pending','running')",
            (scope,),
        )
        self.db.execute(
            "UPDATE outbox SET content='',state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,reason='memory_changed' WHERE scope=?",
            (scope,),
        )

    def record_event(self, ticket: Ticket, kind: str, content: str) -> str:
        if kind not in ("user_reported", "shared_plan"):
            raise ValueError(
                "只有本人陈述的事件或共同计划可记录；不能把 AI 自述当作已核实活动。"
            )
        _, content = self._validate_fact("共同事件", content)
        with self.db:
            self._check_ticket(ticket)
            event_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    ticket.scope,
                    kind,
                    content,
                    ticket.source,
                    "user_explicit",
                    now(),
                    now(),
                ),
            )
        return event_id

    def events(self, scope: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM events WHERE scope=? ORDER BY created_at DESC", (scope,)
            )
        ]

    def reserve_discovery(self, ticket: Ticket, current: datetime):
        # One actual attempt per Beijing day for this single-instance project.
        # The latest ledger, not a restorable cache, owns the daily limit.
        day = current.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with self.db:
            self._check_ticket(ticket)
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO ledger.discovery_days VALUES(?,?,?,'requested','',?)",
                (day, ticket.scope, ticket.source, current.isoformat()),
            ).rowcount
            if not inserted:
                raise ValueError(
                    "北京时间今天已进行过一次B站读取尝试；失败也不自动重试，避免反复请求。"
                )
        return day

    def finish_discovery(self, ticket: Ticket, day: str, metadata=None, *, error=""):
        from .bilibili import VideoMetadata

        with self.db:
            row = self.db.execute(
                "SELECT state,scope,source FROM ledger.discovery_days WHERE day=?",
                (day,),
            ).fetchone()
            if not row or tuple(row) != ("requested", ticket.scope, ticket.source):
                raise ValueError("没有匹配的本次读取尝试，不能记录活动。")
            self._check_ticket(ticket)
            if metadata is not None:
                if not isinstance(metadata, VideoMetadata) or error:
                    raise ValueError("只有已校验的公开元数据可作为外部读取记录。")
                content = json.dumps(metadata.data(), ensure_ascii=False)
                if len(content.encode()) > 2400:
                    raise ValueError("External metadata exceeds storage bound")
                self.db.execute(
                    "INSERT INTO events VALUES(?,?, 'public_metadata',?,?, 'external_metadata',?,?)",
                    (
                        uuid.uuid4().hex,
                        ticket.scope,
                        content,
                        ticket.source,
                        metadata.fetched_at,
                        now(),
                    ),
                )
            elif not re.fullmatch(r"[a-z0-9_]{1,80}", error):
                raise ValueError("读取失败必须有受限失败类别；不能伪造成功活动。")
            self.db.execute(
                "UPDATE ledger.discovery_days SET state=?,result=? WHERE day=?",
                (
                    "fetched" if metadata else "failed",
                    "metadata_only" if metadata else error,
                    day,
                ),
            )

    def relationship(self, scope: str) -> list[dict]:
        enabled = self.db.execute(
            "SELECT enabled FROM scopes WHERE scope=?", (scope,)
        ).fetchone()
        if not enabled or not enabled[0]:
            return []
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM relationships WHERE scope=? ORDER BY updated_at DESC",
                (scope,),
            )
        ]

    def _purge(self, scope: str, predicate: str | None):
        # Called within the same cross-database transaction as the mutation.
        rows = list(
            self.db.execute(
                "SELECT id,source FROM facts WHERE scope=? AND (? IS NULL OR predicate=?)",
                (scope, predicate, predicate),
            ).fetchall()
        )
        if predicate is None:
            rows += self.db.execute(
                "SELECT id,source FROM events WHERE scope=?", (scope,)
            ).fetchall()
            self.db.execute("DELETE FROM events WHERE scope=?", (scope,))
        elif predicate == "共同事件":
            rows += self.db.execute(
                "SELECT id,source FROM events WHERE scope=? AND kind IN ('user_reported','shared_plan')",
                (scope,),
            ).fetchall()
            self.db.execute(
                "DELETE FROM events WHERE scope=? AND kind IN ('user_reported','shared_plan')",
                (scope,),
            )
        elif predicate == "B站活动":
            rows += self.db.execute(
                "SELECT id,source FROM events WHERE scope=? AND kind='public_metadata'",
                (scope,),
            ).fetchall()
            self.db.execute(
                "DELETE FROM events WHERE scope=? AND kind='public_metadata'", (scope,)
            )
        self._invalidate_pending(scope)
        for row in rows:
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.deleted VALUES(?,?,?,?)",
                (row["id"], scope, row["source"], now()),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.blocked VALUES(?,?)",
                (scope, row["source"]),
            )
        self.db.execute(
            "DELETE FROM facts WHERE scope=? AND (? IS NULL OR predicate=?)",
            (scope, predicate, predicate),
        )
        self.db.execute("DELETE FROM turns WHERE scope=?", (scope,))

    def forget(self, scope: str, predicate: str | None = None):
        with self.db:
            self._purge(scope, predicate)
            self.binding.forget(scope, predicate)

    def set_enabled(self, scope: str, enabled: bool):
        with self.db:
            self.db.execute(
                "UPDATE scopes SET enabled=? WHERE scope=?", (int(enabled), scope)
            )
            self.db.execute(
                "INSERT INTO ledger.policies VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled",
                (scope, int(enabled)),
            )
            self._invalidate_pending(scope)
            if not enabled:
                self.binding.forget(scope)

    def history(self, scope: str, conversation: str, budget=4000) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self.db:
            self.db.execute("DELETE FROM turns WHERE created_at < ?", (cutoff,))
        rows = self.db.execute(
            "SELECT user_text,assistant_text FROM turns WHERE scope=? AND conversation=? ORDER BY id DESC LIMIT 3",
            (scope, digest(conversation)),
        ).fetchall()
        result, used = [], 0
        for row in rows:
            size = len((row["user_text"] + row["assistant_text"]).encode("utf-8"))
            if used + size > budget:
                break
            result[0:0] = [
                {"role": "user", "content": row["user_text"]},
                {"role": "assistant", "content": row["assistant_text"]},
            ]
            used += size
        return result

    def append_turn(
        self, ticket: Ticket, conversation: str, user_text: str, assistant_text: str
    ):
        with self.db:
            self.db.execute(
                "UPDATE scopes SET enabled=enabled WHERE scope=?", (ticket.scope,)
            )
            if self.begin(ticket.scope, "unused").revision != ticket.revision:
                raise ValueError("Stale reply cannot persist after deletion")
            if not self.db.execute(
                "SELECT enabled FROM scopes WHERE scope=?", (ticket.scope,)
            ).fetchone()[0]:
                return
            self.db.execute(
                "INSERT INTO turns(scope,conversation,source,user_text,assistant_text,revision,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    ticket.scope,
                    digest(conversation),
                    ticket.source,
                    user_text,
                    assistant_text,
                    ticket.revision,
                    now(),
                ),
            )
            self.db.execute(
                "DELETE FROM turns WHERE scope=? AND conversation=? AND id NOT IN (SELECT id FROM turns WHERE scope=? AND conversation=? ORDER BY id DESC LIMIT 3)",
                (
                    ticket.scope,
                    digest(conversation),
                    ticket.scope,
                    digest(conversation),
                ),
            )

    def facts(self, scope: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM facts WHERE scope=? AND status='active' ORDER BY updated_at DESC",
                (scope,),
            )
        ]

    def recall(self, scope: str, query: str, budget=1200, *, shared=()) -> list[dict]:
        if not self.db.execute(
            "SELECT enabled FROM scopes WHERE scope=?", (scope,)
        ).fetchone()[0]:
            return []
        aliases = {
            "音乐": ["歌", "曲风", "摇滚", "爵士", "听什么"],
            "饮料": ["喝", "咖啡", "茶"],
            "生日": ["出生", "生辰"],
            "称呼": ["叫我", "名字", "姓名"],
        }
        normalized = query.lower()
        terms = set(re.findall(r"[a-z0-9]+", normalized))
        for phrase in re.findall(r"[\u4e00-\u9fff]+", normalized):
            terms.update(phrase[i : i + 2] for i in range(max(1, len(phrase) - 1)))
        topics = {
            key
            for key, words in aliases.items()
            if key in query or any(w in query for w in words)
        }
        ranked = []
        for fact in [*self.facts(scope), *shared]:
            text = fact["predicate"] + fact["value"]
            # Structured keys and mapped topics must outrank incidental word
            # overlap, especially when the byte budget fits only two facts.
            score = 30 * (fact["predicate"].lower() in normalized)
            score += 40 * (fact["predicate"] in topics)
            score += 3 * sum(term in text.lower() for term in terms)
            if score:
                ranked.append((score, fact))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        result, used = [], 0
        for _, fact in ranked[:8]:
            # Conservative UTF-8 byte budget, rather than claiming a measured token count.
            size = len(json.dumps(fact, ensure_ascii=False).encode())
            if used + size <= budget:
                result.append(fact)
                used += size
        return result

    def claim(
        self, platform: str, bot: str, conversation: str, message_id: str
    ) -> bool:
        if not message_id:
            raise ValueError("Stable platform message ID required")
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO receipts VALUES(?,'claimed',?)",
                (digest(platform, bot, conversation, message_id), now()),
            )
        return cursor.rowcount == 1

    def backup(self, destination: Path):
        if Path(destination).exists():
            raise ValueError(
                "Backup requires a fresh path; existing snapshots are preserved"
            )
        with closing(sqlite3.connect(destination)) as target:
            self.db.backup(target)

    @classmethod
    def restore(cls, snapshot: Path, destination: Path, latest_ledger: Path):
        if Path(destination).exists() or not Path(latest_ledger).exists():
            raise ValueError(
                "Restore requires a fresh destination and the latest ledger"
            )
        with (
            closing(
                sqlite3.connect(f"file:{Path(snapshot).as_posix()}?mode=ro", uri=True)
            ) as source,
            closing(sqlite3.connect(destination)) as target,
        ):
            source.backup(target)
        return cls(destination, latest_ledger)
