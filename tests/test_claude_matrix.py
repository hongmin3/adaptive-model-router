"""End-to-end routing matrix for the Claude Code provider.

`test_prompt_matrix.py` stops at the scoring stage: it asserts the profile and reasoning a
prompt produces, never the model that profile resolves to.  That gap hid an inverted
mapping in both directions at once - the MAX profile selected the cheapest Claude tier
because no configured criterion matched it, and the refreshed catalog selected the most
expensive tier for FAST for the mirror-image reason.  Every assertion here therefore runs
the full prompt -> profile -> model -> effort path and states the model alias literally.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from adaptive_model_router.catalog import (
    CatalogResult,
    ClaudeTier,
    ModelInfo,
    claude_alias_for_model,
    claude_model_from_transcript,
    default_family,
    discover_claude_tiers,
    load_claude_catalog,
)
from adaptive_model_router.cli import main as cli_main
from adaptive_model_router.config import PACKAGED_CONFIG, load_config
from adaptive_model_router.hook import evaluate_hook
from adaptive_model_router.scorer import score_prompt
from adaptive_model_router.selector import NO_EVIDENCE, classify_model, recommend, select_model


# The tier each profile must resolve to, written out rather than derived from the
# configuration the code also reads - an expectation computed from the code under test
# cannot disagree with it.
PROFILE_TO_ALIAS = {"FAST": "haiku", "BALANCED": "sonnet", "STRONG": "opus", "MAX": "fable"}

# (prompt, profile, reasoning, Claude effort).  The effort differs from the reasoning only
# where the requested level is outside what Claude Code exposes.
PROMPT_MATRIX = (
    # --- trivial, narrowly scoped edits: cheapest tier, lowest effort -------------------
    ("README 오타 수정해줘", "FAST", "LOW", "low"),
    ("변수명 한 줄만 바꿔줘", "FAST", "LOW", "low"),
    ("이 문장을 자연스럽게 번역해줘", "FAST", "LOW", "low"),
    ("README에 security라는 단어 한 줄 추가해줘", "FAST", "LOW", "low"),
    ("Explain this algorithm", "FAST", "LOW", "low"),
    ("이 요청을 처리해줘", "FAST", "LOW", "low"),
    # "고쳐"/"오탈자" are ordinary phrasings for a tiny edit; without them these landed on
    # the low-confidence fallback, which reaches the same tier with no stated reason.
    ("README 오타 하나만 고쳐줘", "FAST", "LOW", "low"),
    ("README의 한 문장 오탈자만 고쳐줘", "FAST", "LOW", "low"),
    # --- ordinary implementation and diagnosis: everyday tier --------------------------
    ("Python 자동화 기능을 구현해줘", "BALANCED", "MEDIUM", "medium"),
    ("새 API endpoint를 구현해줘", "BALANCED", "MEDIUM", "medium"),
    ("로그인 실패 버그를 분석하고 수정해줘", "BALANCED", "MEDIUM", "medium"),
    ("코드 리뷰해줘", "BALANCED", "MEDIUM", "medium"),
    ("여러 모듈의 의존성 분석해줘", "BALANCED", "MEDIUM", "medium"),
    ("알고리즘 복잡도 분석해줘", "BALANCED", "HIGH", "high"),
    ("SQL 쿼리 최적화해줘", "BALANCED", "HIGH", "high"),
    # --- wide scope, hard reasoning or high impact: strong tier ------------------------
    ("프로젝트 전체를 분석해줘", "STRONG", "HIGH", "high"),
    ("원인 불명의 복잡한 버그를 분석하고 수정해줘", "STRONG", "HIGH", "high"),
    ("Refactor architecture", "STRONG", "HIGH", "high"),
    ("운영 서버의 DB 백업을 삭제해줘", "STRONG", "HIGH", "high"),
    ("보안 취약점을 점검해줘", "STRONG", "HIGH", "high"),
    # A mechanical bulk edit is wide but not hard: strong model, cheap effort.
    ("여러 파일의 변수 이름을 일괄 변경해줘", "STRONG", "LOW", "low"),
    ("사용량 아껴서 프로젝트 전체를 분석해줘", "STRONG", "LOW", "low"),
    # --- the hardest requests: top tier -------------------------------------------------
    ("프로젝트 전체 아키텍처의 근본 원인을 분석하고 보안 코드를 수정한 뒤 테스트해줘", "MAX", "XHIGH", "xhigh"),
    ("전체 프로젝트 보안 취약점을 점검하고 수정해줘", "MAX", "XHIGH", "xhigh"),
    # --- explicit effort directives override the scored level, never the model ---------
    ("reasoning high로 README 오타 수정해줘", "FAST", "HIGH", "high"),
    ("reasoning max로 README 오타 수정해줘", "FAST", "MAX", "max"),
    ("effort max로 테스트 코드 작성해줘", "BALANCED", "MAX", "max"),
    ("최고 성능으로 전체 프로젝트를 리뷰해줘", "STRONG", "MAX", "max"),
    ("max로 해서 운영 서버 설정 변경해줘", "MAX", "MAX", "max"),
    # ULTRA exists for Codex only; Claude Code's ladder stops at max, so it clamps.
    ("reasoning ultra로 프로젝트 전체를 분석해줘", "STRONG", "ULTRA", "max"),
    ("ultra로 해줘", "FAST", "ULTRA", "max"),
)

REAL_HELP = """  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name (e.g.
                                        'claude-fable-5').
