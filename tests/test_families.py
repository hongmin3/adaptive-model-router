from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.catalog import (
    LEGACY_FAMILY,
    CatalogResult,
    ModelInfo,
    default_family,
    detect_family,
    resolve_active_catalogs,
)
from adaptive_model_router.config import PACKAGED_CONFIG, load_config
from adaptive_model_router.selector import recommend


GPT_MODELS = (
    ModelInfo("gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work.",
              ("low", "medium", "high", "xhigh", "max"), 1),
    ModelInfo("gpt-5.6-sol", "GPT-5.6-Sol", "Reliable agentic workhorse for everyday tasks.",
              ("low", "medium", "high", "xhigh", "max"), 4),
    ModelInfo("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work.",
              ("low", "medium", "high", "xhigh", "max"), 7),
    ModelInfo("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model.",
              ("low", "medium", "high", "xhigh", "max"), 8),
)

# DeepSeek publishes no MEDIUM and no XHIGH, and its descriptions carry none of the
# profile hint words, so this family exercises both the pinning and the effort mapping.
DEEPSEEK_MODELS = (
    ModelInfo("deepseek-flash", "DeepSeek-Flash", "Latest frontier agentic coding model with image input.",
              ("low", "high", "max"), 1),
    ModelInfo("deepseek-v4-pro", "DeepSeek-V4-Pro", "Most capable frontier agentic coding model.",
              ("low", "high", "max"), 2),
)

CATALOGS = {
    "openai": CatalogResult("AVAILABLE", GPT_MODELS),
    "deepseek": CatalogResult("AVAILABLE", DEEPSEEK_MODELS),
}


class FamilyDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_slug_patterns_identify_each_family(self) -> None:
        for slug, expected in (
            ("gpt-5.6-sol", "openai"),
            ("gpt-6-astra", "openai"),
            ("deepseek-flash", "deepseek"),
            ("deepseek-v4-pro", "deepseek"),
        ):
            with self.subTest(slug=slug):
                self.assertEqual(expected, detect_family(slug, self.config))

    def test_unknown_and_missing_slugs_fall_back_to_the_default_family(self) -> None:
        self.assertIsNone(detect_family("mistral-large", self.config))
        self.assertIsNone(detect_family(None, self.config))
        self.assertEqual("openai", default_family(self.config))

    def test_config_without_families_still_routes(self) -> None:
        config = load_config(PACKAGED_CONFIG)
        config["providers"]["codex"].pop("families")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "models.json"
            cache.write_text(json.dumps({"models": [{
                "slug": "local-model", "display_name": "Local Model", "description": "balanced everyday",
                "visibility": "list", "priority": 1, "supported_reasoning_levels": [{"effort": "medium"}],
            }]}), encoding="utf-8")
            with patch(
                "adaptive_model_router.catalog.load_codex_catalog",
                return_value=CatalogResult("AVAILABLE", ()),
            ) as loader:
                family, catalogs = resolve_active_catalogs(config, "gpt-5.6-sol")
        loader.assert_called_once()
        self.assertIsNone(family)
        self.assertEqual([LEGACY_FAMILY], list(catalogs))


class CrossFamilyRecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def _recommend(self, family: str, profile: str, reasoning: str, current: str | None = None):
        return recommend(CATALOGS, family, profile, reasoning, self.config, current_model=current)

    def test_deepseek_profiles_are_pinned_despite_unhelpful_descriptions(self) -> None:
        expected = {
            "FAST": "deepseek-flash",
            "BALANCED": "deepseek-flash",
            "STRONG": "deepseek-v4-pro",
            "MAX": "deepseek-v4-pro",
        }
        for profile, slug in expected.items():
            with self.subTest(profile=profile):
                result = self._recommend("deepseek", profile, "HIGH")
                self.assertEqual(slug, result.selection.model.slug)

    def test_gpt_recommendation_carries_the_deepseek_counterpart(self) -> None:
        result = self._recommend("openai", "STRONG", "HIGH")
        self.assertEqual("gpt-5.6-sol", result.selection.model.slug)
        self.assertEqual("GPT", result.label)
        self.assertEqual(1, len(result.counterparts))
        counterpart = result.counterparts[0]
        self.assertEqual("deepseek", counterpart.family)
        self.assertEqual("deepseek-v4-pro", counterpart.model.slug)
        self.assertEqual("high", counterpart.reasoning)

    def test_deepseek_recommendation_carries_the_gpt_counterpart(self) -> None:
        result = self._recommend("deepseek", "BALANCED", "MEDIUM", current="deepseek-flash")
        self.assertEqual("deepseek-flash", result.selection.model.slug)
        self.assertEqual("DeepSeek", result.label)
        counterpart = result.counterparts[0]
        self.assertEqual("openai", counterpart.family)
        self.assertEqual("gpt-5.6-terra", counterpart.model.slug)
        self.assertEqual("medium", counterpart.reasoning)

    def test_the_active_family_never_appears_as_its_own_alternative(self) -> None:
        for family in ("openai", "deepseek"):
            with self.subTest(family=family):
                result = self._recommend(family, "BALANCED", "MEDIUM")
                self.assertNotIn(family, [item.family for item in result.counterparts])

    def test_unsupported_medium_rounds_up_rather_than_down(self) -> None:
        # LOW and HIGH are equidistant from MEDIUM; rounding down would silently hand the
        # everyday default DeepSeek's reduced mode.
        result = self._recommend("deepseek", "BALANCED", "MEDIUM")
        self.assertEqual("high", result.selection.reasoning)

    def test_tie_break_direction_is_configurable(self) -> None:
        config = load_config(PACKAGED_CONFIG)
        config["model_selection"]["reasoning_tie_break"] = "down"
        result = recommend(CATALOGS, "deepseek", "BALANCED", "MEDIUM", config)
        self.assertEqual("low", result.selection.reasoning)

    def test_xhigh_maps_to_the_nearest_level_the_family_publishes(self) -> None:
        result = self._recommend("deepseek", "MAX", "XHIGH")
        self.assertEqual("max", result.selection.reasoning)


class PackagedDefaultMergeTests(unittest.TestCase):
    def test_user_config_gains_new_keys_but_keeps_its_own_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / "router_config.json"
            packaged = json.loads(PACKAGED_CONFIG.read_text(encoding="utf-8"))
            stale = {
                "version": packaged["version"],
                "defaults": packaged["defaults"],
                "thresholds": {"low_max": 2, "medium_max": 8, "high_max": 14},
                "reasoning_order": packaged["reasoning_order"],
                "rules": packaged["rules"][:1],
                "model_selection": {"description_hints": packaged["model_selection"]["description_hints"]},
                "providers": {"codex": {"label": "Codex", "reasoning_label": "Reasoning"}},
            }
            user.write_text(json.dumps(stale), encoding="utf-8")
            config = load_config(user)
        # Keys added to the packaged defaults after the user copy was written must appear,
        # because that copy is never overwritten once installed.
        self.assertIn("families", config["providers"]["codex"])
        self.assertEqual("up", config["model_selection"]["reasoning_tie_break"])
        # The user's own edits win, and their trimmed rule list is not repopulated.
        self.assertEqual(2, config["thresholds"]["low_max"])
        self.assertEqual(1, len(config["rules"]))


if __name__ == "__main__":
    unittest.main()
