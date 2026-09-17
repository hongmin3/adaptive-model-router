from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .catalog import (
    LEGACY_FAMILY,
    ModelInfo,
    default_family,
    discover_claude_tiers,
    family_definitions,
    load_claude_catalog,
    load_current_claude_config,
    load_current_codex_config,
    load_family_catalog,
    load_status_overrides,
    resolve_active_catalogs,
    resolve_command,
)
from .config import config_state, load_config, refresh_user_config
from .scorer import ScoreResult, score_prompt
from .selector import Recommendation, Selection, classify_model, recommend


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
    parser.add_argument(
        "--family",
        help="Model family to route inside (e.g. openai, deepseek); detected from the current model by default",
    )
    parser.add_argument(
        "--list-models", action="store_true",
        help="Print the models this provider exposes, the version behind each one, and the profile it serves",
    )
    parser.add_argument(
        "--refresh-config", action="store_true",
        help="Replace the installed user configuration with the packaged defaults, keeping a backup",
    )
    return parser


def _print_config_state() -> None:
    state = config_state()
    print(f"Config: {state['path']} (version {state['version']})")
    if state["stale"]:
        print(
            f"\n[!] 이 설정은 패키지 기본값(version {state['packaged_version']})보다 오래됐습니다.\n"
            "    설치된 사용자 설정은 한 번 만들어지면 갱신되지 않고, rules·families 같은\n"
            "    목록은 병합되지 않습니다. 즉 이후에 추가된 모델 등급과 규칙이 이 설치에는\n"
            "    도달하지 않습니다. 아무것도 실패하지 않으므로 증상 없이 옛 규칙으로 라우팅됩니다.\n"
            "    갱신: py -m adaptive_model_router.cli --refresh-config (기존 파일은 백업됩니다)"
        )
    print()


def _read_prompt(parts: list[str]) -> str:
    if parts:
        return " ".join(parts).strip()
    return input("요청을 입력하세요:\n> ").strip()


def _print_debug(result: ScoreResult, selection: Selection | None = None) -> None:
    print("\nRouter Debug\n")
    print(f"Score: {result.score}\n")
    print(f"Model Score: {result.model_score}\n")
    print("Matched:")
    if result.matches:
        for match in result.matches:
            print(f"{match.score:+d} {match.label}")
    else:
        print("(none)")
    print(f"\nResult:\n{result.level}")
    print(f"\nConfidence:\n{result.confidence:.2f}")
    print(f"\nConfidence Basis:\n{result.confidence_reason}")
    print(f"\nModel:\n{result.profile}")
    if selection is not None:
        # Why this model, not just which one: "no-evidence" means the configuration
        # described no model for this profile and catalog order decided it.
        print(f"\nSelection Evidence:\n{selection.evidence}")
    if result.uncertain_default_used:
        print(f"\nFallback:\n{result.profile} / {result.reasoning} (low confidence)")


def _print_recommendation(
    recommendation: Recommendation, result: ScoreResult, current_model: str | None,
    current_effort: str | None, provider_label: str, reasoning_label: str,
) -> None:
    selection = recommendation.selection
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
    family_suffix = f" ({recommendation.label})" if recommendation.label else ""
    print(f"Recommended:\n{'KEEP CURRENT' if keep_current else selection.model.display_name}{family_suffix}")
    print(f"\n{reasoning_label}:\n{selection.reasoning.upper()}")
    for counterpart in recommendation.counterparts:
        print(f"\nAlternative ({counterpart.label}):")
        print(f"{counterpart.model.display_name} / {counterpart.reasoning.upper()}")
    print(f"\nReason:\n{result.reason}")
    if selection.note:
        print(f"\n{selection.note}")


def _pinned_profile(alias: str, family: dict) -> str:
    for profile, slugs in family.get("profile_models", {}).items():
        if alias.casefold() in [str(slug).casefold() for slug in slugs]:
            return profile
    return ""


def _classified_profile(model: ModelInfo, config: dict, family: dict) -> str:
    """The profile this model serves, and how it was decided.

    A family may classify by pin (Claude, DeepSeek) or by slug role pattern (GPT), so a
    listing that only reads pins reports a correctly-classified catalog as unclassified.
    """
    profile, evidence = classify_model(model, config, family)
    return f"{profile} ({evidence})" if profile else "-"


