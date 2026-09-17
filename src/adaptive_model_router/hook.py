from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .catalog import (
    LEGACY_FAMILY,
    default_family,
    load_claude_catalog,
    load_current_claude_config,
    resolve_active_catalogs,
)
from .config import load_config
from .scorer import ScoreResult, score_prompt
from .selector import Recommendation, Selection, recommend

# A UserPromptSubmit hook also sees prompts the user never typed this turn - observed
# sources include "sdk", "system", "loop_wakeup", "schedule_wakeup" and "poll_event".
# Blocking those stalls work that has no human present to answer the Y/N confirmation,
# so the gate below admits only source == "user" rather than listing what to skip: an
# allow-list of skippable sources would block every source added after it was written.


def _state_root(provider: str = "codex") -> Path:
    override = os.environ.get("ADAPTIVE_MODEL_ROUTER_STATE_DIR")
    if override:
        return Path(override)
    home_dir = ".claude" if provider == "claude" else ".codex"
    return Path.home() / home_dir / "adaptive-model-router" / "state"


def _approval_path(session_id: str, prompt: str, provider: str = "codex") -> Path:
    safe_session = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20]
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return _state_root(provider) / safe_session / f"{prompt_hash}.json"


def _consume_approval(path: Path, ttl_seconds: int) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fresh = time.time() - float(payload["created_at"]) <= ttl_seconds
        path.unlink(missing_ok=True)
        return fresh
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _store_approval(path: Path, selection: Selection, result: ScoreResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": time.time(),
        "recommended_model": selection.model.slug if selection.model else None,
        "recommended_reasoning": selection.reasoning,
        "score": result.score,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _recommendation_reason(
    current_model: str | None, recommendation: Recommendation, result: ScoreResult, debug: bool,
    provider: str = "codex", provider_label: str = "Codex", reasoning_label: str = "Reasoning",
) -> str:
    selection: Selection = recommendation.selection
    recommended = selection.model.display_name if selection.model else "KEEP CURRENT"
    if recommendation.label:
        recommended = f"{recommended} ({recommendation.label})"
    lines = [
        "Adaptive Model Router",
        "",
        "MODEL CALL BLOCKED BEFORE EXECUTION",
        "",
        f"Current Model: {current_model or 'UNKNOWN'}",
        f"Current {reasoning_label}: UNKNOWN ({provider_label} hook input does not expose the session effort)",
        "",
        f"Recommended: {recommended}",
        f"{reasoning_label}: {selection.reasoning.upper()}",
    ]
    lines.extend(
        f"Alternative ({counterpart.label}): {counterpart.model.display_name} / {counterpart.reasoning.upper()}"
        for counterpart in recommendation.counterparts
    )
    lines.append(f"Reason: {result.reason}")
    if selection.note:
        lines.extend(("", selection.note))
    if debug:
        lines.extend(("", "Router Debug", f"Score: {result.score}", f"Confidence: {result.confidence:.2f}"))
        lines.extend(f"{match.score:+d} {match.label}" for match in result.matches)
    lines.extend((
        "",
        "승인 방법:",
        f"1. 추천 설정을 쓰려면 /model에서 위 모델과 {reasoning_label} 값을 선택하세요.",
        "2. 현재 설정을 유지하려면 변경하지 않아도 됩니다.",
        "3. 같은 Prompt를 다시 제출하면 승인으로 간주되어 실제 작업이 시작됩니다.",
        "",
        "Router 판단에는 LLM/API 호출과 모델 토큰이 사용되지 않았습니다.",
    ))
    return "\n".join(lines)


def evaluate_hook(
    payload: dict[str, Any], config: dict[str, Any] | None = None, provider: str = "codex",
) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    session_id = str(payload.get("session_id", "unknown-session"))
    if not prompt:
        return {"continue": True}
    if provider == "claude":
        # Real Claude Code fires UserPromptSubmit for prompts the user never typed
        # (scheduled wakeups, SDK/subagent calls); only a "user" prompt has someone
        # present to answer the Y/N confirmation, so anything else must pass through.
        source = str(payload.get("source", "user")).strip() or "user"
        if source != "user":
            return {"continue": True}

    router_config = config or load_config()
    hook_config = router_config.get("hook", {})
    approval_path = _approval_path(session_id, prompt, provider)
    if _consume_approval(approval_path, int(hook_config.get("approval_ttl_seconds", 600))):
        return {"continue": True}

    result = score_prompt(prompt, router_config)
    if provider == "claude":
        # Claude Code's hook payload carries no "model" field, unlike the patched
        # Codex build; the running model has to be read from its own settings file.
        current_model, _ = load_current_claude_config()
        # Same family resolution as the CLI path: the per-profile model pins live on the
        # family, so routing without it silently degrades to catalog order.
        active_family = default_family(router_config, provider) or LEGACY_FAMILY
        catalogs = {active_family: load_claude_catalog(config=router_config)}
    else:
        current_model = str(payload.get("model", "")).strip() or None
        active_family, catalogs = resolve_active_catalogs(router_config, current_model)
    recommendation = recommend(
        catalogs,
        active_family,
        result.profile,
        result.reasoning,
        router_config,
        provider=provider,
        current_model=current_model,
        force_current=result.keep_current_model,
    )
    _store_approval(approval_path, recommendation.selection, result)
    provider_config = router_config.get("providers", {}).get(provider, {})
    return {
        "decision": "block",
        "reason": _recommendation_reason(
            current_model, recommendation, result, bool(hook_config.get("debug", False)),
            provider, str(provider_config.get("label", provider.title())),
            str(provider_config.get("reasoning_label", "Reasoning")),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Adaptive Model Router UserPromptSubmit hook")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    args = parser.parse_args()

    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
        response = evaluate_hook(payload, provider=args.provider)
    except Exception as exc:  # A broken advisory router must not make the CLI unusable.
        response = {
            "continue": True,
            "systemMessage": f"Adaptive Model Router skipped: {type(exc).__name__}",
        }
    json.dump(response, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
