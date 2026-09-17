from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.catalog import LEGACY_FAMILY, CatalogResult, ModelInfo
from adaptive_model_router.config import PACKAGED_CONFIG, load_config
from adaptive_model_router.hook import evaluate_hook


class HookTests(unittest.TestCase):
    def test_first_submission_blocks_second_identical_submission_allows(self) -> None:
        model = ModelInfo("fast-model", "Fast Model", "fast affordable", ("low", "medium"), 1)
        payload = {"session_id": "session-1", "model": "strong-model", "prompt": "README 오타 수정해줘"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch(
            "adaptive_model_router.hook.resolve_active_catalogs",
            return_value=(None, {LEGACY_FAMILY: CatalogResult("AVAILABLE", (model,))}),
        ):
            first = evaluate_hook(payload, load_config(PACKAGED_CONFIG))
            second = evaluate_hook(payload, load_config(PACKAGED_CONFIG))
        self.assertEqual("block", first["decision"])
        self.assertIn("Fast Model", first["reason"])
        self.assertEqual({"continue": True}, second)

    def test_state_stores_no_prompt_text(self) -> None:
        model = ModelInfo("balanced", "Balanced", "balanced everyday", ("medium",), 1)
        secret_prompt = "private prompt that must not be stored"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch(
            "adaptive_model_router.hook.resolve_active_catalogs",
            return_value=(None, {LEGACY_FAMILY: CatalogResult("AVAILABLE", (model,))}),
        ):
            evaluate_hook({"session_id": "s", "model": "balanced", "prompt": secret_prompt}, load_config(PACKAGED_CONFIG))
            contents = "".join(path.read_text(encoding="utf-8") for path in Path(directory).rglob("*.json"))
        self.assertNotIn(secret_prompt, contents)

    def test_claude_provider_blocks_and_reads_model_from_settings(self) -> None:
        model = ModelInfo("sonnet", "sonnet", "balanced everyday", ("low", "medium", "high"), 1)
        payload = {"session_id": "session-1", "prompt": "README 오타 수정해줘", "source": "user"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch(
            "adaptive_model_router.hook.load_current_claude_config", return_value=("sonnet", None),
        ), patch(
            "adaptive_model_router.hook.load_claude_catalog",
            return_value=CatalogResult("AVAILABLE", (model,)),
        ):
            first = evaluate_hook(payload, load_config(PACKAGED_CONFIG), provider="claude")
            second = evaluate_hook(payload, load_config(PACKAGED_CONFIG), provider="claude")
        self.assertEqual("block", first["decision"])
        self.assertIn("Claude Code", first["reason"])
        self.assertEqual({"continue": True}, second)

    def test_claude_provider_skips_non_user_sourced_prompts(self) -> None:
        payload = {"session_id": "session-2", "prompt": "cron으로 자동 실행된 prompt", "source": "schedule_wakeup"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ):
            result = evaluate_hook(payload, load_config(PACKAGED_CONFIG), provider="claude")
        self.assertEqual({"continue": True}, result)


if __name__ == "__main__":
    unittest.main()