"""


# What the installed Claude Code build's own tier table looks like once extracted.  The
# tests inject it rather than scanning the real executable: a suite that reads a 220 MB
# binary is both slow and a statement about this machine's install, not about the code.
INSTALLED_TIERS = (
    ClaudeTier("sonnet", "Most efficient for everyday tasks", "claude-sonnet-5"),
    ClaudeTier("opus", "Most capable for ambitious work", "claude-opus-5"),
    ClaudeTier("haiku", "Fastest for quick answers", "claude-haiku-4-5"),
    ClaudeTier("fable", "For your toughest challenges", "claude-fable-5-1"),
    # Named by the build but with no default model id, so `--model mythos` resolves to
    # nothing and the router must not offer it.
    ClaudeTier("mythos", "", ""),
)


INSTALLED_TIERS_BY_ALIAS = {tier.alias: tier for tier in INSTALLED_TIERS}


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    class _Result:
        pass

    result = _Result()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


def _patch_discovery(tiers=INSTALLED_TIERS):
    return patch("adaptive_model_router.catalog.discover_claude_tiers", return_value=tiers)


class ClaudeRoutingMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)
        cls.family_key = default_family(cls.config, "claude")
        cls.family = cls.config["providers"]["claude"]["families"][cls.family_key]
        cls.catalog = load_claude_catalog(config=cls.config)

    def test_prompt_matrix_reaches_the_model_and_the_effort(self) -> None:
        for prompt, profile, reasoning, effort in PROMPT_MATRIX:
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                self.assertEqual((profile, reasoning), (scored.profile, scored.reasoning))
                selection = select_model(
                    self.catalog.models, scored.profile, scored.reasoning,
                    self.config, family=self.family,
                )
                self.assertIsNotNone(selection.model)
                self.assertEqual(PROFILE_TO_ALIAS[profile], selection.model.slug)
                self.assertEqual(effort, selection.reasoning)

    def test_a_tiny_edit_is_matched_by_a_rule_not_by_the_fallback(self) -> None:
        """Same tier either way, but the fallback states no reason and carries 0.15 confidence."""
        for prompt in ("README 오타 하나만 고쳐줘", "README의 한 문장 오탈자만 고쳐줘"):
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                self.assertFalse(scored.uncertain_default_used)
                self.assertIn("narrow simple edit", [match.label for match in scored.matches])

    def test_matrix_covers_every_profile_and_every_reasoning_level(self) -> None:
        """A matrix is only exhaustive if it says so and fails when it stops being."""
        profiles = {row[1] for row in PROMPT_MATRIX}
        reasonings = {row[2] for row in PROMPT_MATRIX}
        self.assertEqual(set(PROFILE_TO_ALIAS), profiles)
        self.assertEqual(
            {level.upper() for level in self.config["reasoning_order"]}, reasonings,
        )

    def test_recommendation_path_agrees_with_direct_selection(self) -> None:
        """The hook and the CLI both go through recommend(); it must not reroute."""
        catalogs = {self.family_key: self.catalog}
        for prompt, profile, _reasoning, effort in PROMPT_MATRIX:
            with self.subTest(prompt=prompt):
                scored = score_prompt(prompt, self.config)
                recommendation = recommend(
                    catalogs, self.family_key, scored.profile, scored.reasoning,
                    self.config, provider="claude",
                )
                self.assertEqual(PROFILE_TO_ALIAS[profile], recommendation.selection.model.slug)
                self.assertEqual(effort, recommendation.selection.reasoning)
                self.assertEqual((), recommendation.counterparts)


class ProfileCoverageTests(unittest.TestCase):
    """No profile may resolve to a model by catalog order alone.

    `priority` is display metadata with no capability meaning, so whenever the configured
    criteria match nothing it silently becomes the decision procedure - and its direction
    is set by whichever model the catalog happens to list first.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def _assert_every_profile_has_evidence(self, models, family) -> None:
        for profile in PROFILE_TO_ALIAS:
            with self.subTest(profile=profile):
                selection = select_model(models, profile, "medium", self.config, family=family)
                self.assertIsNotNone(selection.model)
                self.assertNotEqual(NO_EVIDENCE, selection.evidence)

    def test_claude_tiers_cover_every_profile(self) -> None:
        family_key = default_family(self.config, "claude")
        self._assert_every_profile_has_evidence(
            load_claude_catalog(config=self.config).models,
            self.config["providers"]["claude"]["families"][family_key],
        )

    def test_codex_families_cover_every_profile(self) -> None:
        openai = (
            ModelInfo("gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work.",
                      ("low", "medium", "high", "xhigh", "max"), 1),
            ModelInfo("gpt-5.6-sol", "GPT-5.6-Sol", "Latest frontier agentic coding model.",
                      ("low", "medium", "high", "xhigh", "max"), 1),
            ModelInfo("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work.",
                      ("low", "medium", "high", "xhigh", "max"), 7),
            ModelInfo("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model.",
                      ("low", "medium", "high", "xhigh", "max"), 8),
        )
        families = self.config["providers"]["codex"]["families"]
        self._assert_every_profile_has_evidence(openai, families["openai"])
        deepseek = (
            ModelInfo("deepseek-flash", "deepseek-flash", "Latest frontier agentic coding model with image input.",
                      ("low", "high", "max"), 1),
            ModelInfo("deepseek-v4-pro", "deepseek-v4-pro", "Most capable frontier agentic coding model.",
                      ("low", "high", "max"), 2),
        )
        self._assert_every_profile_has_evidence(deepseek, families["deepseek"])

    def test_unpinned_catalog_reports_that_it_had_no_evidence(self) -> None:
        """The guard must be able to fail, or it is not a guard."""
        opaque = (
            ModelInfo("model-a", "Model A", "an opaque model", ("low", "medium"), 1),
            ModelInfo("model-b", "Model B", "another opaque model", ("low", "medium"), 2),
        )
        selection = select_model(opaque, "MAX", "medium", self.config, family={})
        self.assertEqual(NO_EVIDENCE, selection.evidence)


