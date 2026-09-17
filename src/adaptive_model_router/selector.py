from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .catalog import CatalogResult, ModelInfo


# Why a model ended up selected.  NO_EVIDENCE means nothing in the configuration
# said this model serves this profile and the ranking fell through to catalog
# order, which is display metadata and carries no capability meaning.
PINNED = "pinned"
SLUG_ROLE = "slug-role"
DESCRIPTION_HINT = "description-hint"
FALLBACK_PROFILE = "fallback-profile"
FORCED_CURRENT = "forced-current"
KEPT_CURRENT = "kept-current"
NO_EVIDENCE = "no-evidence"

# Cheapest to most capable; the order a listing walks and a tie is broken in.
PROFILE_ORDER = ("FAST", "BALANCED", "STRONG", "MAX")


@dataclass(frozen=True)
class Selection:
    model: ModelInfo | None
    reasoning: str
    original_reasoning: str
    status: str
    reset: str | None = None
    fallback_from: str | None = None
    note: str = ""
    evidence: str = NO_EVIDENCE


@dataclass(frozen=True)
class Counterpart:
    """The equivalent model in another family of the same provider, shown for comparison."""

    family: str
    label: str
    model: ModelInfo
    reasoning: str
    note: str = ""
    evidence: str = NO_EVIDENCE


@dataclass(frozen=True)
class Recommendation:
    family: str | None
    label: str
    selection: Selection
    counterparts: tuple[Counterpart, ...] = ()


def _hint_score(model: ModelInfo, hints: list[str]) -> int:
    description = model.description.casefold()
    # The first hint names the profile's defining trait (fast, balanced,
    # reliable, most capable).  Secondary hints add evidence without allowing
    # broad words such as "everyday" or "complex" to collapse adjacent tiers.
    if hints and hints[0].casefold() in description:
        return 100
    return sum(1 for hint in hints[1:] if hint.casefold() in description)


def _slug_role_score(model: ModelInfo, profile: str, config: dict[str, Any]) -> int:
    patterns = config["model_selection"].get("slug_role_patterns", {}).get(profile, [])
    return 100 if any(re.search(str(pattern), model.slug, re.I) for pattern in patterns) else 0


def _is_eligible(model: ModelInfo, config: dict[str, Any]) -> bool:
    excluded = config["model_selection"].get(
        "exclude_description_terms", ["previous-generation", "deprecated"],
    )
    description = model.description.casefold()
    return not any(str(term).casefold() in description for term in excluded)


def _preferred_slugs(profile: str, family: dict[str, Any] | None) -> list[str]:
    return [str(slug).casefold() for slug in (family or {}).get("profile_models", {}).get(profile, [])]


def _pinned_profiles(model: ModelInfo, family: dict[str, Any] | None) -> set[str]:
    """Every profile this family pins the model to; empty when the family pins nothing for it."""
    pins = (family or {}).get("profile_models", {})
    slug = model.slug.casefold()
    return {
        profile for profile, slugs in pins.items()
        if slug in [str(value).casefold() for value in slugs]
    }


def profile_evidence(
    model: ModelInfo, profile: str, config: dict[str, Any], family: dict[str, Any] | None = None,
) -> str:
    """What made this model a candidate for this profile - NO_EVIDENCE if nothing did.

    A ranker always returns a first element, so it cannot signal that it had no basis for
    choosing one; without this, an unmatched profile silently resolves to whatever the
    catalog happens to list first.
    """
    if model.slug.casefold() in _preferred_slugs(profile, family):
        return PINNED
    if _slug_role_score(model, profile, config):
        return SLUG_ROLE
    if _hint_score(model, config["model_selection"]["description_hints"].get(profile, [])):
        return DESCRIPTION_HINT
    return NO_EVIDENCE


