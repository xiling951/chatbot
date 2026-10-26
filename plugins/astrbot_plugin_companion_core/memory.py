"""SQLite memory. No model-generated identities or automatic history extraction."""

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
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 2:
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
        """)
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
        predicate, value = predicate.strip(), value.strip()
        if source_type != "user_explicit":
            raise ValueError("Only explicit user facts are accepted in this MVP")
        if not predicate or len(predicate) > 50 or not value or len(value) > 300:
            raise ValueError("Invalid fact length")
        if re.search(
            r"密码|密钥|身份证|银行卡|token|api.?key", predicate + value, re.I
        ):
            raise ValueError("Sensitive credential/identifier memory is disabled")
        with self.db:
            self.db.execute(
                "UPDATE scopes SET enabled=enabled WHERE scope=?", (ticket.scope,)
            )
            current = self.begin(ticket.scope, "unused").revision
            enabled = self.db.execute(
                "SELECT enabled FROM scopes WHERE scope=?", (ticket.scope,)
            ).fetchone()
            blocked = self.db.execute(
                "SELECT 1 FROM ledger.blocked WHERE scope=? AND source=?",
                (ticket.scope, ticket.source),
            ).fetchone()
            if not enabled or not enabled[0] or current != ticket.revision or blocked:
                raise ValueError("Memory disabled or stale/deleted source")
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
            fact_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    ticket.scope,
                    predicate,
                    value,
                    ticket.source,
                    source_type,
                    old["version"] + 1 if old else 1,
                    "active",
                    old["id"] if old else None,
                    now(),
                    now(),
                ),
            )
        return fact_id

    def _purge(self, scope: str, predicate: str | None):
        # Called within the same cross-database transaction as the mutation.
        rows = self.db.execute(
            "SELECT id,source FROM facts WHERE scope=? AND (? IS NULL OR predicate=?)",
            (scope, predicate, predicate),
        ).fetchall()
        self.db.execute(
            "INSERT INTO ledger.revisions VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET revision=revision+1",
            (scope,),
        )
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

    def set_enabled(self, scope: str, enabled: bool):
        with self.db:
            self.db.execute(
                "UPDATE scopes SET enabled=? WHERE scope=?", (int(enabled), scope)
            )
            self.db.execute(
                "INSERT INTO ledger.policies VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled",
                (scope, int(enabled)),
            )
            self.db.execute(
                "INSERT INTO ledger.revisions VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET revision=revision+1",
                (scope,),
            )
            self.db.execute("DELETE FROM turns WHERE scope=?", (scope,))

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

    def recall(self, scope: str, query: str, budget=1200) -> list[dict]:
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
        for fact in self.facts(scope):
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
        if Path(destination).resolve() in (self.path.resolve(), self.ledger.resolve()):
            raise ValueError("Backup must use a different path")
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
