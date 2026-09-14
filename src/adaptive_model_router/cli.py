from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .catalog import (
    load_claude_catalog,
    load_codex_catalog,
    load_current_claude_config,
    load_current_codex_config,
    load_status_overrides,
    resolve_command,
)
from .config import load_config
from .scorer import ScoreResult, score_prompt
from .selector import Selection, select_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Zero-token local model and reasoning router")
    parser.add_argument("prompt", nargs="*", help="Task prompt; omitted prompts are read interactively")
    parser.add_argument("--config", type=Path, help="Router JSON configuration")
    parser.add_argument("--status-file", type=Path, help="Optional locally supplied model availability JSON")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--debug", action="store_true", help="Show score and matched rules")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation and execute")
    parser.add_argument("--no-exec", action="store_true", help="Print recommendation only")
    parser.add_argument(
        "--refresh-catalog", action="store_true",
        help="Explicitly query CLI metadata; default routing reads local files only",
    )
    parser.add_argument("--codex-command", default="codex", help="Codex executable name or path")
    parser.add_argument("--claude-command", default="claude", help="Claude executable name or path")
    return parser


def _read_prompt(parts: list[str]) -> str:
    if parts:
        return " ".join(parts).strip()
    return input("요청을 입력하세요:\n> ").strip()


def _print_debug(result: ScoreResult) -> None:
    print("\nRouter Debug\n")
    print(f"Score: {result.score}\n")
    print("Matched:")
    if result.matches:
        for match in result.matches:
            print(f"{match.score:+d} {match.label}")
    else:
        print("(none)")
    print(f"\nResult:\n{result.level}")
    print(f"\nConfidence:\n{result.confidence:.2f}")
    print(f"\nModel:\n{result.profile}")
    if result.uncertain_default_used:
        print("\nFallback:\nBALANCED / MEDIUM (low confidence)")


def _print_recommendation(
    selection: Selection, result: ScoreResult, current_model: str | None, current_effort: str | None,
    provider_label: str, reasoning_label: str,
) -> None:
    print(f"{provider_label}\n")
    print(f"{selection.status}\n")
    if current_model:
        print(f"Current:\n{current_model} / {(current_effort or 'UNKNOWN').upper()}\n")
    if selection.fallback_from:
        print(f"{selection.fallback_from}\nLIMIT OR UNAVAILABLE")
        print(f"\nReset:\n{selection.reset or 'Unknown'}\n")
    if selection.model is None:
        print("AVAILABLE MODELS:\nUNKNOWN")
        return
    keep_current = selection.model.slug == current_model and selection.reasoning == (current_effort or "").casefold()
    print(f"Recommended:\n{'KEEP CURRENT' if keep_current else selection.model.display_name}")
    print(f"\n{reasoning_label}:\n{selection.reasoning.upper()}")
    print(f"\nReason:\n{result.reason}")
    if selection.note:
        print(f"\n{selection.note}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompt = _read_prompt(args.prompt)
    if not prompt:
        print("Prompt is required.", file=sys.stderr)
        return 2

    config = load_config(args.config)
    result = score_prompt(prompt, config)
    if args.provider == "codex":
        catalog = load_codex_catalog(args.codex_command, refresh=args.refresh_catalog)
        current_model, current_effort = load_current_codex_config()
    else:
        catalog = load_claude_catalog(args.claude_command, config, refresh=args.refresh_catalog)
        current_model, current_effort = load_current_claude_config()
    provider_config = config["providers"][args.provider]
    if catalog.status == "AUTH_REQUIRED":
        print(f"{provider_config['label']}\n\nAUTHENTICATION REQUIRED\n\n현재 로그인 상태를 확인할 수 없습니다.\n\n모델 추천 전에 인증이 필요합니다.")
        return 3
    statuses = load_status_overrides(args.status_file, args.provider)
    selection = select_model(
        catalog.models, result.profile, result.reasoning, config, current_model, statuses,
        force_current=result.keep_current_model,
    )

    _print_recommendation(
        selection, result, current_model, current_effort,
        provider_config["label"], provider_config["reasoning_label"],
    )
    if args.debug:
        _print_debug(result)
    if args.no_exec or selection.model is None:
        return 0 if selection.model else 4

    if not args.yes:
        answer = input(f"\n{selection.model.display_name} / {selection.reasoning.upper()}로 실행할까요? [Y/n] ").strip().casefold()
        if answer not in ("", "y", "yes"):
            print("실행하지 않았습니다.")
            return 0

    if args.provider == "codex":
        command = [
            resolve_command(args.codex_command), "--model", selection.model.slug,
            "-c", f'model_reasoning_effort="{selection.reasoning}"', prompt,
        ]
    else:
        command = [resolve_command(args.claude_command), "--model", selection.model.slug, "--effort", selection.reasoning, prompt]
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
