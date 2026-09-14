from __future__ import annotations

import json
import sys
from typing import Any

from .catalog import load_codex_catalog
from .config import load_config
from .scorer import score_prompt
from .selector import select_model


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
    catalog = load_codex_catalog()
    current_model = str(payload.get("current_model", "")).strip() or None
    selection = select_model(
        catalog.models,
        result.profile,
        result.reasoning,
        router_config,
        current_model=current_model,
        force_current=result.keep_current_model,
    )
    if selection.model is None:
        return {
            "enabled": False,
            "error": "no_available_model",
            "score": result.score,
            "confidence": result.confidence,
        }

    return {
        "enabled": True,
        "model": selection.model.slug,
        "model_display_name": selection.model.display_name,
        "reasoning": selection.reasoning,
        "profile": result.profile,
        "score": result.score,
        "confidence": result.confidence,
        "reason": result.reason,
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
