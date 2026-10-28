"""User-bound encrypted local snapshots; quarantine verification never activates."""

import argparse
import ctypes
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
import zipfile
from contextlib import closing
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filelock import FileLock
from local_paths import DATA, PLUGIN_DATA, PROJECT_ROOT

HOST_SCRIPTS = (
    "start.ps1",
    "stop.ps1",
    "recover_owned.ps1",
    "supervise_runtime.ps1",
    "runtime_bootstrap.py",
    "qq_transport.py",
    "runtime_watchdog.py",
    "local_paths.py",
    "stop_proactive.py",
    "local_backup.py",
    "availability_report.py",
)

BACKUP_ROOT = Path("F:/chatbot-backups")
ENTROPY = b"CompanionCore local snapshot v1"
NAME = re.compile(r"companion-(\d{8}T\d{6})-[0-9a-f]{32}\.dpapi")


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows user-bound DPAPI is required")
    source_buffer = ctypes.create_string_buffer(value)
    entropy_buffer = ctypes.create_string_buffer(ENTROPY)
    source = Blob(
        len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    entropy = Blob(
        len(ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        description = None
    else:
        function = crypt.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(Blob),
            wintypes.LPCWSTR,
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        description = "CompanionCore private snapshot"
    function.restype = wintypes.BOOL
    # UI forbidden, current user scope. Never use LOCAL_MACHINE (all users).
    if not function(
        ctypes.byref(source),
        description,
        ctypes.byref(entropy),
        None,
        None,
        1,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, wintypes.HLOCAL))


def sqlite_bytes(source, *, name="main"):
    deadline = time.monotonic() + 20

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("Snapshot deadline exceeded")

    with closing(sqlite3.connect(":memory:")) as destination:
        source.backup(destination, name=name, pages=256, progress=progress, sleep=0.05)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Snapshot integrity failed")
        return destination.serialize()


def memory_pair(home):
    with closing(
        sqlite3.connect(
            (home / "memory.sqlite").as_uri() + "?mode=ro", uri=True, timeout=5
        )
    ) as source:
        source.execute(
            "ATTACH DATABASE ? AS ledger",
            ((home / "deletions.sqlite").as_uri() + "?mode=ro",),
        )
        source.execute("BEGIN")
        # Shared read locks on BOTH attached databases hold one snapshot while
        # the app's main+ledger write transactions wait. No live single-file copy.
        main_id = source.execute(
            "SELECT value FROM meta WHERE key='ledger_id'"
        ).fetchone()[0]
        ledger_id = source.execute("SELECT id FROM ledger.meta").fetchone()[0]
        if main_id != ledger_id:
            raise RuntimeError("Ledger identity mismatch")
        result = {
            "memory.sqlite": sqlite_bytes(source),
            "deletions.sqlite": sqlite_bytes(source, name="ledger"),
        }
        source.rollback()
        return result, main_id


def collect(data=DATA, home=PLUGIN_DATA):
    pair, ledger_id = memory_pair(home)
    files = {
        "plugin_data/companion_core/" + name: content for name, content in pair.items()
    }
    required = [
        data / "cmd_config.json",
        data / "admin.local.json",
        home / "settings.local.json",
        home / "allowance.local.json",
        home / "weixin-bound.local.json",
    ]
    optional = [data / "mcp_server.json", home / "qq-bound.local.json"]
    for path in required + optional:
        if not path.is_file():
            if path in required:
                raise FileNotFoundError(path.name)
            continue
        content = path.read_bytes()
        json.loads(content.decode("utf-8-sig"))
        files[path.relative_to(data).as_posix()] = content
    root_config = PROJECT_ROOT / "config" / "runtime_root.local.json"
    if root_config.exists():
        content = root_config.read_bytes()
        json.loads(content)
        files["deployment/runtime_root.local.json"] = content
    files.update(collect_host_scripts(PROJECT_ROOT))
    plugin = data / "plugins" / "astrbot_plugin_companion_core"
    for path in plugin.iterdir():
        if (
            path.is_file()
            and not path.is_symlink()
            and path.suffix in {".py", ".sql", ".yaml", ".txt"}
        ):
            files[path.relative_to(data).as_posix()] = path.read_bytes()
    for name, content in collect_emote_assets(plugin).items():
        files[(plugin.relative_to(data) / name).as_posix()] = content
    framework = data / "data_v4.db"
    if framework.exists():
        with closing(
            sqlite3.connect(framework.as_uri() + "?mode=ro", uri=True, timeout=5)
        ) as source:
            files["data_v4.db"] = sqlite_bytes(source)
    return files, ledger_id


def collect_emote_assets(plugin):
    # Only the static, named project asset set; never recurse into chat caches.
    root = plugin / "assets" / "emotes"
    if not root.exists():
        return {}
    if (
        root.is_symlink()
        or (plugin / "assets").is_symlink()
        or root.resolve().parent != plugin.resolve() / "assets"
    ):
        raise ValueError("Emote asset root escaped plugin material")
    result = {}
    for path in root.iterdir():
        if path.name != "manifest.json" and not re.fullmatch(
            r"E(?:0[1-9]|[1-4][0-9]|50)\.(?:svg|png)", path.name
        ):
            continue
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != root.resolve()
            or path.stat().st_size > 128 * 1024
        ):
            raise ValueError("Invalid static asset in backup")
        result[path.relative_to(plugin).as_posix()] = path.read_bytes()
    if "assets/emotes/manifest.json" not in result:
        raise ValueError("Static emote manifest missing from backup")
    return result


def collect_host_scripts(project):
    result = {}
    scripts = project / "scripts"
    for name in HOST_SCRIPTS:
        source = scripts / name
        if (
            source.is_symlink()
            or not source.is_file()
            or source.resolve().parent != scripts.resolve()
        ):
            raise ValueError("Required controlled startup script missing or redirected")
        result["deployment/scripts/" + name] = source.read_bytes()
    return result


def encode(files, ledger_id, current):
    manifest = {
        "format": 1,
        "created_at": current.isoformat(),
        "ledger_id": ledger_id,
        "activation": "never automatic; current deletion/permission ledger required",
        "files": {
            name: hashlib.sha256(value).hexdigest() for name, value in files.items()
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", json.dumps(manifest))
    return dpapi(output.getvalue())


def decode(payload):
    with zipfile.ZipFile(io.BytesIO(dpapi(payload, decrypt=True))) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entry")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != 1 or set(names) != set(manifest["files"]) | {
            "manifest.json"
        }:
            raise ValueError("Unexpected archive contents")
        files = {}
        for name, expected in manifest["files"].items():
            if (
                name.startswith(("/", "\\"))
                or "\\" in name
                or ":" in name
                or ".." in Path(name).parts
            ):
                raise ValueError("Unsafe archive path")
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Snapshot digest mismatch")
            files[name] = content
        return files, manifest


def snapshot(current=None):
    current = current or datetime.now(timezone.utc)
    BACKUP_ROOT.mkdir(exist_ok=True)
    with FileLock(str(PLUGIN_DATA / "backup.local.lock"), timeout=0):
        files, ledger_id = collect()
        encrypted = encode(files, ledger_id, current)
        # Verify decryptability and hashes before publishing a complete snapshot.
        decoded, _ = decode(encrypted)
        if decoded != files:
            raise RuntimeError("Encrypted round trip mismatch")
        name = f"companion-{current.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.dpapi"
        destination = BACKUP_ROOT / name
        temporary = destination.with_suffix(".partial")
        try:
            with temporary.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        prune(BACKUP_ROOT, current)
    report = {
        "file": name,
        "file_count": len(files),
        "encrypted_bytes": len(encrypted),
        "created_at": current.isoformat(),
        "verified_decryption": True,
    }
    (PLUGIN_DATA / "backup-backup.local.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    return report


def maybe_backup(current):
    """Due every 24h when awake; failed attempts back off one hour."""
    status = PLUGIN_DATA / "backup-backup.local.json"
    failure = PLUGIN_DATA / "backup-failure.local.json"
    try:
        for path, limit, result in (
            (status, timedelta(hours=24), "not_due"),
            (failure, timedelta(hours=1), "failure_backoff"),
        ):
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                timestamp = previous["created_at" if path == status else "at"]
                age = current - datetime.fromisoformat(timestamp)
                exists = path == failure or (BACKUP_ROOT / previous["file"]).is_file()
                if exists and timedelta(0) <= age < limit:
                    return result
            except (OSError, ValueError, KeyError, TypeError):
                pass
        snapshot(current)
        return "created"
    except Exception as exc:
        # Backup failure must not stop health observations or runtime recovery.
        try:
            failure.write_text(
                json.dumps(
                    {"at": current.isoformat(), "error_type": type(exc).__name__}
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        return "failed"


def prune(folder, current):
    root = folder.resolve()
    removed = 0
    for path in folder.iterdir():
        match = NAME.fullmatch(path.name)
        if (
            not match
            or path.is_symlink()
            or path.resolve().parent != root
            or not path.is_file()
        ):
            continue
        try:
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if created < current - timedelta(days=7):
            path.unlink()
            removed += 1
    return removed


def verify_quarantine(path):
    sys.path.insert(0, str(PROJECT_ROOT))
    from plugins.astrbot_plugin_companion_core.memory import MemoryStore

    started = time.monotonic()
    files, manifest = decode(path.read_bytes())
    with tempfile.TemporaryDirectory(
        prefix="restore-drill-", dir=PLUGIN_DATA
    ) as scratch:
        root = Path(scratch)
        # Never extract configuration onto the active runtime or restore tokens.
        for name, content in files.items():
            if name.endswith((".sqlite", ".db")):
                destination = root / Path(name).name
                destination.write_bytes(content)
                with closing(sqlite3.connect(destination)) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Quarantine SQLite integrity failed")
        # Snapshot's old ledger is insufficient. Capture the live authoritative
        # ledger now, then replay ONLY against this isolated copy.
        with closing(
            sqlite3.connect(
                (PLUGIN_DATA / "deletions.sqlite").as_uri() + "?mode=ro", uri=True
            )
        ) as live:
            latest = sqlite_bytes(live)
        ledger = root / "latest.sqlite"
        ledger.write_bytes(latest)
        with closing(sqlite3.connect(ledger)) as check:
            if (
                check.execute("SELECT id FROM meta").fetchone()[0]
                != manifest["ledger_id"]
            ):
                raise RuntimeError("Current ledger identity mismatch")
        restored = MemoryStore.restore(
            root / "memory.sqlite", root / "quarantine.sqlite", ledger
        )
        try:
            deleted = sum(
                restored.db.execute(
                    f"SELECT count(*) FROM {table} WHERE id IN (SELECT id FROM ledger.deleted)"
                ).fetchone()[0]
                for table in ("facts", "events", "candidates", "emotion_events")
            )
            unsafe = restored.db.execute(
                "SELECT count(*) FROM outbox WHERE state='queued' AND idempotency_key IN (SELECT idempotency_key FROM ledger.submissions WHERE state IN ('sending','accepted_by_platform','unknown'))"
            ).fetchone()[0]
            if (
                deleted
                or unsafe
                or restored.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            ):
                raise RuntimeError("Latest ledger replay validation failed")
        finally:
            restored.close()
    return {
        "snapshot": path.name,
        "sqlite_integrity": "ok",
        "latest_ledger_replayed": True,
        "restored_deleted_items": deleted,
        "unsafe_repeat_queue": unsafe,
        "activated": False,
        "configuration_restored": False,
        "quarantine_seconds": round(time.monotonic() - started, 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "verify"])
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        report = snapshot()
    else:
        path = args.snapshot or max(
            BACKUP_ROOT.glob("companion-*.dpapi"), key=lambda p: p.name
        )
        report = verify_quarantine(path)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    (PLUGIN_DATA / f"backup-{args.action}.local.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
