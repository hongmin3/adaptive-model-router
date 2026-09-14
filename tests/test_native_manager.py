from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.native_manager import ensure_user_config, install_archive


class NativeManagerTests(unittest.TestCase):
    def test_user_config_is_created_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "router_config.json"
            with patch("adaptive_model_router.native_manager.user_config_path", return_value=destination):
                first = ensure_user_config()
                destination.write_text('{"custom": true}', encoding="utf-8")
                second = ensure_user_config()
            self.assertEqual(first, second)
            self.assertEqual(destination.read_text(encoding="utf-8"), '{"custom": true}')

    def test_install_archive_writes_binary_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            archive = Path(temp) / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("bin/codex.exe", b"test-binary")
                bundle.writestr("bin/codex-code-mode-host.exe", b"host")
                bundle.writestr("codex-path/rg.exe", b"rg")
                bundle.writestr("codex-resources/codex-command-runner.exe", b"runner")
                bundle.writestr("codex-resources/codex-windows-sandbox-setup.exe", b"setup")
            with patch.dict(os.environ, {"ADAPTIVE_MODEL_ROUTER_INSTALL_DIR": str(root)}), patch(
                "adaptive_model_router.native_manager._prepend_user_path"
            ):
                installed = install_archive(archive, "router-v1")
            self.assertEqual(installed.read_bytes(), b"test-binary")
            state = json.loads((root / "installed.json").read_text(encoding="utf-8"))
            self.assertEqual(state["version"], "router-v1")
            self.assertEqual(len(state["files"]), 5)
            self.assertEqual((root / "bin" / "codex-code-mode-host.exe").read_bytes(), b"host")
            self.assertEqual((root / "codex-path" / "rg.exe").read_bytes(), b"rg")


if __name__ == "__main__":
    unittest.main()
