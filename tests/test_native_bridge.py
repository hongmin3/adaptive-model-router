from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.catalog import CatalogResult, ModelInfo
from adaptive_model_router.config import load_config
from adaptive_model_router.native_bridge import route_native


class NativeBridgeTests(unittest.TestCase):
    def test_returns_machine_readable_recommendation(self) -> None:
        catalog = CatalogResult(
            status="AVAILABLE",
            models=(
                ModelInfo("fast-model", "Fast Model", "fast low latency", ("low", "medium"), 1),
                ModelInfo("strong-model", "Strong Model", "reliable complex demanding", ("medium", "high"), 2),
            ),
        )
        with patch("adaptive_model_router.native_bridge.load_codex_catalog", return_value=catalog):
            result = route_native(
                {"prompt": "프로젝트 전체 구조를 분석하고 아키텍처를 개선해줘", "current_model": "fast-model"},
                load_config(),
            )
        self.assertTrue(result["enabled"])
        self.assertEqual(result["model"], "strong-model")
        self.assertEqual(result["reasoning"], "high")

    def test_empty_prompt_is_disabled(self) -> None:
        self.assertEqual(route_native({"prompt": ""})["error"], "empty_prompt")

    def test_bridge_runs_outside_repository(self) -> None:
        process = subprocess.run(
            [sys.executable, "-m", "adaptive_model_router.native_bridge"],
            input=json.dumps({"prompt": "README typo fix", "current_model": None}),
            text=True,
            capture_output=True,
            check=True,
            cwd=Path.home(),
        )
        self.assertTrue(json.loads(process.stdout)["enabled"])


if __name__ == "__main__":
    unittest.main()