def _print_claude_models(config: dict, command: str) -> int:
    """Show every tier the installed build knows, what version it resolves to, and its profile.

    This is the pre-install check: Claude Code exposes tier aliases (`opus`) rather than
    versioned slugs, so a new model release changes what `opus` means without changing
    anything the router stores.  A new *tier* is different and is reported below.
    """
    provider = config["providers"]["claude"]
    family = provider.get("families", {}).get(default_family(config, "claude"), {})
    configured = provider.get("verified_alias_families", {})
    discovered = {tier.alias: tier for tier in discover_claude_tiers(command, refresh=True)}

    print(f"{provider['label']}\n")
    if not discovered:
        print("이 Claude Code 빌드에서 Tier 표를 읽지 못했습니다. 설정값만 표시합니다.\n")
    print(f"{'Tier':10s} {'Version':22s} {'Profile':10s} Source")
    print("-" * 66)
    for alias in list(configured) + [name for name in discovered if name not in configured]:
        tier = discovered.get(alias)
        version = (tier.model_id if tier else "") or "-"
        profile = _pinned_profile(alias, family) or "-"
        if alias in configured and tier:
            source = "configured + build"
        elif alias in configured:
            source = "configured only"
        elif tier and tier.model_id:
            source = "build only (NEW)"
        else:
            source = "build only (not selectable)"
        print(f"{alias:10s} {version:22s} {profile:10s} {source}")

    unclassified = [
        alias for alias, tier in discovered.items()
        if tier.model_id and not _pinned_profile(alias, family)
    ]
    if unclassified:
        print(
            f"\n분류되지 않은 신규 Tier: {', '.join(unclassified)}\n"
            "이 Tier는 어떤 Profile의 1순위도 되지 않습니다. 등급을 정하려면 설정의\n"
            "providers.claude.families.<family>.profile_models 와 verified_alias_families 에\n"
            "함께 추가하세요. Router가 순위를 추측하지 않는 이유는, 잘못된 순위가 조용히\n"
            "가장 어려운 작업을 가장 싼 모델로 보내기 때문입니다."
        )
    else:
        print("\n모든 선택 가능한 Tier에 Profile이 지정되어 있습니다.")
    print(
        "\nTier alias는 항상 그 등급의 최신 모델을 가리킵니다. Claude Code를 업데이트하면\n"
        "위 Version 열이 자동으로 바뀌며, Router 설정은 고치지 않아도 됩니다."
    )
    return 0


def _print_codex_models(config: dict, command: str, refresh: bool) -> int:
    print(f"{config['providers']['codex']['label']}\n")
    print(f"{'Model':18s} {'Profile':24s} Reasoning")
    print("-" * 70)
    unclassified: list[str] = []
    for name, definition in family_definitions(config, "codex").items():
        catalog = load_family_catalog(name, config, "codex", refresh, command)
        print(f"[{definition.get('label', name)}] {catalog.status}")
        for model in catalog.models:
            profile = _classified_profile(model, config, definition)
            if profile == "-":
                unclassified.append(model.slug)
            print(f"{model.slug:18s} {profile:24s} {', '.join(model.efforts)}")
    if unclassified:
        print(
            f"\n분류되지 않은 모델: {', '.join(unclassified)}\n"
            "model_selection.slug_role_patterns 또는 family의 profile_models에 추가하기 전까지\n"
            "이 모델은 어떤 Profile의 1순위도 되지 않습니다."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.refresh_config:
        backup, destination = refresh_user_config()
        print(f"Config refreshed: {destination}")
        print(f"Backup: {backup}" if backup else "No previous config to back up.")
        return 0
    if args.list_models:
        config = load_config(args.config)
        _print_config_state()
        if args.provider == "claude":
            return _print_claude_models(config, args.claude_command)
        return _print_codex_models(config, args.codex_command, args.refresh_catalog)

    prompt = _read_prompt(args.prompt)
    if not prompt:
        print("Prompt is required.", file=sys.stderr)
        return 2

    config = load_config(args.config)
    result = score_prompt(prompt, config)
    if args.provider == "codex":
        current_model, current_effort = load_current_codex_config()
        active_family, catalogs = resolve_active_catalogs(
            config, current_model, args.provider, args.refresh_catalog, args.codex_command,
        )
    else:
        current_model, current_effort = load_current_claude_config()
        # Claude has one family, but it still goes through the family machinery: that is
        # where per-profile model pins live, and without them the profile that matches no
        # description hint falls through to catalog order.
        active_family = default_family(config, args.provider) or LEGACY_FAMILY
        catalogs = {
            active_family: load_claude_catalog(args.claude_command, config, refresh=args.refresh_catalog)
        }
    if args.family and args.family in catalogs:
        active_family = args.family
    provider_config = config["providers"][args.provider]
    active_catalog = catalogs.get(active_family or LEGACY_FAMILY)
    if active_catalog is not None and active_catalog.status == "AUTH_REQUIRED":
        print(f"{provider_config['label']}\n\nAUTHENTICATION REQUIRED\n\n현재 로그인 상태를 확인할 수 없습니다.\n\n모델 추천 전에 인증이 필요합니다.")
        return 3
    statuses = load_status_overrides(args.status_file, args.provider)
    recommendation = recommend(
        catalogs, active_family, result.profile, result.reasoning, config, args.provider,
        current_model, statuses, force_current=result.keep_current_model,
    )
    selection = recommendation.selection

    _print_recommendation(
        recommendation, result, current_model, current_effort,
        provider_config["label"], provider_config["reasoning_label"],
    )
    if args.debug:
        _print_debug(result, selection)
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
