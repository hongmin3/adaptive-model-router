from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.manage_claude_hook import install, status, uninstall


class ManageClaudeHookTests(unittest.TestCase):
    def test_install_preserves_existing_settings_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".claude" / "settings.json"
            path.parent.mkdir(parents=True)
            original = {
                "theme": "auto",
                "enabledPlugins": {"claude-mem@thedotmack": True},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "existing"}]}]},
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            backup, changed = install(home, "C:/Python/python.exe")
            second_backup, second_changed = install(home, "C:/Python/python.exe")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(changed)
            self.assertIsNotNone(backup)
            self.assertFalse(second_changed)
            self.assertIsNone(second_backup)
            self.assertEqual("auto", payload["theme"])
            self.assertEqual(original["enabledPlugins"], payload["enabledPlugins"])
            self.assertEqual(original["hooks"]["SessionStart"], payload["hooks"]["SessionStart"])
            self.assertIn("--provider claude", payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"])
            self.assertTrue(status(home))

    def test_install_creates_settings_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backup, changed = install(home, "C:/Python/python.exe")
            self.assertTrue(changed)
            self.assertIsNone(backup)
            self.assertTrue(status(home))

    def test_uninstall_removes_only_router_hook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            install(home, "C:/Python/python.exe")
            _, changed = uninstall(home)
            self.assertTrue(changed)
            self.assertFalse(status(home))


if __name__ == "__main__":
    unittest.main()
