from __future__ import annotations

import json
import sys
from typing import Any

from .catalog import resolve_active_catalogs
from .config import load_config
from .scorer import score_prompt
from .selector import recommend


def route_native(payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a machine-readable recommendation for the patched Codex TUI.

    This path is deliberately local-only: it reads the packaged JSON rules and
    Codex's local model cache, and never starts an AI CLI or calls an API.
    """
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return {"enabled": False, "error": "empty_prompt"}

    router_config = config or load_config()
    result = score_prompt(prompt, router_config)
    current_model = str(payload.get("current_model", "")).strip() or None
    # The provider wrapper launches Codex with `--model deepseek-flash`, so the running slug
    # is what says which family is actually serving this session.
    active_family, catalogs = resolve_active_catalogs(router_config, current_model)
    recommendation = recommend(
        catalogs,
        active_family,
        result.profile,
        result.reasoning,
        router_config,
        current_model=current_model,
        force_current=result.keep_current_model,
    )
    selection = recommendation.selection
    if selection.model is None:
        return {
            "enabled": False,
            "error": "no_available_model",
            "score": result.score,
            "model_score": result.model_score,
            "confidence": result.confidence,
            "confidence_reason": result.confidence_reason,
        }

    return {
        "enabled": True,
        "model": selection.model.slug,
        "model_display_name": selection.model.display_name,
        "reasoning": selection.reasoning,
        "family": recommendation.family,
        "family_label": recommendation.label,
        "profile": result.profile,
        "score": result.score,
        "model_score": result.model_score,
        "confidence": result.confidence,
        "confidence_reason": result.confidence_reason,
        "reason": f"{result.reason} ({result.confidence_reason})",
        "alternatives": [
            {
                "family": counterpart.family,
                "family_label": counterpart.label,
                "model": counterpart.model.slug,
                "model_display_name": counterpart.model.display_name,
                "reasoning": counterpart.reasoning,
            }
            for counterpart in recommendation.counterparts
        ],
    }


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        response = route_native(json.load(sys.stdin))
    except Exception as exc:  # The native integration is fail-open by design.
        response = {"enabled": False, "error": type(exc).__name__}
    json.dump(response, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
