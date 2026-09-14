from __future__ import annotations

import unittest

from adaptive_model_router.catalog import ModelInfo
from adaptive_model_router.config import load_config
from adaptive_model_router.selector import select_model


MODELS = (
    ModelInfo("strong", "Strong", "Reliable model for complex work", ("low", "medium", "high", "xhigh"), 1),
    ModelInfo("balanced", "Balanced", "Balanced model for everyday work", ("low", "medium", "high"), 5),
    ModelInfo("fast", "Fast", "Fast and affordable model", ("low", "medium"), 8),
)


class ModelSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()

    def test_limit_uses_same_provider_fallback(self) -> None:
        result = select_model(
            MODELS, "FAST", "LOW", self.config,
            statuses={"fast": {"status": "WEEKLY_LIMIT_REACHED", "reset": "2026-09-18 09:00"}},
        )
        self.assertEqual("balanced", result.model.slug)
        self.assertEqual("fast", result.fallback_from)
        self.assertEqual("2026-09-18 09:00", result.reset)

    def test_unsupported_reasoning_uses_nearest_supported_level(self) -> None:
        result = select_model((MODELS[2],), "FAST", "HIGH", self.config)
        self.assertEqual("medium", result.reasoning)

    def test_current_model_is_kept_when_sufficient(self) -> None:
        result = select_model(MODELS, "BALANCED", "MEDIUM", self.config, current_model="balanced")
        self.assertEqual("balanced", result.model.slug)

    def test_explicit_current_model_is_kept(self) -> None:
        result = select_model(MODELS, "FAST", "LOW", self.config, current_model="strong", force_current=True)
        self.assertEqual("strong", result.model.slug)


if __name__ == "__main__":
    unittest.main()
