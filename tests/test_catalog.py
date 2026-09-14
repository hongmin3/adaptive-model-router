from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.catalog import load_claude_catalog, load_codex_catalog
from adaptive_model_router.config import load_config


class LocalCatalogTests(unittest.TestCase):
    def test_codex_default_reads_cache_without_subprocess(self) -> None:
        payload = {
            "models": [{
                "slug": "local-model",
                "display_name": "Local Model",
                "description": "balanced everyday",
                "visibility": "list",
                "priority": 1,
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "models.json"
            cache.write_text(json.dumps(payload), encoding="utf-8")
            with patch("adaptive_model_router.catalog.subprocess.run") as run:
                result = load_codex_catalog(cache_path=cache)
        run.assert_not_called()
        self.assertEqual("AVAILABLE", result.status)
        self.assertEqual("local-model", result.models[0].slug)

    def test_claude_default_uses_registry_without_subprocess(self) -> None:
        with patch("adaptive_model_router.catalog.subprocess.run") as run:
            result = load_claude_catalog(config=load_config())
        run.assert_not_called()
        self.assertEqual("AVAILABLE", result.status)
        self.assertIn("sonnet", {model.slug for model in result.models})


if __name__ == "__main__":
    unittest.main()
