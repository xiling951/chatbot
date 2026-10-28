import io
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from local_backup import dpapi, encode, decode, memory_pair, prune, maybe_backup
from local_backup import collect_emote_assets, collect_host_scripts, HOST_SCRIPTS
from plugins.astrbot_plugin_companion_core.memory import MemoryStore


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 27, tzinfo=timezone.utc)

    def test_actual_windows_user_encryption_and_tamper_detection(self):
        payload = b"owned test secret, not an actual credential"
        encrypted = dpapi(payload)
        self.assertNotIn(payload, encrypted)
        self.assertEqual(dpapi(encrypted, decrypt=True), payload)
        tampered = bytearray(encrypted)
        tampered[-1] ^= 1
        with self.assertRaises(OSError):
            dpapi(bytes(tampered), decrypt=True)

    def test_explicit_startup_scripts_are_backed_up_without_recursing_private_project_files(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            for name in HOST_SCRIPTS:
                (root / "scripts" / name).write_text(
                    "controlled fixture", encoding="utf-8"
                )
            (root / "scripts/private.local.json").write_text(
                "not included", encoding="utf-8"
            )
            files = collect_host_scripts(root)
            self.assertEqual(len(files), len(HOST_SCRIPTS))
            self.assertIn("deployment/scripts/runtime_bootstrap.py", files)
            self.assertFalse(any(".local." in name for name in files))
            (root / "scripts/start.ps1").unlink()
            with self.assertRaises(ValueError):
                collect_host_scripts(root)

    def test_static_emote_material_included_but_other_files_and_caches_excluded(self):
        import shutil

        original = (
            Path(__file__).resolve().parents[1]
            / "plugins/astrbot_plugin_companion_core"
        )
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp)
            self.assertEqual(collect_emote_assets(plugin), {})
            shutil.copytree(original / "assets", plugin / "assets")
            (plugin / "assets/emotes/secret.local.json").write_text(
                "not a real credential"
            )
            (plugin / "assets/emotes/cache").mkdir()
            files = collect_emote_assets(plugin)
            self.assertEqual(len(files), 65)
            self.assertIn("assets/emotes/E32.png", files)
            self.assertIn("assets/emotes/E32.svg", files)
            self.assertFalse(any("secret" in key or "cache" in key for key in files))
            (plugin / "assets/emotes/manifest.json").unlink()
            with self.assertRaises(ValueError):
                collect_emote_assets(plugin)

    def test_bundle_roundtrip_and_unsafe_path_rejection(self):
        original = {"config.json": b'{"test":true}', "plugin/memory.sqlite": b"test"}
        restored, manifest = decode(encode(original, "ledger", self.current))
        self.assertEqual(restored, original)
        self.assertEqual(manifest["ledger_id"], "ledger")
        with self.assertRaises(ValueError):
            decode(encode({"../escape.txt": b"unsafe"}, "ledger", self.current))

    def test_duplicate_or_digest_failure_not_extracted(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("data.txt", b"modified")
            archive.writestr(
                "manifest.json",
                json.dumps({"format": 1, "files": {"data.txt": "invalidhash"}}),
            )
        with self.assertRaises(ValueError):
            decode(dpapi(stream.getvalue()))

    def test_attached_pair_snapshot_preserves_identity_and_deletion_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = MemoryStore(root / "memory.sqlite", root / "deletions.sqlite")
            try:
                scope = store.scope("web", "bot", "owner", "private", "persona")
                store.put(store.begin(scope, "old-source"), "音乐", "爵士")
                store.forget(scope, "音乐")
                pair, identifier = memory_pair(root)
                with closing(sqlite3.connect(":memory:")) as snapshot:
                    snapshot.deserialize(pair["memory.sqlite"])
                    self.assertEqual(
                        snapshot.execute(
                            "SELECT value FROM meta WHERE key='ledger_id'"
                        ).fetchone()[0],
                        identifier,
                    )
                    self.assertEqual(
                        snapshot.execute("SELECT count(*) FROM facts").fetchone()[0], 0
                    )
                    self.assertEqual(
                        snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                    )
                with closing(sqlite3.connect(":memory:")) as ledger:
                    ledger.deserialize(pair["deletions.sqlite"])
                    self.assertEqual(
                        ledger.execute("SELECT id FROM meta").fetchone()[0], identifier
                    )
                    self.assertEqual(
                        ledger.execute("SELECT count(*) FROM deleted").fetchone()[0], 1
                    )
            finally:
                store.close()

    def test_retention_only_our_complete_encrypted_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / ("companion-20261001T000000-" + "a" * 32 + ".dpapi")
            recent = root / ("companion-20261026T000000-" + "b" * 32 + ".dpapi")
            unrelated = root / "other-backup.dpapi"
            partial = root / ("companion-20261001T000000-" + "c" * 32 + ".partial")
            for path in (old, recent, unrelated, partial):
                path.write_bytes(b"owned test")
            self.assertEqual(prune(root, self.current), 1)
            self.assertFalse(old.exists())
            self.assertTrue(all(p.exists() for p in (recent, unrelated, partial)))

    def test_daily_schedule_missing_file_and_failure_backoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "owned.dpapi").write_bytes(b"test")
            status = root / "backup-backup.local.json"
            status.write_text(
                json.dumps(
                    {"file": "owned.dpapi", "created_at": self.current.isoformat()}
                )
            )
            with (
                patch("local_backup.PLUGIN_DATA", root),
                patch("local_backup.BACKUP_ROOT", root),
                patch(
                    "local_backup.snapshot", side_effect=OSError("simulated")
                ) as snapshot,
            ):
                self.assertEqual(maybe_backup(self.current), "not_due")
                snapshot.assert_not_called()
                (root / "owned.dpapi").unlink()
                self.assertEqual(maybe_backup(self.current), "failed")
                self.assertEqual(maybe_backup(self.current), "failure_backoff")
                snapshot.assert_called_once()

    def test_backup_failure_even_when_status_unwritable_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch("local_backup.PLUGIN_DATA", Path(temp)),
                patch("local_backup.snapshot", side_effect=OSError("disk unavailable")),
                patch.object(
                    Path, "write_text", side_effect=OSError("disk unavailable")
                ),
            ):
                self.assertEqual(maybe_backup(self.current), "failed")
