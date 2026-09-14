from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.native_manager import install_archive


class NativeManagerTests(unittest.TestCase):
    def test_install_archive_writes_binary_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            archive = Path(temp) / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("codex.exe", b"test-binary")
            with patch.dict(os.environ, {"ADAPTIVE_MODEL_ROUTER_INSTALL_DIR": str(root)}), patch(
                "adaptive_model_router.native_manager._prepend_user_path"
            ):
                installed = install_archive(archive, "router-v1")
            self.assertEqual(installed.read_bytes(), b"test-binary")
            self.assertEqual(
                json.loads((root / "installed.json").read_text(encoding="utf-8"))["version"],
                "router-v1",
            )


if __name__ == "__main__":
    unittest.main()