class ClaudeTierTableTests(unittest.TestCase):
    """The tier table and the profile pins are two statements about the same thing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)
        cls.provider = cls.config["providers"]["claude"]
        cls.pins = cls.provider["families"][default_family(cls.config, "claude")]["profile_models"]

    def test_declared_tier_matches_the_pin_that_actually_routes(self) -> None:
        declared = {alias: values["profile"] for alias, values in self.provider["verified_alias_families"].items()}
        pinned = {alias: profile for profile, aliases in self.pins.items() for alias in aliases}
        self.assertEqual(declared, pinned)

    def test_pins_match_the_expected_tier_order(self) -> None:
        self.assertEqual({profile: [alias] for profile, alias in PROFILE_TO_ALIAS.items()}, self.pins)

    def test_declared_efforts_are_a_subset_of_the_reasoning_ladder(self) -> None:
        ladder = set(self.config["reasoning_order"])
        self.assertTrue(set(self.provider["default_efforts"]) <= ladder)

    def test_every_directive_level_has_a_place_on_the_ladder(self) -> None:
        ladder = {level.upper() for level in self.config["reasoning_order"]}
        for level in self.config["directives"]["reasoning"]:
            with self.subTest(level=level):
                self.assertIn(level, ladder)


class ClaudeCatalogDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def test_default_path_reads_no_subprocess(self) -> None:
        with patch("adaptive_model_router.catalog.subprocess.run") as run:
            result = load_claude_catalog(config=self.config)
        run.assert_not_called()
        self.assertEqual("AVAILABLE", result.status)
        self.assertEqual(list(PROFILE_TO_ALIAS.values()), [model.slug for model in result.models])

    def test_refresh_keeps_tiers_the_help_examples_omit(self) -> None:
        """`--help` names example aliases, not the alias set; haiku is absent from it."""
        with _patch_discovery(), patch(
            "adaptive_model_router.catalog.subprocess.run", return_value=_completed(REAL_HELP)
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertEqual("AVAILABLE", result.status)
        self.assertIn("haiku", [model.slug for model in result.models])
        self.assertEqual(list(PROFILE_TO_ALIAS.values()), [model.slug for model in result.models])

    def test_a_tier_the_build_names_but_cannot_resolve_is_not_offered(self) -> None:
        """`mythos` is in the build's tier list with no default model id behind it."""
        with _patch_discovery(), patch(
            "adaptive_model_router.catalog.subprocess.run", return_value=_completed(REAL_HELP)
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertNotIn("mythos", [model.slug for model in result.models])

    def test_a_new_tier_is_discovered_from_the_build_not_from_help(self) -> None:
        """A tier added by a Claude Code update appears without editing --help parsing."""
        future = INSTALLED_TIERS + (ClaudeTier("solaris", "For very long horizons", "claude-solaris-1"),)
        with _patch_discovery(future), patch(
            "adaptive_model_router.catalog.subprocess.run", return_value=_completed(REAL_HELP)
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertEqual(list(PROFILE_TO_ALIAS.values()) + ["solaris"], [m.slug for m in result.models])
        self.assertIn("solaris", result.message)
        self.assertIn("does not classify", result.message)

    def test_a_newly_discovered_tier_never_takes_over_a_profile(self) -> None:
        """Discovery adds candidates; only a human-assigned profile makes one route."""
        future = INSTALLED_TIERS + (ClaudeTier("solaris", "For very long horizons", "claude-solaris-1"),)
        with _patch_discovery(future), patch(
            "adaptive_model_router.catalog.subprocess.run", return_value=_completed(REAL_HELP)
        ):
            catalog = load_claude_catalog(config=self.config, refresh=True)
        family = self.config["providers"]["claude"]["families"][default_family(self.config, "claude")]
        for profile, alias in PROFILE_TO_ALIAS.items():
            with self.subTest(profile=profile):
                selection = select_model(catalog.models, profile, "medium", self.config, family=family)
                self.assertEqual(alias, selection.model.slug)

    def test_refresh_adopts_the_advertised_effort_ladder(self) -> None:
        help_text = REAL_HELP.replace("(low, medium, high, xhigh, max)", "(low, high, max)")
        with _patch_discovery(), patch(
            "adaptive_model_router.catalog.subprocess.run", return_value=_completed(help_text)
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        for model in result.models:
            self.assertEqual(("low", "high", "max"), model.efforts)

    def test_unparseable_help_keeps_the_configured_tiers(self) -> None:
        """A help text that stops advertising aliases is not a reason to stop routing."""
        with _patch_discovery(), patch(
            "adaptive_model_router.catalog.subprocess.run", return_value=_completed("usage: claude\n")
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertEqual("AVAILABLE", result.status)
        self.assertEqual(list(PROFILE_TO_ALIAS.values()), [model.slug for model in result.models])
        self.assertIn("advertises no model aliases", result.message)

    def test_authentication_error_is_reported_rather_than_routed_around(self) -> None:
        with patch(
            "adaptive_model_router.catalog.subprocess.run",
            return_value=_completed("", returncode=1, stderr="AUTH_ERROR: login required"),
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertEqual("AUTH_REQUIRED", result.status)

    def test_a_failed_cli_call_is_unknown_not_empty_success(self) -> None:
        with patch(
            "adaptive_model_router.catalog.subprocess.run",
            return_value=_completed("", returncode=2, stderr="command failed"),
        ):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertEqual("UNKNOWN", result.status)
        self.assertEqual((), result.models)

    def test_a_missing_executable_is_unknown(self) -> None:
        with patch("adaptive_model_router.catalog.subprocess.run", side_effect=OSError("not found")):
            result = load_claude_catalog(config=self.config, refresh=True)
        self.assertEqual("UNKNOWN", result.status)

    def test_a_configuration_without_tiers_is_unknown(self) -> None:
        self.assertEqual("UNKNOWN", load_claude_catalog(config={}).status)


# The shapes the real Claude Code build carries, including the decoy that broke the first
# version of the extractor: a single-entry object that a looser pattern matched first.
BUILD_TIER_NAMES = b'ANTHROPIC_TIER_NAMES=["sonnet","opus","haiku","fable","mythos"];'
BUILD_TIER_DESCRIPTIONS = (
    b'TIER_DESCRIPTIONS={haiku:"Fastest for quick answers",'
    b'sonnet:"Most efficient for everyday tasks",'
    b'opus:"Most capable for ambitious work",'
    b'fable:"For your toughest challenges"};'
)
BUILD_TIER_DEFAULTS = (
    b'{fable:"claude-fable-5-1",opus:"claude-opus-5",'
    b'sonnet:"claude-sonnet-5",haiku:"claude-haiku-4-5"}'
)
BUILD_DECOY = b'var fallback={default:"claude-haiku-4-5"};'


class BuildTierExtractionTests(unittest.TestCase):
    """Reading the tier table out of the installed Claude Code executable."""

    def _write_build(self, directory: str, padding: int = 0, body: bytes | None = None) -> Path:
        path = Path(directory) / "claude.exe"
        blob = body if body is not None else (
            BUILD_DECOY + BUILD_TIER_NAMES + BUILD_TIER_DESCRIPTIONS + BUILD_TIER_DEFAULTS
        )
        path.write_bytes(b"\0" * padding + blob)
        return path

    def _discover(self, path: Path, state: str) -> tuple:
        with patch.dict("os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": state}), patch(
            "adaptive_model_router.catalog.resolve_command", return_value=str(path),
        ):
            return discover_claude_tiers(refresh=True)

    def test_every_tier_and_its_resolved_model_id_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tiers = self._discover(self._write_build(directory), directory)
        self.assertEqual(
            {
                "sonnet": "claude-sonnet-5", "opus": "claude-opus-5",
                "haiku": "claude-haiku-4-5", "fable": "claude-fable-5-1", "mythos": "",
            },
            {tier.alias: tier.model_id for tier in tiers},
        )
        self.assertEqual("Fastest for quick answers", next(t for t in tiers if t.alias == "haiku").description)

    def test_a_single_entry_decoy_object_is_not_read_as_the_tier_defaults(self) -> None:
        """`{default:"claude-haiku-4-5"}` appears earlier in the real build."""
        with tempfile.TemporaryDirectory() as directory:
            body = BUILD_DECOY + BUILD_TIER_NAMES + BUILD_TIER_DESCRIPTIONS
            tiers = self._discover(self._write_build(directory, body=body), directory)
        # No defaults object at all is the honest answer; a wrong one would report a
        # confident version for every tier.
        self.assertEqual({""}, {tier.model_id for tier in tiers})

    def test_a_table_straddling_a_read_boundary_is_still_found(self) -> None:
        """The scan reads in windows; without overlap a match on the seam disappears.

        The padding is computed so the tier-name literal is cut in half by the 8 MiB read
        boundary. An approximate offset is not enough: land the literal one byte past the
        seam and it sits wholly inside the next window, so the test passes with the
        overlap removed - which is how the first version of this test behaved.
        """
        chunk = 8 << 20
        padding = chunk - len(BUILD_DECOY) - len(BUILD_TIER_NAMES) // 2
        self.assertLess(padding + len(BUILD_DECOY), chunk, "names literal must start before the seam")
        self.assertGreater(
            padding + len(BUILD_DECOY) + len(BUILD_TIER_NAMES), chunk,
            "names literal must end after the seam",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_build(directory, padding=padding)
            tiers = self._discover(path, directory)
        self.assertEqual(5, len(tiers))
        self.assertEqual("claude-opus-5", next(t for t in tiers if t.alias == "opus").model_id)

    def test_a_build_without_the_table_yields_nothing_rather_than_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_build(directory, body=b"an unrelated executable")
            self.assertEqual((), self._discover(path, directory))

    def test_a_huge_transcript_is_read_from_its_end(self) -> None:
        """Session transcripts reach tens of megabytes and the hook has a 10 s budget."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.jsonl"
            filler = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 400}})
            with path.open("w", encoding="utf-8") as handle:
                for _ in range(4000):
                    handle.write(filler + "\n")
                handle.write(json.dumps(
                    {"type": "assistant", "message": {"role": "assistant", "model": "claude-fable-5-1"}}) + "\n")
            self.assertGreater(path.stat().st_size, 1 << 20)
            started = time.monotonic()
            model = claude_model_from_transcript(path)
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual("claude-fable-5-1", model)

    def test_a_transcript_whose_last_turn_is_older_than_the_tail_reports_nothing(self) -> None:
        """Reading the end means an answer from the end; it never guesses from the start.

        The old entry goes on the *third* line, not the first: the reader drops the first
        line of its window because a seek lands mid-line, so an entry on line one is
        discarded whether the window starts at the seek point or at byte zero, and the test
        passes with the seek removed - which is how the first version of this test behaved.
        """
        filler = json.dumps({"type": "user", "message": {"role": "user", "content": "y" * 400}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stale.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                handle.write(filler + "\n")
                handle.write(filler + "\n")
                handle.write(json.dumps(
                    {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5"}}) + "\n")
                for _ in range(4000):
                    handle.write(filler + "\n")
            # The entry must sit outside the tail window, or this tests nothing.
            self.assertGreater(path.stat().st_size - 3 * (len(filler) + 1), 262144)
            self.assertIsNone(claude_model_from_transcript(path))

    def test_a_missing_or_malformed_transcript_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent.jsonl"
            broken = Path(directory) / "broken.jsonl"
            broken.write_text("not json\n{\"type\": \"assistant\"}\n", encoding="utf-8")
            self.assertIsNone(claude_model_from_transcript(missing))
            self.assertIsNone(claude_model_from_transcript(broken))
            self.assertIsNone(claude_model_from_transcript(None))

    def test_a_model_id_resolves_to_the_tier_alias(self) -> None:
        config = load_config(PACKAGED_CONFIG)
        with _patch_discovery():
            self.assertEqual("sonnet", claude_alias_for_model("claude-sonnet-5", config))
            self.assertEqual("fable", claude_alias_for_model("claude-fable-5-1", config))
            # A version the installed build does not know still resolves by tier name.
            self.assertEqual("opus", claude_alias_for_model("claude-opus-9", config))
            self.assertIsNone(claude_alias_for_model("some-other-vendor-model", config))
            self.assertIsNone(claude_alias_for_model(None, config))

    def test_a_missing_executable_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual((), self._discover(Path(directory) / "absent.exe", directory))

    def test_the_result_is_cached_against_the_build_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_build(directory)
            self._discover(path, directory)
            with patch.dict("os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}), patch(
                "adaptive_model_router.catalog.resolve_command", return_value=str(path),
            ), patch("adaptive_model_router.catalog._tiers_from_build") as scan:
                cached = discover_claude_tiers()
            scan.assert_not_called()
            self.assertEqual(5, len(cached))

    def test_an_updated_build_invalidates_the_cache(self) -> None:
        """A Claude Code update must not be served from the previous build's table."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_build(directory)
            self._discover(path, directory)
            updated = BUILD_DECOY + BUILD_TIER_NAMES.replace(b',"mythos"', b"") + BUILD_TIER_DESCRIPTIONS
            path.write_bytes(updated + BUILD_TIER_DEFAULTS)
            with patch.dict("os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}), patch(
                "adaptive_model_router.catalog.resolve_command", return_value=str(path),
            ):
                tiers = discover_claude_tiers()
        self.assertEqual(["sonnet", "opus", "haiku", "fable"], [tier.alias for tier in tiers])


class ModelListingTests(unittest.TestCase):
    """`--list-models` is the pre-install check; it must not disagree with the router."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def _run(self, argv: list[str], tiers=INSTALLED_TIERS) -> str:
        output = StringIO()
        with patch("adaptive_model_router.cli.discover_claude_tiers", return_value=tiers), \
                redirect_stdout(output):
            self.assertEqual(0, cli_main(argv))
        return output.getvalue()

    def test_every_tier_is_listed_with_its_version_and_profile(self) -> None:
        printed = self._run(["--provider", "claude", "--list-models"])
        for alias, profile in (
            ("haiku", "FAST"), ("sonnet", "BALANCED"), ("opus", "STRONG"), ("fable", "MAX"),
        ):
            with self.subTest(alias=alias):
                row = next(line for line in printed.splitlines() if line.startswith(alias))
                self.assertIn(profile, row)
                self.assertIn(INSTALLED_TIERS_BY_ALIAS[alias].model_id, row)
        self.assertIn("모든 선택 가능한 Tier에 Profile이 지정되어 있습니다", printed)

    def test_a_tier_with_no_default_model_is_marked_not_selectable(self) -> None:
        printed = self._run(["--provider", "claude", "--list-models"])
        row = next(line for line in printed.splitlines() if line.startswith("mythos"))
        self.assertIn("not selectable", row)

    def test_a_new_tier_is_listed_and_flagged_for_assignment(self) -> None:
        future = INSTALLED_TIERS + (ClaudeTier("solaris", "For very long horizons", "claude-solaris-1"),)
        printed = self._run(["--provider", "claude", "--list-models"], future)
        row = next(line for line in printed.splitlines() if line.startswith("solaris"))
        self.assertIn("claude-solaris-1", row)
        self.assertIn("NEW", row)
        self.assertIn("분류되지 않은 신규 Tier: solaris", printed)

    def test_a_newer_version_behind_the_same_alias_needs_no_configuration_change(self) -> None:
        """The whole point of routing by tier alias: opus 5 -> opus 6 is transparent."""
        upgraded = tuple(
            ClaudeTier(tier.alias, tier.description, tier.model_id.replace("-5", "-6"))
            for tier in INSTALLED_TIERS
        )
        printed = self._run(["--provider", "claude", "--list-models"], upgraded)
        self.assertIn("claude-opus-6", printed)
        self.assertIn("모든 선택 가능한 Tier에 Profile이 지정되어 있습니다", printed)

    def test_the_listed_profile_matches_the_model_the_router_would_pick(self) -> None:
        """A listing that re-derives classification drifts from the ranker; this catches it."""
        family = self.config["providers"]["claude"]["families"][default_family(self.config, "claude")]
        models = load_claude_catalog(config=self.config).models
        for profile, alias in PROFILE_TO_ALIAS.items():
            with self.subTest(profile=profile):
                model = next(item for item in models if item.slug == alias)
                self.assertEqual(profile, classify_model(model, self.config, family)[0])
                selection = select_model(models, profile, "medium", self.config, family=family)
                self.assertEqual(alias, selection.model.slug)

    def test_a_secondary_keyword_does_not_outrank_a_defining_one(self) -> None:
        """"Our most capable model for complex, demanding work" answers to STRONG's weak
        hints and to MAX's defining one; only comparing the scores picks MAX."""
        astra = ModelInfo(
            "gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work.",
            ("low", "high"), 1,
        )
        family = self.config["providers"]["codex"]["families"]["openai"]
        self.assertEqual("MAX", classify_model(astra, self.config, family)[0])

    def test_an_unclassified_model_is_reported_as_such(self) -> None:
        opaque = ModelInfo("model-x", "Model X", "an opaque model", ("low",), 1)
        self.assertEqual(("", NO_EVIDENCE), classify_model(opaque, self.config, {}))


class ClaudeEffortMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)
        cls.family = cls.config["providers"]["claude"]["families"][default_family(cls.config, "claude")]

    def test_every_ladder_level_maps_onto_the_claude_efforts(self) -> None:
        # Claude Code exposes low..max; ultra exists for Codex only and must clamp down.
        expected = {
            "LOW": "low", "MEDIUM": "medium", "HIGH": "high",
            "XHIGH": "xhigh", "MAX": "max", "ULTRA": "max",
        }
        models = load_claude_catalog(config=self.config).models
        for requested, effort in expected.items():
            with self.subTest(requested=requested):
                selection = select_model(models, "BALANCED", requested, self.config, family=self.family)
                self.assertEqual(effort, selection.reasoning)

    def test_a_clamped_effort_says_so(self) -> None:
        models = load_claude_catalog(config=self.config).models
        selection = select_model(models, "BALANCED", "ULTRA", self.config, family=self.family)
        self.assertIn("ULTRA is unsupported", selection.note)

    def test_an_unclamped_effort_adds_no_note(self) -> None:
        models = load_claude_catalog(config=self.config).models
        selection = select_model(models, "BALANCED", "HIGH", self.config, family=self.family)
        self.assertEqual("", selection.note)

    def test_a_reduced_ladder_rounds_up_rather_than_down(self) -> None:
        reduced = (ModelInfo("sonnet", "sonnet", "Most efficient for everyday tasks", ("low", "high", "max"), 1),)
        selection = select_model(reduced, "BALANCED", "MEDIUM", self.config, family=self.family)
        self.assertEqual("high", selection.reasoning)


class ClaudeDegradedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)
        cls.family = cls.config["providers"]["claude"]["families"][default_family(cls.config, "claude")]
        cls.models = load_claude_catalog(config=cls.config).models

    def test_an_unavailable_tier_falls_back_inside_the_provider(self) -> None:
        selection = select_model(
            self.models, "MAX", "XHIGH", self.config, family=self.family,
            statuses={"fable": {"status": "USAGE_LIMIT_REACHED", "reset": "2026-09-18 09:00"}},
        )
        self.assertEqual("opus", selection.model.slug)
        self.assertEqual("fable", selection.fallback_from)
        self.assertEqual("2026-09-18 09:00", selection.reset)

    def test_every_tier_unavailable_is_reported_rather_than_guessed(self) -> None:
        statuses = {alias: {"status": "USAGE_LIMIT_REACHED"} for alias in PROFILE_TO_ALIAS.values()}
        selection = select_model(
            self.models, "BALANCED", "MEDIUM", self.config, family=self.family, statuses=statuses,
        )
        self.assertEqual("UNAVAILABLE", selection.status)
        self.assertIsNone(selection.model)

    def test_the_current_tier_is_kept_only_when_it_is_the_right_tier(self) -> None:
        kept = select_model(
            self.models, "BALANCED", "MEDIUM", self.config, current_model="sonnet", family=self.family,
        )
        self.assertEqual("sonnet", kept.model.slug)
        # "Most capable for ambitious work" is the CLI's wording for opus, which reads as
        # the MAX hint; the pin, not the wording, decides that opus is the STRONG tier.
        upgraded = select_model(
            self.models, "MAX", "XHIGH", self.config, current_model="opus", family=self.family,
        )
        self.assertEqual("fable", upgraded.model.slug)

    def test_keep_current_model_directive_overrides_the_tier(self) -> None:
        forced = select_model(
            self.models, "MAX", "XHIGH", self.config, current_model="haiku",
            family=self.family, force_current=True,
        )
        self.assertEqual("haiku", forced.model.slug)


class ClaudeHookMatrixTests(unittest.TestCase):
    """The hook is the only surface that runs before a real Claude Code turn."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PACKAGED_CONFIG)

    def _evaluate(self, payload: dict, directory: str, current_model: str | None = "sonnet") -> dict:
        # tests/__init__.py clears CLAUDE_CODE_ENTRYPOINT for the whole suite, so an unset
        # variable here means "terminal", which is Claude Code's own default reading.
        with patch.dict("os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}), patch(
            "adaptive_model_router.hook.load_current_claude_config", return_value=(current_model, None),
        ):
            return evaluate_hook(payload, self.config, provider="claude")

    def test_a_user_prompt_is_blocked_with_the_recommended_tier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = self._evaluate(
                {"session_id": "s", "prompt": "전체 프로젝트 보안 취약점을 점검하고 수정해줘", "source": "user"},
                directory,
            )
        self.assertEqual("block", response["decision"])
        self.assertIn("Recommended: fable", response["reason"])
        self.assertIn("Effort: XHIGH", response["reason"])

    def test_resubmitting_the_same_prompt_is_the_approval(self) -> None:
        payload = {"session_id": "s", "prompt": "README 오타 수정해줘", "source": "user"}
        with tempfile.TemporaryDirectory() as directory:
            first = self._evaluate(payload, directory)
            second = self._evaluate(payload, directory)
        self.assertEqual("block", first["decision"])
        self.assertIn("Recommended: haiku", first["reason"])
        self.assertEqual({"continue": True}, second)

    def test_an_expired_approval_blocks_again(self) -> None:
        payload = {"session_id": "s", "prompt": "README 오타 수정해줘", "source": "user"}
        expired = dict(self.config, hook=dict(self.config["hook"], approval_ttl_seconds=0))
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch("adaptive_model_router.hook.load_current_claude_config", return_value=("sonnet", None)):
            evaluate_hook(payload, expired, provider="claude")
            second = evaluate_hook(payload, expired, provider="claude")
        self.assertEqual("block", second["decision"])

    def test_a_different_prompt_is_not_approved_by_the_previous_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._evaluate({"session_id": "s", "prompt": "README 오타 수정해줘", "source": "user"}, directory)
            other = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"}, directory,
            )
        self.assertEqual("block", other["decision"])
        self.assertIn("Recommended: opus", other["reason"])

    def _transcript(self, directory: str, *models: str) -> str:
        """A session transcript in the shape Claude Code writes."""
        path = Path(directory) / "session.jsonl"
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})]
        for model in models:
            lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "model": model}}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_the_current_model_is_read_from_the_session_transcript(self) -> None:
        """The only live source: no payload field and no CLAUDE_* variable carries it."""
        with tempfile.TemporaryDirectory() as directory, _patch_discovery():
            transcript = self._transcript(directory, "claude-opus-5", "claude-sonnet-5")
            response = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘",
                 "transcript_path": transcript, "source": "user"},
                directory, current_model=None,
            )
        # Reported as the tier alias the router routes by, not the raw id.
        self.assertIn("Current Model: sonnet (last turn, from the session transcript)", response["reason"])

    def test_the_transcript_beats_a_pinned_setting(self) -> None:
        """A /model switch changes what runs; settings.json may still say something else."""
        with tempfile.TemporaryDirectory() as directory, _patch_discovery():
            transcript = self._transcript(directory, "claude-opus-5")
            response = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "transcript_path": transcript},
                directory, current_model="haiku",
            )
        self.assertIn("Current Model: opus (last turn", response["reason"])

    def test_a_pinned_model_is_used_when_the_transcript_has_no_turn_yet(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _patch_discovery():
            transcript = self._transcript(directory)  # user turn only
            response = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "transcript_path": transcript},
                directory, current_model="sonnet",
            )
        self.assertIn("Current Model: sonnet (pinned in settings.json)", response["reason"])

    def test_the_first_prompt_of_a_session_says_so_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _patch_discovery():
            response = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "transcript_path": ""},
                directory, current_model=None,
            )
        self.assertIn("Current Model: UNKNOWN (first prompt of the session", response["reason"])

    def test_a_session_already_on_the_right_tier_is_told_to_keep_it(self) -> None:
        """Resolving to an alias is what makes this comparison possible at all."""
        with tempfile.TemporaryDirectory() as directory, _patch_discovery():
            transcript = self._transcript(directory, "claude-sonnet-5")
            response = self._evaluate(
                {"session_id": "s", "prompt": "API 연동 기능 구현해줘", "transcript_path": transcript},
                directory, current_model=None,
            )
        self.assertIn("Current Model: sonnet", response["reason"])
        self.assertIn("Recommended: sonnet", response["reason"])

    def test_only_terminal_surfaces_are_interrupted(self) -> None:
        """One settings.json registration fires on every Claude Code surface.

        Entrypoints measured on 2.1.274: the desktop app reports "claude-desktop" and
        `claude --print` reports "sdk-cli"; a plain terminal leaves the variable unset,
        which Claude Code itself reads as "cli".
        """
        cases = {
            "cli": "block", "ssh-remote": "block", "claude-coworker-terminal": "block",
            "claude-desktop": "continue", "claude-desktop-3p": "continue",
            "claude-vscode": "continue", "sdk-ts": "continue", "sdk-py": "continue",
            "sdk-cli": "continue", "remote_desktop": "continue", "remote_mobile": "continue",
            "claude-in-teams": "continue", "claude_in_slack": "continue",
            "local-agent": "continue", "mcp": "continue",
            # A surface Anthropic adds later is not in the allow-list, so it is left alone.
            "some-future-surface": "continue",
        }
        for entrypoint, expected in cases.items():
            with self.subTest(entrypoint=entrypoint), tempfile.TemporaryDirectory() as directory:
                with patch.dict("os.environ", {"CLAUDE_CODE_ENTRYPOINT": entrypoint}):
                    response = self._evaluate(
                        {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                        directory,
                    )
                self.assertEqual(expected, response.get("decision", "continue"))

    def test_an_unset_entrypoint_is_treated_as_the_terminal(self) -> None:
        """A plain `claude` session sets nothing; Claude Code's own default is "cli"."""
        with tempfile.TemporaryDirectory() as directory:
            environment = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ENTRYPOINT"}
            with patch.dict("os.environ", environment, clear=True):
                response = self._evaluate(
                    {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"}, directory,
                )
        self.assertEqual("block", response["decision"])

    def _with_modes(self, **modes: str) -> dict:
        return dict(self.config, hook=dict(self.config["hook"], claude_modes=modes))

    def test_the_mode_of_each_surface_is_configurable(self) -> None:
        config = self._with_modes(**{"claude-desktop": "block"})
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory, "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"},
        ), patch("adaptive_model_router.hook.load_current_claude_config", return_value=("sonnet", None)):
            response = evaluate_hook(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                config, provider="claude",
            )
        self.assertEqual("block", response["decision"])

    def test_a_gui_surface_is_advised_without_being_interrupted(self) -> None:
        """A desktop session has no way to approve by resubmitting, but can still be told."""
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory, "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"},
        ), patch("adaptive_model_router.hook.load_current_claude_config", return_value=(None, None)):
            response = evaluate_hook(
                {"session_id": "s", "prompt": "전체 프로젝트 보안 취약점을 점검하고 수정해줘", "source": "user"},
                self.config, provider="claude",
            )
        self.assertTrue(response["continue"])
        self.assertNotIn("decision", response)
        self.assertIn("Recommended: fable", response["systemMessage"])
        self.assertIn("RECOMMENDATION (not applied)", response["systemMessage"])
        self.assertNotIn("승인 방법", response["systemMessage"])

    def test_advising_stores_no_approval_because_nothing_was_withheld(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory, "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"},
        ), patch("adaptive_model_router.hook.load_current_claude_config", return_value=(None, None)):
            evaluate_hook(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                self.config, provider="claude",
            )
            self.assertEqual([], list(Path(directory).rglob("*.json")))

    def test_an_unnamed_surface_falls_back_to_the_configured_default(self) -> None:
        for fallback, expected in (("off", "continue"), ("advise", "continue"), ("block", "block")):
            with self.subTest(fallback=fallback), tempfile.TemporaryDirectory() as directory:
                config = self._with_modes(**{"*": fallback})
                with patch.dict(
                    "os.environ",
                    {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory,
                     "CLAUDE_CODE_ENTRYPOINT": "surface-invented-tomorrow"},
                ), patch(
                    "adaptive_model_router.hook.load_current_claude_config", return_value=(None, None),
                ):
                    response = evaluate_hook(
                        {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                        config, provider="claude",
                    )
                self.assertEqual(expected, response.get("decision", "continue"))

    def test_an_unrecognised_mode_value_is_treated_as_off(self) -> None:
        """A typo must not silently mean "block" on a surface that cannot approve."""
        config = self._with_modes(**{"cli": "blcok"})
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory},
        ), patch("adaptive_model_router.hook.load_current_claude_config", return_value=(None, None)):
            response = evaluate_hook(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘"}, config, provider="claude",
            )
        self.assertEqual({"continue": True}, response)

    def test_a_session_already_on_the_recommendation_is_not_interrupted(self) -> None:
        """Blocking to confirm what is already set teaches the user the screen is noise."""
        with tempfile.TemporaryDirectory() as directory, _patch_discovery(), patch.dict(
            "os.environ", {"CLAUDE_EFFORT": "MEDIUM"},
        ):
            transcript = self._transcript(directory, "claude-sonnet-5")
            response = self._evaluate(
                {"session_id": "s", "prompt": "API 연동 기능 구현해줘", "transcript_path": transcript},
                directory, current_model=None,
            )
        self.assertEqual({"continue": True}, response)

    def test_a_mismatch_in_either_half_still_interrupts(self) -> None:
        cases = {"LOW": "effort differs", "MEDIUM": "both match"}
        for effort, _label in cases.items():
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as directory, \
                    _patch_discovery(), patch.dict("os.environ", {"CLAUDE_EFFORT": effort}):
                transcript = self._transcript(directory, "claude-sonnet-5")
                response = self._evaluate(
                    {"session_id": "s", "prompt": "API 연동 기능 구현해줘", "transcript_path": transcript},
                    directory, current_model=None,
                )
                expected = "continue" if effort == "MEDIUM" else "block"
                self.assertEqual(expected, response.get("decision", "continue"))

    def test_an_unknown_current_setting_is_not_treated_as_a_match(self) -> None:
        """Absence is not agreement: the first prompt of a session must still be routed."""
        with tempfile.TemporaryDirectory() as directory, _patch_discovery():
            environment = {k: v for k, v in os.environ.items() if k != "CLAUDE_EFFORT"}
            environment["ADAPTIVE_MODEL_ROUTER_STATE_DIR"] = directory
            with patch.dict("os.environ", environment, clear=True), patch(
                "adaptive_model_router.hook.load_current_claude_config", return_value=("sonnet", None),
            ):
                response = evaluate_hook(
                    {"session_id": "s", "prompt": "API 연동 기능 구현해줘"}, self.config, provider="claude",
                )
        self.assertEqual("block", response["decision"])

    def test_the_codex_provider_is_not_gated_on_the_claude_entrypoint(self) -> None:
        """Codex reaches the router through a patched terminal TUI, never through a GUI."""
        model = ModelInfo("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model.",
                          ("low", "medium"), 1)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory, "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"},
        ), patch(
            "adaptive_model_router.hook.resolve_active_catalogs",
            return_value=("openai", {"openai": CatalogResult("AVAILABLE", (model,))}),
        ):
            response = evaluate_hook(
                {"session_id": "s", "prompt": "README 오타 수정해줘", "model": "gpt-5.6-luna"},
                self.config,
            )
        self.assertEqual("block", response["decision"])

    def test_the_current_effort_is_read_from_the_environment(self) -> None:
        """The payload has no effort field, but Claude Code exports CLAUDE_EFFORT."""
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory,
             "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_EFFORT": "max"},
        ), patch("adaptive_model_router.hook.load_current_claude_config", return_value=("sonnet", None)):
            response = evaluate_hook(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                self.config, provider="claude",
            )
        self.assertIn("Current Effort: MAX", response["reason"])

    def test_a_missing_effort_variable_says_unknown_rather_than_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {k: v for k, v in os.environ.items() if k != "CLAUDE_EFFORT"}
            environment.update(ADAPTIVE_MODEL_ROUTER_STATE_DIR=directory, CLAUDE_CODE_ENTRYPOINT="cli")
            with patch.dict("os.environ", environment, clear=True), patch(
                "adaptive_model_router.hook.load_current_claude_config", return_value=("sonnet", None),
            ):
                response = evaluate_hook(
                    {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                    self.config, provider="claude",
                )
        self.assertIn("Current Effort: UNKNOWN", response["reason"])

    def test_prompts_the_user_did_not_type_pass_straight_through(self) -> None:
        # Blocking these stalls work with nobody present to answer the confirmation.
        for source in ("sdk", "system", "loop_wakeup", "schedule_wakeup", "poll_event", "resume"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                response = self._evaluate(
                    {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": source}, directory,
                )
                self.assertEqual({"continue": True}, response)

    def test_a_payload_without_a_source_is_treated_as_typed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = self._evaluate({"session_id": "s", "prompt": "프로젝트 전체를 분석해줘"}, directory)
        self.assertEqual("block", response["decision"])

    def test_an_empty_prompt_is_not_routed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual({"continue": True}, self._evaluate({"session_id": "s", "prompt": "   "}, directory))

    def test_an_unpinned_session_model_still_routes(self) -> None:
        """settings.json carries a model only when the user pinned one."""
        with tempfile.TemporaryDirectory() as directory:
            response = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                directory, current_model=None,
            )
        self.assertEqual("block", response["decision"])
        self.assertIn("Current Model: UNKNOWN", response["reason"])
        self.assertIn("Recommended: opus", response["reason"])

    def test_the_block_message_names_no_counterpart_for_claude(self) -> None:
        """Claude Code has one family; a comparison row would be inventing one."""
        with tempfile.TemporaryDirectory() as directory:
            response = self._evaluate(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"}, directory,
            )
        self.assertNotIn("Alternative", response["reason"])

    def test_abandoned_approvals_are_swept_when_the_next_one_is_written(self) -> None:
        """Only a consumed approval deletes itself, so the rest would accumulate forever."""
        with tempfile.TemporaryDirectory() as directory:
            self._evaluate({"session_id": "s", "prompt": "프로젝트 전체를 분석해줘"}, directory)
            stale = list(Path(directory).rglob("*.json"))
            self.assertEqual(1, len(stale))
            # Age the abandoned approval past the TTL, then write a different one.
            old = time.time() - 10_000
            os.utime(stale[0], (old, old))
            self._evaluate({"session_id": "s", "prompt": "API 연동 기능 구현해줘"}, directory)
            remaining = list(Path(directory).rglob("*.json"))
        self.assertEqual(1, len(remaining))
        self.assertNotIn(stale[0].name, [path.name for path in remaining])

    def test_approvals_left_by_earlier_sessions_are_swept_too(self) -> None:
        """Approvals are filed per session, so a sweep of only the current one never
        reaches the sessions that already ended - which is every session that leaked."""
        with tempfile.TemporaryDirectory() as directory:
            old_session = Path(directory) / "0123456789abcdef0123"
            old_session.mkdir()
            leaked = old_session / ("a" * 64 + ".json")
            leaked.write_text('{"created_at": 0}', encoding="utf-8")
            stale = time.time() - 10_000
            os.utime(leaked, (stale, stale))
            self._evaluate({"session_id": "new", "prompt": "프로젝트 전체를 분석해줘"}, directory)
            self.assertFalse(leaked.exists())
            self.assertFalse(old_session.exists(), "an emptied session directory must go too")
            self.assertEqual(1, len(list(Path(directory).rglob("*.json"))))

    def test_a_fresh_approval_is_not_swept_by_a_later_prompt(self) -> None:
        """The sweep must not eat the approval the user is about to resubmit."""
        with tempfile.TemporaryDirectory() as directory:
            first = {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘"}
            self._evaluate(first, directory)
            self._evaluate({"session_id": "s", "prompt": "API 연동 기능 구현해줘"}, directory)
            self.assertEqual(2, len(list(Path(directory).rglob("*.json"))))
            self.assertEqual({"continue": True}, self._evaluate(first, directory))

    def test_the_stored_approval_never_holds_the_prompt(self) -> None:
        secret = "이 문장은 저장되면 안 되는 내용입니다"
        with tempfile.TemporaryDirectory() as directory:
            self._evaluate({"session_id": "s", "prompt": secret, "source": "user"}, directory)
            stored = "".join(path.read_text(encoding="utf-8") for path in Path(directory).rglob("*.json"))
        self.assertNotIn(secret, stored)
        self.assertEqual({"created_at", "recommended_model", "recommended_reasoning", "score"},
                         set(json.loads(stored)))

    def test_a_broken_catalog_does_not_block_the_cli(self) -> None:
        """An advisory router that raises must not make Claude Code unusable."""
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ADAPTIVE_MODEL_ROUTER_STATE_DIR": directory}
        ), patch(
            "adaptive_model_router.hook.load_claude_catalog",
            return_value=CatalogResult("UNKNOWN", ()),
        ), patch(
            "adaptive_model_router.hook.load_current_claude_config", return_value=(None, None),
        ):
            response = evaluate_hook(
                {"session_id": "s", "prompt": "프로젝트 전체를 분석해줘", "source": "user"},
                self.config, provider="claude",
            )
        self.assertEqual("block", response["decision"])
        self.assertIn("KEEP CURRENT", response["reason"])


if __name__ == "__main__":
    unittest.main()
