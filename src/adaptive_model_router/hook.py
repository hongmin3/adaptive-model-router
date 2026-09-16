from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .catalog import resolve_active_catalogs
from .config import load_config
from .scorer import ScoreResult, score_prompt
from .selector import Recommendation, Selection, recommend


def _state_root() -> Path:
    override = os.environ.get("ADAPTIVE_MODEL_ROUTER_STATE_DIR")
    return Path(override) if override else Path.home() / ".codex" / "adaptive-model-router" / "state"


def _approval_path(session_id: str, prompt: str) -> Path:
    safe_session = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20]
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return _state_root() / safe_session / f"{prompt_hash}.json"


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
        "Current Reasoning: UNKNOWN (Codex hook input does not expose the session effort)",
        "",
        f"Recommended: {recommended}",
        f"Reasoning: {selection.reasoning.upper()}",
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
        "1. 추천 설정을 쓰려면 /model에서 위 모델과 Reasoning을 선택하세요.",
        "2. 현재 설정을 유지하려면 변경하지 않아도 됩니다.",
        "3. 같은 Prompt를 다시 제출하면 승인으로 간주되어 실제 작업이 시작됩니다.",
        "",
        "Router 판단에는 LLM/API 호출과 모델 토큰이 사용되지 않았습니다.",
    ))
    return "\n".join(lines)


def evaluate_hook(payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    session_id = str(payload.get("session_id", "unknown-session"))
    current_model = str(payload.get("model", "")).strip() or None
    if not prompt:
        return {"continue": True}

    router_config = config or load_config()
    hook_config = router_config.get("hook", {})
    approval_path = _approval_path(session_id, prompt)
    if _consume_approval(approval_path, int(hook_config.get("approval_ttl_seconds", 600))):
        return {"continue": True}

    result = score_prompt(prompt, router_config)
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
    _store_approval(approval_path, recommendation.selection, result)
    return {
        "decision": "block",
        "reason": _recommendation_reason(
            current_model, recommendation, result, bool(hook_config.get("debug", False))
        ),
    }


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
        response = evaluate_hook(payload)
    except Exception as exc:  # A broken advisory router must not make Codex unusable.
        response = {
            "continue": True,
            "systemMessage": f"Adaptive Model Router skipped: {type(exc).__name__}",
        }
    json.dump(response, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
