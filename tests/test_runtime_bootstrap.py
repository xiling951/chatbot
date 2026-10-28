"""Host entry-point compatibility without launching the live service."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runtime_bootstrap


class BootstrapTests(unittest.TestCase):
    def invoke(self, root, settings, installation_error=None):
        (root / "settings.local.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )
        with (
            patch.object(runtime_bootstrap, "PLUGIN_DATA", root),
            patch.object(runtime_bootstrap, "CODE_ROOT", root),
            patch.object(runtime_bootstrap.os, "chdir") as chdir,
            patch.object(runtime_bootstrap.runpy, "run_path") as run,
            patch.object(
                runtime_bootstrap, "install_proxy", side_effect=installation_error
            ) as install,
            patch.object(sys, "path", list(sys.path)),
            patch.object(sys, "argv", list(sys.argv)),
        ):
            runtime_bootstrap.main()
            chdir.assert_called_once_with(root)
            run.assert_called_once_with(str(root / "main.py"), run_name="__main__")
            self.assertEqual(sys.argv, [str(root / "main.py")])
            return json.loads(
                (root / "qq-transport-status.local.json").read_text()
            ), install.call_count

    def test_default_entry_keeps_proxy_opt_in_and_original_main(self):
        with tempfile.TemporaryDirectory() as temp:
            status, calls = self.invoke(Path(temp), {})
            self.assertFalse(status["qq_loopback_proxy_installed"])
            self.assertEqual(calls, 0)

    def test_explicit_qq_proxy_failure_does_not_prevent_weixin_and_web_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            status, calls = self.invoke(
                Path(temp),
                {"qq_loopback_proxy": "http://127.0.0.1:7890"},
                ValueError("fixture invalid setting"),
            )
            self.assertEqual(calls, 1)
            self.assertFalse(status["qq_loopback_proxy_installed"])
            self.assertEqual(status["error_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
