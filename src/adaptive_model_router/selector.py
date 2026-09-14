from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import ModelInfo


@dataclass(frozen=True)
class Selection:
    model: ModelInfo | None
    reasoning: str
    original_reasoning: str
    status: str
    reset: str | None = None
    fallback_from: str | None = None
    note: str = ""


def _hint_score(model: ModelInfo, hints: list[str]) -> int:
    description = model.description.casefold()
    return sum(1 for hint in hints if hint.casefold() in description)


def _rank_for_profile(models: list[ModelInfo], profile: str, config: dict[str, Any]) -> list[ModelInfo]:
    hints = config["model_selection"]["description_hints"].get(profile, [])
    if profile == "MAX":
        return sorted(models, key=lambda item: item.priority)
    return sorted(models, key=lambda item: (-_hint_score(item, hints), item.priority))


def _fits_profile(model: ModelInfo, profile: str, config: dict[str, Any]) -> bool:
    hints = config["model_selection"]["description_hints"].get(profile, [])
    return _hint_score(model, hints) > 0


def _nearest_effort(requested: str, supported: tuple[str, ...], order: list[str]) -> str:
    if requested in supported:
        return requested
    requested_index = order.index(requested) if requested in order else order.index("medium")
    candidates = [value for value in supported if value in order]
    if not candidates:
        return requested
    return min(candidates, key=lambda value: (abs(order.index(value) - requested_index), order.index(value) > requested_index))


def select_model(
    models: tuple[ModelInfo, ...], profile: str, reasoning: str, config: dict[str, Any],
    current_model: str | None = None, statuses: dict[str, dict[str, Any]] | None = None,
    force_current: bool = False,
) -> Selection:
    statuses = statuses or {}
    requested = reasoning.casefold()
    available = [model for model in models if statuses.get(model.slug, {}).get("status", "AVAILABLE") == "AVAILABLE"]
    if not available:
        return Selection(None, requested, requested, "UNAVAILABLE", note="No available same-provider model found.")

    current = next((model for model in available if model.slug == current_model), None)
    if current and (force_current or _fits_profile(current, profile, config)):
        chosen = current
    else:
        preferred_rank = _rank_for_profile(list(models), profile, config)
        preferred = preferred_rank[0]
        if preferred in available:
            chosen = preferred
        else:
            chosen = None
            for fallback_profile in config["model_selection"].get("fallback_profiles", {}).get(profile, []):
                candidates = [model for model in available if _fits_profile(model, fallback_profile, config)]
                if candidates:
                    chosen = _rank_for_profile(candidates, fallback_profile, config)[0]
                    break
            chosen = chosen or _rank_for_profile(available, profile, config)[0]

    adjusted = _nearest_effort(requested, chosen.efforts, config["reasoning_order"])
    preferred = _rank_for_profile(list(models), profile, config)[0]
    preferred_status = statuses.get(preferred.slug, {}).get("status", "AVAILABLE")
    fallback_from = preferred.slug if preferred.slug != chosen.slug and preferred_status != "AVAILABLE" else None
    state = statuses.get(fallback_from or chosen.slug, {})
    return Selection(
        chosen, adjusted, requested, "AVAILABLE", reset=state.get("reset"), fallback_from=fallback_from,
        note=(f"{requested.upper()} is unsupported; using {adjusted.upper()}." if adjusted != requested else ""),
    )
