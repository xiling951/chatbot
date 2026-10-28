"""Persist shutdown revocation without upgrading an older running database."""

import sqlite3
from contextlib import closing
from pathlib import Path

from local_paths import PLUGIN_DATA


def revoke_pending(database: Path, ledger: Path) -> tuple[int, int]:
    if not database.exists():
        return 0, 0
    with closing(
        sqlite3.connect(f"file:{database.as_posix()}?mode=rw", uri=True, timeout=5)
    ) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < 4:
            return 0, 0
        if version not in (4, 5) or not ledger.exists():
            raise RuntimeError("Compatible schema and latest proactive ledger required")
        db.execute("ATTACH DATABASE ? AS ledger", (str(ledger),))
        db.executescript(
            "PRAGMA secure_delete=ON; PRAGMA synchronous=FULL; PRAGMA ledger.synchronous=FULL;"
        )
        with db:
            revoked = db.execute(
                "UPDATE ledger.proactive SET revision=revision+1,enabled=0 WHERE enabled=1"
            ).rowcount
            in_flight = db.execute(
                "SELECT count(*) FROM outbox WHERE state='sending'"
            ).fetchone()[0]
            db.execute(
                "UPDATE tasks SET state='cancelled',content='' WHERE state IN ('pending','running')"
            )
            db.execute(
                "UPDATE executions SET state='cancelled',reason='global_stop' WHERE state IN ('pending','running')"
            )
            db.execute(
                "UPDATE outbox SET state='cancelled',content='',reason='global_stop' WHERE state='queued'"
            )
            db.execute(
                "UPDATE outbox SET reason='global_stop_in_flight' WHERE state='sending'"
            )
        return revoked, in_flight


if __name__ == "__main__":
    revoked, in_flight = revoke_pending(
        PLUGIN_DATA / "memory.sqlite", PLUGIN_DATA / "deletions.sqlite"
    )
    print(
        f"Proactive shutdown revocation persisted: {revoked} scopes; {in_flight} submissions may already be in flight."
    )
