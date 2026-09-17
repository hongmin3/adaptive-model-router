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
    claude_alias_for_model,
    claude_model_from_transcript,
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
# Measured on Claude Code 2.1.274: the UserPromptSubmit payload carries no `source` field
# at all, so that gate is currently inert and the entrypoint gate below does the work.

# One hook registration in ~/.claude/settings.json fires on every Claude Code surface -
# terminal, desktop app, IDE extension, SDK - because settings.json is user-scoped, not
# per-surface.  Only a terminal session has someone who can answer the confirmation by
# resubmitting, so everything else is passed straight through.
#
# Claude Code's launchers set CLAUDE_CODE_ENTRYPOINT (measured: "claude-desktop" in the
# desktop app, "sdk-cli" under `claude --print`); a plain terminal session leaves it unset,
# which the CLI's own code reads as `CLAUDE_CODE_ENTRYPOINT ?? "cli"`.  This is an
# allow-list on purpose: a deny-list of known GUI surfaces would admit every surface added
# after it was written, and the whole point is not to interrupt those.
TERMINAL_ENTRYPOINTS = ("cli", "ssh-remote", "claude-coworker-terminal")

# What the router does on a given surface.  BLOCK holds the prompt for confirmation, which
# only works where someone can resubmit; ADVISE shows the same recommendation as a system
# message and lets the prompt through, which is what a GUI wants; OFF says nothing.
BLOCK = "block"
ADVISE = "advise"
OFF = "off"
DEFAULT_SURFACE_MODE = "*"


def _entrypoint() -> str:
    return (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "cli").strip()


def _surface_mode(hook_config: dict[str, Any]) -> str:
    """How loudly to speak on the surface this hook is running on.

    Keyed by entrypoint with a `*` fallback so an unknown surface has a stated answer
    rather than inheriting whichever branch happened to be written last; the shipped
    fallback is OFF, which keeps a surface Anthropic adds later quiet until it is named.
    """
    modes = hook_config.get("claude_modes")
    if not isinstance(modes, dict) or not modes:
        return BLOCK if _entrypoint() in TERMINAL_ENTRYPOINTS else OFF
    mode = modes.get(_entrypoint(), modes.get(DEFAULT_SURFACE_MODE, OFF))
    return mode if mode in (BLOCK, ADVISE, OFF) else OFF


def _already_recommended(
    current_model: str | None, current_effort: str | None, selection: Selection,
) -> bool:
    """True when the session is already on exactly what would be recommended.

    Both halves must be known: an unknown model or effort is not a match, it is an absence,
    and treating it as one would silence the router on every session's first prompt.
    """
    if not current_model or not current_effort or selection.model is None:
        return False
    return (
        current_model.casefold() == selection.model.slug.casefold()
        and current_effort.casefold() == selection.reasoning.casefold()
    )


def _resolve_claude_model(
    payload: dict[str, Any], config: dict[str, Any],
) -> tuple[str | None, str]:
    """The model this Claude Code session is on, and where that was read from.

    Neither the UserPromptSubmit payload nor any CLAUDE_* variable carries the model, so
    the session transcript - whose path the payload does hand over - is the only live
    source.  It is reported as the tier alias the router routes by, so a session already on
    the right tier is recognised instead of being told to switch to it.  The source is part
    of the answer: the transcript describes the previous turn, a pinned setting describes
    every turn, and the two can disagree after a mid-session /model change.
    """
    transcript_model = claude_model_from_transcript(payload.get("transcript_path"))
    if transcript_model:
        alias = claude_alias_for_model(transcript_model, config)
        return (alias or transcript_model), "last turn, from the session transcript"
    pinned, _ = load_current_claude_config()
    if pinned:
        return pinned, "pinned in settings.json"
    return None, ""


def _current_effort() -> str | None:
    """The effort level Claude Code is actually running this turn.

    The UserPromptSubmit payload has no effort field, but Claude Code exports the active
    level to every hook command as CLAUDE_EFFORT (measured present in both the desktop app
    and a CLI run), so the recommendation can be compared against the current setting
    instead of reporting UNKNOWN.
    """
    return (os.environ.get("CLAUDE_EFFORT") or "").strip().upper() or None


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


def _prune_approvals(root: Path, ttl_seconds: int) -> None:
    """Drop approvals that can no longer be consumed, across every session.

    `_consume_approval` deletes only what it reads, so every recommendation the user walked
    away from - a different prompt, a closed session - stays on disk for good.  The sweep
    rides inside the write that creates the next one rather than sitting beside it as a
    separate duty, so it cannot be skipped while approvals are still being written.

    It walks the whole state root, not just the session being written: approvals are filed
    per session directory, so a sweep scoped to the current one can never reach the sessions
    that have already ended - which is every session that leaked. Empty directories go too,
    or the leak just changes shape from files to directories.
    """
    cutoff = time.time() - max(ttl_seconds, 0)
    try:
        sessions = [entry for entry in root.iterdir() if entry.is_dir()]
    except OSError:
        return
    for session in sessions:
        try:
            for entry in session.iterdir():
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            if next(session.iterdir(), None) is None:
                session.rmdir()
        except OSError:
            continue


