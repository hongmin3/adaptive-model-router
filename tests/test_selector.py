from __future__ import annotations

import unittest

from adaptive_model_router.catalog import ModelInfo
from adaptive_model_router.config import PACKAGED_CONFIG, load_config
from adaptive_model_router.selector import select_model


MODELS = (
    ModelInfo("strong", "Strong", "Reliable model for complex work", ("low", "medium", "high", "xhigh"), 1),
    ModelInfo("balanced", "Balanced", "Balanced model for everyday work", ("low", "medium", "high"), 5),
    ModelInfo("fast", "Fast", "Fast and affordable model", ("low", "medium"), 8),
)

OPENAI_MODELS = (
    ModelInfo("gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work.",
              ("low", "medium", "high", "xhigh", "max"), 1),
    ModelInfo("gpt-5.6-sol", "GPT-5.6-Sol", "Reliable agentic workhorse for everyday tasks.",
              ("low", "medium", "high", "xhigh", "max"), 4),
    ModelInfo("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work.",
              ("low", "medium", "high", "xhigh", "max"), 7),
    ModelInfo("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model.",
              ("low", "medium", "high", "xhigh", "max"), 8),
    ModelInfo("gpt-5.5", "GPT-5.5", "Proven previous-generation model for coding and general work.",
              ("low", "medium", "high"), 12),
)

CACHE_DESCRIPTION_MODELS = (
    ModelInfo("gpt-5.6-sol", "GPT-5.6-Sol", "Latest frontier agentic coding model.",
              ("low", "medium", "high", "xhigh", "max"), 1),
    ModelInfo("gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work.",
              ("low", "medium", "high", "xhigh", "max"), 1),
    ModelInfo("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work.",
              ("low", "medium", "high", "xhigh", "max"), 7),
    ModelInfo("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model.",
              ("low", "medium", "high", "xhigh", "max"), 8),
)


class ModelSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

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

    def test_current_openai_generation_maps_all_four_profiles(self) -> None:
        expected = {
            "FAST": ("gpt-5.6-luna", "LOW"),
            "BALANCED": ("gpt-5.6-terra", "MEDIUM"),
            "STRONG": ("gpt-5.6-sol", "HIGH"),
            "MAX": ("gpt-6-astra", "XHIGH"),
        }
        for profile, (slug, reasoning) in expected.items():
            with self.subTest(profile=profile):
                result = select_model(
                    OPENAI_MODELS, profile, reasoning,
                    self.config, current_model="gpt-5.6-sol",
                )
                self.assertEqual(slug, result.model.slug)

    def test_new_catalog_model_is_automatically_considered(self) -> None:
        future = ModelInfo(
            "gpt-6-terra", "GPT-6-Terra", "Balanced next-generation coding model.",
            ("low", "medium", "high"), 2,
        )
        result = select_model(OPENAI_MODELS + (future,), "BALANCED", "MEDIUM", self.config)
        self.assertEqual("gpt-6-terra", result.model.slug)

    def test_previous_generation_model_is_not_a_recommendation_target(self) -> None:
        result = select_model((OPENAI_MODELS[-1],), "BALANCED", "MEDIUM", self.config)
        self.assertIsNone(result.model)

    def test_catalog_description_drift_does_not_reverse_sol_and_astra(self) -> None:
        strong = select_model(CACHE_DESCRIPTION_MODELS, "STRONG", "HIGH", self.config)
        maximum = select_model(CACHE_DESCRIPTION_MODELS, "MAX", "XHIGH", self.config)

        self.assertEqual("gpt-5.6-sol", strong.model.slug)
        self.assertEqual("gpt-6-astra", maximum.model.slug)


if __name__ == "__main__":
    unittest.main()
