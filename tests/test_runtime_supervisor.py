"""Run the real Windows wrapper with an isolated harmless watchdog fixture."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime_watchdog import process_start_ticks


@unittest.skipUnless(os.name == "nt", "Windows scheduled-task wrapper")
class SupervisorTests(unittest.TestCase):
    def invoke(self, saved=None, missing_config=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            scripts = root / "scripts"
            scripts.mkdir()
            config = root / "config"
            config.mkdir()
            runtime = root / "runtime"
            home = runtime / "data" / "plugin_data" / "companion_core"
            home.mkdir(parents=True)
            if not missing_config:
                (config / "runtime_root.local.json").write_text(
                    json.dumps({"runtime_root": str(runtime)}), encoding="utf-8"
                )
            source = (
                Path(__file__).resolve().parents[1]
                / "scripts"
                / "supervise_runtime.ps1"
            )
            target = scripts / source.name
            shutil.copy2(source, target)
            # The wrapper launches this fixture, never the production watchdog.
            (scripts / "runtime_watchdog.py").write_text(
                "from pathlib import Path\n"
                "Path(__file__).with_suffix('.started').write_text('one fixture launch')\n",
                encoding="utf-8",
            )
            if saved is not None:
                (home / "watchdog-process.local.json").write_text(
                    saved if isinstance(saved, str) else json.dumps(saved),
                    encoding="utf-8",
                )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-File", str(target)],
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return (
                result.returncode,
                (scripts / "runtime_watchdog.started").exists(),
                len(list((runtime / "data" / "companion_logs").glob("*"))),
            )

    def test_existing_process_and_exact_creation_time_skip_logs_and_launch(self):
        result = self.invoke(
            {"id": os.getpid(), "started_ticks": process_start_ticks(os.getpid())}
        )
        self.assertEqual(result, (0, False, 0))

    def test_reused_pid_wrong_time_or_malformed_identity_cannot_suppress_launch(self):
        for saved in (
            {"id": os.getpid(), "started_ticks": process_start_ticks(os.getpid()) - 1},
            "{invalid json",
            {"id": 2147483647, "started_ticks": 1},
        ):
            with self.subTest(saved=saved):
                code, launched, logs = self.invoke(saved)
                self.assertEqual(code, 0)
                self.assertTrue(launched)
                self.assertEqual(logs, 2)

    def test_missing_config_fails_before_child_or_logs(self):
        code, launched, logs = self.invoke(missing_config=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)
        self.assertEqual(logs, 0)


if __name__ == "__main__":
    unittest.main()