def _store_approval(
    path: Path, selection: Selection, result: ScoreResult, ttl_seconds: int = 600,
) -> None:
    _prune_approvals(path.parent.parent, ttl_seconds)
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
    current_effort: str | None = None, current_source: str = "", advisory: bool = False,
) -> str:
    selection: Selection = recommendation.selection
    recommended = selection.model.display_name if selection.model else "KEEP CURRENT"
    if recommendation.label:
        recommended = f"{recommended} ({recommendation.label})"
    model_line = (
        f"{current_model} ({current_source})" if current_model and current_source
        else current_model
        or "UNKNOWN (first prompt of the session; pin one in settings.json to always show it)"
    )
    effort_line = current_effort or (
        f"UNKNOWN ({provider_label} does not expose the session effort to this hook)"
    )
    lines = [
        "Adaptive Model Router",
        "",
        "RECOMMENDATION (not applied)" if advisory else "MODEL CALL BLOCKED BEFORE EXECUTION",
        "",
        f"Current Model: {model_line}",
        f"Current {reasoning_label}: {effort_line}",
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
    if advisory:
        lines.extend((
            "",
            f"이 Prompt는 그대로 실행됩니다. 추천을 쓰려면 /model에서 위 모델과 {reasoning_label} 값을 바꾸세요.",
        ))
    else:
        lines.extend((
            "",
            "승인 방법:",
            f"1. 추천 설정을 쓰려면 /model에서 위 모델과 {reasoning_label} 값을 선택하세요.",
            "2. 현재 설정을 유지하려면 변경하지 않아도 됩니다.",
            "3. 같은 Prompt를 다시 제출하면 승인으로 간주되어 실제 작업이 시작됩니다.",
        ))
    lines.extend(("", "Router 판단에는 LLM/API 호출과 모델 토큰이 사용되지 않았습니다."))
    return "\n".join(lines)


def evaluate_hook(
    payload: dict[str, Any], config: dict[str, Any] | None = None, provider: str = "codex",
) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    session_id = str(payload.get("session_id", "unknown-session"))
    if not prompt:
        return {"continue": True}

    router_config = config or load_config()
    hook_config = router_config.get("hook", {})
    mode = BLOCK
    if provider == "claude":
        mode = _surface_mode(hook_config)
        if mode == OFF:
            return {"continue": True}
        # Real Claude Code fires UserPromptSubmit for prompts the user never typed
        # (scheduled wakeups, SDK/subagent calls); only a "user" prompt has someone
        # present to answer the Y/N confirmation, so anything else must pass through.
        source = str(payload.get("source", "user")).strip() or "user"
        if source != "user":
            return {"continue": True}

    approval_path = _approval_path(session_id, prompt, provider)
    if _consume_approval(approval_path, int(hook_config.get("approval_ttl_seconds", 600))):
        return {"continue": True}

    result = score_prompt(prompt, router_config)
    if provider == "claude":
        # Claude Code's hook payload carries no "model" field, unlike the patched
        # Codex build; the running model has to be read from its own settings file.
        current_model, current_source = _resolve_claude_model(payload, router_config)
        # Same family resolution as the CLI path: the per-profile model pins live on the
        # family, so routing without it silently degrades to catalog order.
        active_family = default_family(router_config, provider) or LEGACY_FAMILY
        catalogs = {active_family: load_claude_catalog(config=router_config)}
    else:
        current_model = str(payload.get("model", "")).strip() or None
        current_source = ""
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
    current_effort = _current_effort() if provider == "claude" else None
    # Nothing to confirm when the session is already on the recommendation: blocking here
    # costs a round trip and teaches the user that the screen carries no information.
    if _already_recommended(current_model, current_effort, recommendation.selection):
        return {"continue": True}

    provider_config = router_config.get("providers", {}).get(provider, {})
    reason = _recommendation_reason(
        current_model, recommendation, result, bool(hook_config.get("debug", False)),
        provider, str(provider_config.get("label", provider.title())),
        str(provider_config.get("reasoning_label", "Reasoning")),
        current_effort, current_source, advisory=mode == ADVISE,
    )
    if mode == ADVISE:
        # A surface with no approval step still benefits from the recommendation; it just
        # must not be interrupted for it.  No approval is stored: nothing was withheld.
        return {"continue": True, "systemMessage": reason}
    _store_approval(
        approval_path, recommendation.selection, result,
        int(hook_config.get("approval_ttl_seconds", 600)),
    )
    return {"decision": "block", "reason": reason}


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
