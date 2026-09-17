from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adaptive_model_router
from adaptive_model_router.config import PACKAGED_CONFIG, SOURCE_CONFIG, load_config

# Anything added to the package after the last install is imported inside the test that
# needs it, never at module scope: a module-level import of a new symbol turns the wrong-
# build case into a collection error, which takes the guard below down with it and hides
# the one message that explains what went wrong.


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class WorkingTreeIsUnderTestTests(unittest.TestCase):
    """The loader, not the suite, chooses which build gets verified - so check it."""

    def test_package_resolves_to_this_working_tree(self) -> None:
        resolved = Path(adaptive_model_router.__file__).resolve()
        expected = (REPOSITORY_ROOT / "src" / "adaptive_model_router").resolve()
        self.assertEqual(
            expected, resolved.parent,
            "The suite imported an installed copy of adaptive_model_router "
            f"({resolved}) instead of this repository's src/ tree. Every result below "
            "describes that other build. Run `python run_tests.py` from the repository "
            "root (or `python -m unittest discover` from the repository root), not "
            "`python -m unittest discover -s tests`.",
        )

    def test_packaged_and_source_configs_are_identical(self) -> None:
        """One of these two copies ships and the other is read in-place; a divergence
        routes differently depending on how the router was installed."""
        packaged = json.loads(PACKAGED_CONFIG.read_text(encoding="utf-8"))
        source = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(source, packaged)


class InstalledConfigDriftTests(unittest.TestCase):
    """The user copy is written once and never overwritten, so it silently ages."""

    def _write_user_config(self, directory: str, mutate) -> Path:
        payload = json.loads(PACKAGED_CONFIG.read_text(encoding="utf-8"))
        mutate(payload)
        path = Path(directory) / "router_config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_an_older_user_config_is_reported_as_stale(self) -> None:
        from adaptive_model_router.config import config_state

        with tempfile.TemporaryDirectory() as directory:
            path = self._write_user_config(directory, lambda p: p.update(version=p["version"] - 1))
            state = config_state(path)
        self.assertTrue(state["stale"])
        self.assertLess(state["version"], state["packaged_version"])

    def test_the_packaged_config_is_never_reported_as_stale(self) -> None:
        from adaptive_model_router.config import config_state

        state = config_state(PACKAGED_CONFIG)
        self.assertFalse(state["stale"])

    def test_a_missing_dict_key_reaches_an_old_install_but_a_list_does_not(self) -> None:
        """Why the staleness warning exists: half the defaults propagate and half do not."""
        def strip(payload: dict) -> None:
            payload["version"] = payload["version"] - 1
            payload["providers"]["claude"].pop("families")
            payload["rules"] = [rule for rule in payload["rules"] if rule["id"] != "code_review"]

        with tempfile.TemporaryDirectory() as directory:
            merged = load_config(self._write_user_config(directory, strip))
        # Dicts recurse, so the tier pins added later do reach this installation.
        self.assertIn("families", merged["providers"]["claude"])
        # Lists are atomic, so a rule added later does not.
        self.assertNotIn("code_review", [rule["id"] for rule in merged["rules"]])

    def test_refreshing_replaces_the_config_and_keeps_a_backup(self) -> None:
        from adaptive_model_router.config import refresh_user_config

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "router_config.json"
            destination.write_text('{"version": 1, "rules": []}', encoding="utf-8")
            with patch("adaptive_model_router.config.user_config_path", return_value=destination):
                backup, written = refresh_user_config(backup_root=Path(directory) / "backups")
            self.assertEqual(destination, written)
            self.assertIsNotNone(backup)
            self.assertIn('"version": 1', backup.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(PACKAGED_CONFIG.read_text(encoding="utf-8")),
                json.loads(destination.read_text(encoding="utf-8")),
            )

    def test_refreshing_a_fresh_install_needs_no_backup(self) -> None:
        from adaptive_model_router.config import refresh_user_config

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "router_config.json"
            with patch("adaptive_model_router.config.user_config_path", return_value=destination):
                backup, written = refresh_user_config()
        self.assertIsNone(backup)
        self.assertEqual(destination, written)


if __name__ == "__main__":
    unittest.main()
