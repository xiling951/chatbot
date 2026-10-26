"""Consistent backup and quarantine restore; never replace a live database."""

import argparse
import sys
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
    store = MemoryStore(home / "memory.sqlite", home / "deletions.sqlite")
    try:
        store.backup(args.destination)
        print(
            "Consistent memory backup created. Keep the authoritative deletion ledger separately current."
        )
    finally:
        store.close()
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
            for table in ("facts", "events", "candidates")
        )
        if integrity != "ok" or remaining_deleted:
            raise SystemExit("Quarantine validation failed; do not activate")
        print(
            "Quarantine restore integrity and deletion replay passed. Not activated for chat."
        )
    finally:
        store.close()
