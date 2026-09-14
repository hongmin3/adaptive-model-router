from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.catalog import CatalogResult, ModelInfo
from adaptive_model_router.config import load_config
from adaptive_model_router.hook import evaluate_hook


class HookTests(unittest.TestCase):
    def test_first_submission_blocks_second_identical_submission_allows(self) -> None:
        model = ModelInfo("fast-model", "Fast Model", "fast affordable", ("low", "medium"), 1)
        payload = {"session_id": "session-1", "model": "strong-model", "prompt": "README 오타 수정해줘"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch(
            "adaptive_model_router.hook.load_codex_catalog", return_value=CatalogResult("AVAILABLE", (model,))
        ):
            first = evaluate_hook(payload, load_config())
            second = evaluate_hook(payload, load_config())
        self.assertEqual("block", first["decision"])
        self.assertIn("Fast Model", first["reason"])
        self.assertEqual({"continue": True}, second)

    def test_state_stores_no_prompt_text(self) -> None:
        model = ModelInfo("balanced", "Balanced", "balanced everyday", ("medium",), 1)
        secret_prompt = "private prompt that must not be stored"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch(
            "adaptive_model_router.hook.load_codex_catalog", return_value=CatalogResult("AVAILABLE", (model,))
        ):
            evaluate_hook({"session_id": "s", "model": "balanced", "prompt": secret_prompt}, load_config())
            contents = "".join(path.read_text(encoding="utf-8") for path in Path(directory).rglob("*.json"))
        self.assertNotIn(secret_prompt, contents)


if __name__ == "__main__":
    unittest.main()
