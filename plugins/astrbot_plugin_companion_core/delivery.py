"""Persistent proactive execution/outbox core; no network calls or implicit consent.

All transitions use MemoryStore's attached, authoritative ledger transaction.
Sending/unknown/accepted attempts retain quota. Only a proven rejection permits
bounded retry. Receipt and quota history survive restoring an older main DB.
"""

import json
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .memory import MemoryStore, Ticket, digest, now
from .scheduling import due_slot, in_quiet_hours, utc


def stamp(value: datetime) -> str:
    return utc(value).isoformat()


def instant(value: str) -> datetime:
    return utc(datetime.fromisoformat(value))


class DeliveryStore:
    LEASE_SECONDS = 60
    RETRY_SECONDS = 30
    MAX_ATTEMPTS = 2

    def __init__(self, memory: MemoryStore, policy: dict | None = None):
        self.memory, self.db = memory, memory.db
        policy = policy or {}
        for name, key, default in (
            ("LEASE_SECONDS", "lease_seconds", 60),
            ("RETRY_SECONDS", "safe_retry_base_seconds", 30),
            ("MAX_ATTEMPTS", "safe_retry_max_attempts", 2),
            ("UNANSWERED_HOURS", "unanswered_cooldown_hours", 24),
        ):
            value = policy.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Invalid messaging policy")
            setattr(self, name, value)
        if self.LEASE_SECONDS < 60 or self.MAX_ATTEMPTS > 3:
            raise ValueError(
                "Lease must cover request deadlines; safe retry must remain bounded"
            )

    def _lock(self, scope: str):
        self.db.execute("UPDATE scopes SET enabled=enabled WHERE scope=?", (scope,))

    def authorize(
        self,
        scope: str,
        target: str,
        enabled: bool,
        current: datetime,
        *,
        zone="America/Tijuana",
        maximum=2,
        quiet_start=23,
        quiet_end=8,
    ) -> int:
        """Call only from a trusted owner action, never a model/tool argument."""
        ZoneInfo(zone)
        in_quiet_hours(current, zone, quiet_start, quiet_end)
        if (
            not target
            or len(target) > 1000
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= 2
        ):
            raise ValueError("Invalid trusted target or daily maximum")
        with self.db:
            self._lock(scope)
            if not self.db.execute(
                "SELECT 1 FROM scopes WHERE scope=?", (scope,)
            ).fetchone():
                raise ValueError("Unknown trusted scope")
            self.db.execute(
                "INSERT INTO ledger.proactive VALUES(?,1,?) ON CONFLICT(scope) DO UPDATE SET revision=revision+1,enabled=excluded.enabled",
                (scope, int(enabled)),
            )
            revision = self.db.execute(
                "SELECT revision FROM ledger.proactive WHERE scope=?", (scope,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO proactive_policies VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET target=excluded.target,timezone=excluded.timezone,maximum_per_day=excluded.maximum_per_day,quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,auth_revision=excluded.auth_revision",
                (
                    scope,
                    target,
                    zone,
                    maximum,
                    quiet_start,
                    quiet_end,
                    revision,
                    stamp(current),
                ),
            )
            self._cancel(scope, "authorization_changed")
        return revision

    def _cancel(self, scope: str, reason: str):
        self.db.execute(
            "UPDATE tasks SET state='cancelled',content='' WHERE scope=? AND state IN ('pending','running')",
            (scope,),
        )
        self.db.execute(
            "UPDATE executions SET state='cancelled',reason=? WHERE scope=? AND state IN ('pending','running')",
            (reason, scope),
        )
        # An already submitted network request cannot be recalled here.
        self.db.execute(
            "UPDATE outbox SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,reason=? WHERE scope=? AND state IN ('queued','sending')",
            (reason, scope),
        )

    def stop_all(self, current: datetime):
        for row in self.db.execute(
            "SELECT p.* FROM proactive_policies p JOIN ledger.proactive a ON p.scope=a.scope WHERE a.enabled=1"
        ).fetchall():
            self.authorize(
                row["scope"],
                row["target"],
                False,
                current,
                zone=row["timezone"],
                maximum=row["maximum_per_day"],
                quiet_start=row["quiet_start"],
                quiet_end=row["quiet_end"],
            )

    def record_response(self, scope: str, current: datetime):
        with self.db:
            self.db.execute(
                "UPDATE proactive_policies SET last_response_at=? WHERE scope=?",
                (stamp(current), scope),
            )

    def _valid(self, row) -> bool:
        source = row["source"] if "source" in row.keys() else None
        if "execution_id" in row.keys():
            parent = self.db.execute(
                "SELECT source FROM executions WHERE id=?", (row["execution_id"],)
            ).fetchone()
            source = parent[0] if parent else None
        if (
            source
            and self.db.execute(
                "SELECT 1 FROM ledger.blocked WHERE scope=? AND source=?",
                (row["scope"], source),
            ).fetchone()
        ):
            return False
        authorization = self.db.execute(
            "SELECT revision,enabled FROM ledger.proactive WHERE scope=?",
            (row["scope"],),
        ).fetchone()
        enabled = self.db.execute(
            "SELECT enabled FROM scopes WHERE scope=?", (row["scope"],)
        ).fetchone()
        return bool(
            authorization
            and authorization["enabled"]
            and enabled
            and enabled[0]
            and authorization["revision"] == row["auth_revision"]
            and self.memory.begin(row["scope"], "revision-check").revision
            == row["memory_revision"]
        )

    def create(
        self,
        ticket: Ticket,
        content: str,
        next_run: datetime,
        expires: datetime,
        current: datetime,
        *,
        key: str,
        rule: dict | None = None,
        grace_seconds=300,
    ) -> str:
        rule = rule or {"kind": "once"}
        next_run, expires, current = utc(next_run), utc(expires), utc(current)
        if (
            not key
            or len(key) > 200
            or not content.strip()
            or len(content.encode("utf-8")) > 2000
            or not next_run <= expires
            or isinstance(grace_seconds, bool)
            or not isinstance(grace_seconds, int)
            or not 1 <= grace_seconds <= 86400
        ):
            raise ValueError("Invalid task content, interval or lifetime")
        with self.db:
            self.memory._check_ticket(ticket)
            policy = self.db.execute(
                "SELECT * FROM proactive_policies WHERE scope=?", (ticket.scope,)
            ).fetchone()
            if not policy or not self._valid(
                {
                    "scope": ticket.scope,
                    "auth_revision": policy["auth_revision"],
                    "memory_revision": ticket.revision,
                }
            ):
                raise ValueError("Proactive authorization is missing or revoked")
            due_slot(next_run, next_run, rule, policy["timezone"])
            prior = self.db.execute(
                "SELECT * FROM tasks WHERE scope=? AND idempotency_key=?",
                (ticket.scope, digest(key)),
            ).fetchone()
            if prior:
                if (
                    prior["content"] != content
                    or prior["rule"] != json.dumps(rule, sort_keys=True)
                    or prior["next_run_at"] != stamp(next_run)
                ):
                    raise ValueError(
                        "Idempotency key already belongs to another task/state"
                    )
                return prior["id"]
            task_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    ticket.scope,
                    ticket.source,
                    digest(key),
                    json.dumps(rule, sort_keys=True),
                    policy["timezone"],
                    stamp(next_run),
                    stamp(expires),
                    grace_seconds,
                    policy["auth_revision"],
                    ticket.revision,
                    content,
                    "pending",
                    stamp(current),
                ),
            )
        return task_id

    def begin_due(self, current: datetime) -> list[dict]:
        """Claim executions atomically; generation is separate from delivery."""
        current = utc(current)
        claimed = []
        with self.db:
            self.db.execute("UPDATE tasks SET state=state WHERE 0")
            tasks = self.db.execute(
                "SELECT * FROM tasks WHERE state='pending' AND next_run_at<=? ORDER BY next_run_at LIMIT 20",
                (stamp(current),),
            ).fetchall()
            for task in tasks:
                if not self._valid(task):
                    self.db.execute(
                        "UPDATE tasks SET state='cancelled',content='' WHERE id=?",
                        (task["id"],),
                    )
                    continue
                latest, following = due_slot(
                    instant(task["next_run_at"]),
                    current,
                    json.loads(task["rule"]),
                    task["timezone"],
                )
                if latest is None:
                    continue
                deadline = min(
                    instant(task["expires_at"]),
                    latest + timedelta(seconds=task["grace_seconds"]),
                )
                expired = current > deadline
                following = (
                    following
                    if following and following <= instant(task["expires_at"])
                    else None
                )
                self.db.execute(
                    "UPDATE tasks SET next_run_at=?,state=? WHERE id=?",
                    (
                        stamp(following) if following else None,
                        "pending"
                        if following
                        else ("expired" if expired else "succeeded"),
                        task["id"],
                    ),
                )
                execution_id = digest(task["id"], stamp(latest))
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        execution_id,
                        task["id"],
                        task["scope"],
                        task["source"],
                        stamp(latest),
                        stamp(deadline),
                        task["auth_revision"],
                        task["memory_revision"],
                        "expired" if expired else "running",
                        "expired_latest_slot" if expired else "latest_due_slot",
                        stamp(current + timedelta(seconds=self.LEASE_SECONDS)),
                        stamp(current),
                    ),
                )
                if cursor.rowcount and not expired:
                    execution = dict(
                        self.db.execute(
                            "SELECT * FROM executions WHERE id=?", (execution_id,)
                        ).fetchone()
                    )
                    execution["content"] = task["content"]
                    claimed.append(execution)
        return claimed

    def complete_generation(
        self, execution_id: str, content: str, current: datetime
    ) -> str | None:
        if not content.strip() or len(content.encode("utf-8")) > 2000:
            raise ValueError("Invalid generated content")
        current = utc(current)
        with self.db:
            self.db.execute(
                "UPDATE executions SET state=state WHERE id=?", (execution_id,)
            )
            execution = self.db.execute(
                "SELECT * FROM executions WHERE id=?", (execution_id,)
            ).fetchone()
            if not execution or execution["state"] != "running":
                return None
            if (
                not self._valid(execution)
                or current > instant(execution["expires_at"])
                or current > instant(execution["lease_until"])
            ):
                self.db.execute(
                    "UPDATE executions SET state='cancelled',reason='late_or_revoked_generation' WHERE id=?",
                    (execution_id,),
                )
                return None
            policy = self.db.execute(
                "SELECT * FROM proactive_policies WHERE scope=?", (execution["scope"],)
            ).fetchone()
            outbox_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO outbox(id,execution_id,scope,idempotency_key,target,content,auth_revision,memory_revision,expires_at,state,lease_until,not_before,reason) VALUES(?,?,?,?,?,?,?,?,?,'queued',NULL,?,'generation_completed')",
                (
                    outbox_id,
                    execution_id,
                    execution["scope"],
                    execution_id,
                    policy["target"],
                    content,
                    execution["auth_revision"],
                    execution["memory_revision"],
                    execution["expires_at"],
                    stamp(current),
                ),
            )
            self.db.execute(
                "UPDATE executions SET state='succeeded',reason='candidate_persisted' WHERE id=?",
                (execution_id,),
            )
        return outbox_id

    def queued(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM outbox WHERE state='queued' ORDER BY not_before LIMIT 20"
            )
        ]

    def fail_generation(self, execution_id: str):
        with self.db:
            self.db.execute(
                "UPDATE executions SET state='failed',reason='generation_failed' WHERE id=? AND state='running'",
                (execution_id,),
            )

    def cancel_task(self, scope: str, task_prefix: str) -> bool:
        if not re.fullmatch(r"[0-9a-f]{12,32}", task_prefix):
            raise ValueError("请提供 12–32 位提醒编号。")
        with self.db:
            self._lock(scope)
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE scope=? AND id LIKE ?",
                (scope, task_prefix + "%"),
            ).fetchall()
            if len(rows) != 1:
                return False
            row = rows[0]
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.deleted VALUES(?,?,?,?)",
                (row["id"], scope, row["source"], now()),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO ledger.blocked VALUES(?,?)",
                (scope, row["source"]),
            )
            self.db.execute(
                "UPDATE tasks SET state='cancelled',content='' WHERE id=?", (row["id"],)
            )
            self.db.execute(
                "UPDATE executions SET state='cancelled',reason='task_cancelled' WHERE task_id=? AND state IN ('pending','running')",
                (row["id"],),
            )
            self.db.execute(
                "UPDATE outbox SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,reason='task_cancelled',content='' WHERE execution_id IN (SELECT id FROM executions WHERE task_id=?)",
                (row["id"],),
            )
            return True

    def claim_send(
        self, outbox_id: str, current: datetime, *, capability: bool
    ) -> dict | None:
        current = utc(current)
        with self.db:
            self.db.execute("UPDATE outbox SET state=state WHERE id=?", (outbox_id,))
            row = self.db.execute(
                "SELECT * FROM outbox WHERE id=?", (outbox_id,)
            ).fetchone()
            if not row or row["state"] != "queued":
                return None
            if not self._valid(row):
                self.db.execute(
                    "UPDATE outbox SET state='cancelled',content='',reason='revoked_or_memory_changed' WHERE id=?",
                    (outbox_id,),
                )
                return None
            if current > instant(row["expires_at"]):
                self.db.execute(
                    "UPDATE outbox SET state='expired',reason='candidate_expired' WHERE id=?",
                    (outbox_id,),
                )
                return None
            policy = self.db.execute(
                "SELECT * FROM proactive_policies WHERE scope=?", (row["scope"],)
            ).fetchone()
            receipt = self.db.execute(
                "SELECT * FROM ledger.submissions WHERE idempotency_key=?",
                (row["idempotency_key"],),
            ).fetchone()
            if receipt and receipt["state"] in (
                "sending",
                "accepted_by_platform",
                "unknown",
            ):
                self.db.execute(
                    "UPDATE outbox SET state=?,reason=? WHERE id=?",
                    (
                        "unknown"
                        if receipt["state"] == "sending"
                        else receipt["state"],
                        "authoritative_submission_exists",
                        outbox_id,
                    ),
                )
                return None
            if receipt and receipt["attempts"] >= self.MAX_ATTEMPTS:
                self.db.execute(
                    "UPDATE outbox SET state='failed',reason='safe_retry_exhausted' WHERE id=?",
                    (outbox_id,),
                )
                return None
            if current < instant(row["not_before"]) or (
                receipt
                and receipt["retry_at"]
                and current < instant(receipt["retry_at"])
            ):
                return None
            reason = None
            if not capability:
                reason = "channel_not_verified"
            elif in_quiet_hours(
                current, policy["timezone"], policy["quiet_start"], policy["quiet_end"]
            ):
                reason = "quiet_hours"
            day = current.astimezone(ZoneInfo(policy["timezone"])).date().isoformat()
            reserved = self.db.execute(
                "SELECT count(*) FROM ledger.submissions WHERE scope=? AND local_day=? AND state IN ('sending','accepted_by_platform','unknown')",
                (row["scope"], day),
            ).fetchone()[0]
            if not reason and reserved >= policy["maximum_per_day"]:
                reason = "local_daily_quota"
            last = self.db.execute(
                "SELECT max(submitted_at) FROM ledger.submissions WHERE scope=? AND state IN ('sending','accepted_by_platform','unknown')",
                (row["scope"],),
            ).fetchone()[0]
            if (
                not reason
                and last
                and last > policy["last_response_at"]
                and current - instant(last) < timedelta(hours=self.UNANSWERED_HOURS)
            ):
                reason = "unanswered_cooldown"
            if reason:
                self.db.execute(
                    "UPDATE outbox SET reason=? WHERE id=?", (reason, outbox_id)
                )
                return None
            self.db.execute(
                "INSERT INTO ledger.submissions VALUES(?,?,'sending',?,?,1,NULL) ON CONFLICT(idempotency_key) DO UPDATE SET state='sending',local_day=excluded.local_day,submitted_at=excluded.submitted_at,attempts=attempts+1,retry_at=NULL",
                (row["idempotency_key"], row["scope"], day, stamp(current)),
            )
            self.db.execute(
                "UPDATE outbox SET state='sending',lease_until=?,reason='submission_reserved' WHERE id=?",
                (stamp(current + timedelta(seconds=self.LEASE_SECONDS)), outbox_id),
            )
            return dict(
                self.db.execute(
                    "SELECT * FROM outbox WHERE id=?", (outbox_id,)
                ).fetchone()
            )

    def finish_send(self, outbox_id: str, outcome: str, current: datetime):
        """outcome='rejected' requires proof no platform submission was accepted."""
        if outcome not in ("accepted_by_platform", "unknown", "rejected"):
            raise ValueError("Unknown transport outcome")
        current = utc(current)
        with self.db:
            self.db.execute("UPDATE outbox SET state=state WHERE id=?", (outbox_id,))
            row = self.db.execute(
                "SELECT * FROM outbox WHERE id=?", (outbox_id,)
            ).fetchone()
            if not row or row["state"] != "sending":
                return
            receipt = self.db.execute(
                "SELECT * FROM ledger.submissions WHERE idempotency_key=?",
                (row["idempotency_key"],),
            ).fetchone()
            retry_at = None
            if outcome == "rejected":
                state = (
                    "queued"
                    if self._valid(row)
                    and receipt["attempts"] < self.MAX_ATTEMPTS
                    and current < instant(row["expires_at"])
                    else "failed"
                )
                retry_at = stamp(
                    current
                    + timedelta(
                        seconds=self.RETRY_SECONDS * 2 ** (receipt["attempts"] - 1)
                    )
                )
                journal_state = "failed"
            else:
                state = journal_state = outcome
            self.db.execute(
                "UPDATE ledger.submissions SET state=?,retry_at=? WHERE idempotency_key=?",
                (journal_state, retry_at, row["idempotency_key"]),
            )
            reason = "definitely_not_accepted" if outcome == "rejected" else outcome
            if outcome == "accepted_by_platform" and not self._valid(row):
                reason = "accepted_after_revocation_or_memory_change"
            self.db.execute(
                "UPDATE outbox SET state=?,not_before=?,lease_until=NULL,reason=? WHERE id=?",
                (
                    state,
                    retry_at or stamp(current),
                    reason,
                    outbox_id,
                ),
            )

    def recover(self, current: datetime):
        current = utc(current)
        with self.db:
            self.db.execute(
                "UPDATE executions SET state='failed',reason='generation_lease_expired' WHERE state='running' AND lease_until<?",
                (stamp(current),),
            )
            self.db.execute(
                "UPDATE outbox SET state='unknown',reason='submission_lease_expired' WHERE state='sending' AND lease_until<?",
                (stamp(current),),
            )
            self.db.execute(
                "UPDATE ledger.submissions SET state='unknown' WHERE state='sending' AND submitted_at<?",
                (stamp(current - timedelta(seconds=self.LEASE_SECONDS)),),
            )