def classify_model(
    model: ModelInfo, config: dict[str, Any], family: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """The profile(s) this model is configured to serve, and the evidence that says so.

    Deliberately reuses the ranker's own scoring rather than re-deriving it: a listing
    that asks "does any criterion match" instead of "which criterion wins" reports the
    wrong tier wherever a secondary keyword matches a neighbouring profile - the GPT model
    described as "most capable for complex, demanding work" answers to STRONG's weak hints
    and to MAX's defining one, and only the comparison picks MAX.
    """
    pinned = _pinned_profiles(model, family)
    if pinned:
        return "/".join(profile for profile in PROFILE_ORDER if profile in pinned), PINNED
    best_profile, best_score, best_evidence = "", 0, NO_EVIDENCE
    hints = config["model_selection"]["description_hints"]
    for profile in PROFILE_ORDER:
        slug_score = _slug_role_score(model, profile, config)
        hint_score = _hint_score(model, hints.get(profile, []))
        score = max(slug_score, hint_score)
        if score > best_score:
            best_profile = profile
            best_score = score
            best_evidence = SLUG_ROLE if slug_score >= hint_score else DESCRIPTION_HINT
    return (best_profile, best_evidence) if best_profile else ("", NO_EVIDENCE)


def _rank_for_profile(
    models: list[ModelInfo], profile: str, config: dict[str, Any], family: dict[str, Any] | None = None,
) -> list[ModelInfo]:
    hints = config["model_selection"]["description_hints"].get(profile, [])
    ranked = sorted(
        models,
        key=lambda item: (-max(_hint_score(item, hints), _slug_role_score(item, profile, config)), item.priority),
    )
    preferred = _preferred_slugs(profile, family)
    if not preferred:
        return ranked
    # A family may pin which model serves a profile when its descriptions carry no usable
    # hint; sorted() is stable, so unpinned models keep the hint ordering behind the pinned.
    return sorted(
        ranked,
        key=lambda item: preferred.index(item.slug.casefold())
        if item.slug.casefold() in preferred
        else len(preferred),
    )


def _fits_profile(
    model: ModelInfo, profile: str, config: dict[str, Any], family: dict[str, Any] | None = None,
) -> bool:
    # A family that pins a model to a profile has answered the question for that model:
    # the CLI's own tier wording often matches a neighbouring profile's hint (Claude's
    # "Most capable for ambitious work" reads as MAX while it is the STRONG tier), and
    # letting the hint win there keeps the wrong current model instead of upgrading it.
    pinned = _pinned_profiles(model, family)
    if pinned:
        return profile in pinned
    hints = config["model_selection"]["description_hints"].get(profile, [])
    # Keeping the current model on a secondary word caused Sol to satisfy
    # BALANCED via "everyday" and Astra to satisfy STRONG via "complex".
    # Require the defining trait when deciding whether the current model is
    # already the right tier.
    return bool(hints) and str(hints[0]).casefold() in model.description.casefold()


def _nearest_effort(
    requested: str, supported: tuple[str, ...], order: list[str], tie_break: str = "up",
) -> str:
    if requested in supported:
        return requested
    requested_index = order.index(requested) if requested in order else order.index("medium")
    candidates = [value for value in supported if value in order]
    if not candidates:
        return requested
    # DeepSeek exposes no MEDIUM, so the common BALANCED recommendation always lands on a
    # tie between LOW and HIGH. Rounding down would silently hand the everyday default the
    # provider's reduced mode, so ties round up unless the config says otherwise.
    prefer_lower = str(tie_break).casefold() == "down"
    return min(
        candidates,
        key=lambda value: (
            abs(order.index(value) - requested_index),
            (order.index(value) > requested_index) if prefer_lower else (order.index(value) < requested_index),
        ),
    )


def select_model(
    models: tuple[ModelInfo, ...], profile: str, reasoning: str, config: dict[str, Any],
    current_model: str | None = None, statuses: dict[str, dict[str, Any]] | None = None,
    force_current: bool = False, family: dict[str, Any] | None = None,
) -> Selection:
    statuses = statuses or {}
    requested = reasoning.casefold()
    eligible = [model for model in models if _is_eligible(model, config)]
    available = [
        model for model in eligible
        if statuses.get(model.slug, {}).get("status", "AVAILABLE") == "AVAILABLE"
    ]
    if not available:
        return Selection(None, requested, requested, "UNAVAILABLE", note="No available same-provider model found.")

    current = next((model for model in available if model.slug == current_model), None)
    if current and (force_current or _fits_profile(current, profile, config, family)):
        chosen = current
        evidence = FORCED_CURRENT if force_current else KEPT_CURRENT
    else:
        preferred_rank = _rank_for_profile(eligible, profile, config, family)
        preferred = preferred_rank[0]
        if preferred in available:
            chosen = preferred
            evidence = profile_evidence(chosen, profile, config, family)
        else:
            chosen = None
            evidence = NO_EVIDENCE
            for fallback_profile in config["model_selection"].get("fallback_profiles", {}).get(profile, []):
                candidates = [model for model in available if _fits_profile(model, fallback_profile, config, family)]
                if candidates:
                    chosen = _rank_for_profile(candidates, fallback_profile, config, family)[0]
                    evidence = FALLBACK_PROFILE
                    break
            if chosen is None:
                chosen = _rank_for_profile(available, profile, config, family)[0]
                evidence = profile_evidence(chosen, profile, config, family)

    tie_break = config["model_selection"].get("reasoning_tie_break", "up")
    adjusted = _nearest_effort(requested, chosen.efforts, config["reasoning_order"], tie_break)
    preferred = _rank_for_profile(eligible, profile, config, family)[0]
    preferred_status = statuses.get(preferred.slug, {}).get("status", "AVAILABLE")
    fallback_from = preferred.slug if preferred.slug != chosen.slug and preferred_status != "AVAILABLE" else None
    state = statuses.get(fallback_from or chosen.slug, {})
    return Selection(
        chosen, adjusted, requested, "AVAILABLE", reset=state.get("reset"), fallback_from=fallback_from,
        note=(f"{requested.upper()} is unsupported; using {adjusted.upper()}." if adjusted != requested else ""),
        evidence=evidence,
    )


def select_counterparts(
    catalogs: dict[str, CatalogResult], active_family: str | None, profile: str, reasoning: str,
    config: dict[str, Any], provider: str = "codex", statuses: dict[str, dict[str, Any]] | None = None,
) -> tuple[Counterpart, ...]:
    """Pick the equivalent model in every other family of the same provider.

    The counterpart is informational. The router never switches provider on its own, so this
    answers "what would serve this task on the other side" rather than proposing a swap.
    """
    families = config.get("providers", {}).get(provider, {}).get("families", {})
    counterparts: list[Counterpart] = []
    for family, definition in families.items():
        if family == active_family:
            continue
        catalog = catalogs.get(family)
        if catalog is None or not catalog.models:
            continue
        selection = select_model(
            catalog.models, profile, reasoning, config, statuses=statuses, family=definition,
        )
        if selection.model is None:
            continue
        counterparts.append(Counterpart(
            family=family,
            label=str(definition.get("label", family)),
            model=selection.model,
            reasoning=selection.reasoning,
            note=selection.note,
            evidence=selection.evidence,
        ))
    return tuple(counterparts)


def recommend(
    catalogs: dict[str, CatalogResult], active_family: str | None, profile: str, reasoning: str,
    config: dict[str, Any], provider: str = "codex", current_model: str | None = None,
    statuses: dict[str, dict[str, Any]] | None = None, force_current: bool = False,
) -> Recommendation:
    """Select inside the active family, then attach the equivalent model in each other family."""
    families = config.get("providers", {}).get(provider, {}).get("families", {})
    definition = families.get(active_family or "", {})
    active_catalog = catalogs.get(active_family or "")
    models = active_catalog.models if active_catalog else ()
    selection = select_model(
        models, profile, reasoning, config, current_model, statuses, force_current, definition,
    )
    return Recommendation(
        family=active_family,
        label=str(definition.get("label", active_family or "")),
        selection=selection,
        counterparts=select_counterparts(
            catalogs, active_family, profile, reasoning, config, provider, statuses,
        ),
    )
