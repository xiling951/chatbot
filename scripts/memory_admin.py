"""Consistent backup and quarantine restore; never replace a live database."""

import argparse
import sys
import sqlite3
from contextlib import closing
from pathlib import Path
from local_paths import PLUGIN_DATA

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.astrbot_plugin_companion_core.memory import MemoryStore

home = PLUGIN_DATA
parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="action", required=True)
backup = sub.add_parser("backup")
backup.add_argument("destination", type=Path)
restore = sub.add_parser("restore")
restore.add_argument("snapshot", type=Path)
restore.add_argument("destination", type=Path)
args = parser.parse_args()
if args.action == "backup":
    if args.destination.exists() or not (home / "deletions.sqlite").exists():
        raise SystemExit("Backup requires a fresh destination and the current ledger")
    # Never initialize the development MemoryStore just to back up a live older
    # plugin: doing so would migrate its DB before the running code is upgraded.
    with (
        closing(
            sqlite3.connect(
                f"file:{(home / 'memory.sqlite').as_posix()}?mode=ro", uri=True
            )
        ) as source,
        closing(sqlite3.connect(args.destination)) as target,
    ):
        source.backup(target)
        print(
            "Consistent memory backup created. Keep the authoritative deletion ledger separately current."
        )
else:
    if args.destination.resolve() in (
        (home / "memory.sqlite").resolve(),
        (home / "deletions.sqlite").resolve(),
    ):
        raise SystemExit(
            "Restore must target a new quarantine file, never the live database"
        )
    store = MemoryStore.restore(
        args.snapshot, args.destination, home / "deletions.sqlite"
    )
    try:
        integrity = store.db.execute("PRAGMA integrity_check").fetchone()[0]
        remaining_deleted = sum(
            store.db.execute(
                f"SELECT count(*) FROM {table} WHERE id IN (SELECT id FROM ledger.deleted)"
            ).fetchone()[0]
            for table in ("facts", "events", "candidates", "emotion_events")
        )
        unsafe_queue = store.db.execute(
            "SELECT count(*) FROM outbox o WHERE state='queued' AND (memory_revision != COALESCE((SELECT revision FROM ledger.revisions r WHERE r.scope=o.scope),0) OR auth_revision != COALESCE((SELECT revision FROM ledger.proactive a WHERE a.scope=o.scope),-1) OR idempotency_key IN (SELECT idempotency_key FROM ledger.submissions WHERE state IN ('sending','accepted_by_platform','unknown')))"
        ).fetchone()[0]
        if integrity != "ok" or remaining_deleted or unsafe_queue:
            raise SystemExit("Quarantine validation failed; do not activate")
        print(
            "Quarantine restore integrity and deletion replay passed. Not activated for chat."
        )
    finally:
        store.close()
