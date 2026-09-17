from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AUTH_MARKERS = ("AUTH_ERROR", "LOGIN_REQUIRED", "INVALID_API_KEY", "SESSION_EXPIRED", "authentication required")
LIMIT_MARKERS = ("WEEKLY LIMIT", "RATE LIMIT", "DAILY LIMIT", "USAGE LIMIT", "LIMIT REACHED")


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    display_name: str
    description: str
    efforts: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class CatalogResult:
    status: str
    models: tuple[ModelInfo, ...]
    message: str = ""


def resolve_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved:
        return resolved
    if not command.casefold().endswith((".cmd", ".exe")):
        resolved = shutil.which(f"{command}.cmd") or shutil.which(f"{command}.exe")
        if resolved:
            return resolved
    try:
        located = subprocess.run(
            ["where.exe", command], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        )
        if located.returncode == 0 and located.stdout.strip():
            return located.stdout.splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return command


def _parse_codex_models(payload: dict[str, Any]) -> CatalogResult:
    try:
        models = []
        for item in payload.get("models", []):
            if item.get("visibility") != "list":
                continue
            models.append(ModelInfo(
                slug=item["slug"],
                display_name=item.get("display_name", item["slug"]),
                description=item.get("description", ""),
                efforts=tuple(level["effort"] for level in item.get("supported_reasoning_levels", [])),
                priority=int(item.get("priority", 9999)),
            ))
        return CatalogResult("AVAILABLE" if models else "UNKNOWN", tuple(models))
    except (KeyError, TypeError, ValueError) as exc:
        return CatalogResult("UNKNOWN", (), f"Invalid model catalog: {exc}")


def load_codex_catalog(command: str = "codex", refresh: bool = False, cache_path: Path | None = None) -> CatalogResult:
    local_cache = cache_path or Path.home() / ".codex" / "models_cache.json"
    if not refresh:
        try:
            with local_cache.open("r", encoding="utf-8") as handle:
                return _parse_codex_models(json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            return CatalogResult("UNKNOWN", (), f"Local Codex model cache unavailable: {exc}")

    executable = resolve_command(command)
    try:
        completed = subprocess.run(
            [executable, "debug", "models"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CatalogResult("UNKNOWN", (), str(exc))

    combined = f"{completed.stdout}\n{completed.stderr}"
    upper = combined.upper()
    if any(marker in upper for marker in AUTH_MARKERS):
        return CatalogResult("AUTH_REQUIRED", (), combined.strip())
    if completed.returncode != 0:
        status = "LIMIT_REACHED" if any(marker in upper for marker in LIMIT_MARKERS) else "UNKNOWN"
        return CatalogResult(status, (), combined.strip())
    try:
        return _parse_codex_models(json.loads(completed.stdout))
    except json.JSONDecodeError as exc:
        return CatalogResult("UNKNOWN", (), f"Invalid model catalog: {exc}")


def load_current_codex_config(path: Path | None = None) -> tuple[str | None, str | None]:
    config_path = path or Path.home() / ".codex" / "config.toml"
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
        return payload.get("model"), payload.get("model_reasoning_effort")
    except (OSError, tomllib.TOMLDecodeError):
        return None, None


# Claude Code ships no machine-readable model catalog and no `models` subcommand, but the
# build itself carries the table its own /model picker is drawn from.  Reading it is how the
# router learns about a tier that did not exist when this configuration was written.
CLAUDE_TIER_NAMES = re.compile(rb'ANTHROPIC_TIER_NAMES\s*=\s*\[([^\]]{0,400})\]')
CLAUDE_TIER_DESCRIPTIONS = re.compile(rb'TIER_DESCRIPTIONS\s*=\s*\{([^{}]{0,800})\}')
# At least three entries: the build also contains single-entry objects such as
# {default:"claude-haiku-4-5"}, and a one-pair pattern matches one of those first and
# reports a complete-looking result. The parsed keys are cross-checked against the tier
# names below, so a different multi-entry object cannot pass either.
CLAUDE_TIER_DEFAULTS = re.compile(
    rb'\{\s*[a-z]{3,10}\s*:\s*"claude-[^"]{1,60}"(?:\s*,\s*[a-z]{3,10}\s*:\s*"claude-[^"]{1,60}"){2,}\s*\}'
)
_JS_STRING = re.compile(r'"([^"]{1,60})"')
_JS_PAIR = re.compile(r'([A-Za-z_$][\w$]*|"[^"]{1,40}")\s*:\s*"([^"]{0,200})"')


@dataclass(frozen=True)
class ClaudeTier:
    """One `--model` alias the installed Claude Code build knows about."""

    alias: str
    description: str
    model_id: str


def claude_tier_cache_path() -> Path:
    override = os.environ.get("ADAPTIVE_MODEL_ROUTER_STATE_DIR")
    root = Path(override) if override else Path.home() / ".claude" / "adaptive-model-router"
    return root / "claude-tiers.json"


def _scan_binary(path: Path, patterns: dict[str, re.Pattern[bytes]]) -> dict[str, bytes]:
    """Stream the executable looking for each pattern; a 220 MB build takes ~0.3 s.

    Read in overlapping windows: a match that straddles a chunk boundary is invisible to
    a loop that hands each chunk to the regex on its own.
    """
    found: dict[str, bytes] = {}
    chunk, overlap, tail = 8 << 20, 8192, b""
    try:
        with path.open("rb") as handle:
            while len(found) < len(patterns):
                block = handle.read(chunk)
                if not block:
                    break
                window = tail + block
                for key, pattern in patterns.items():
                    if key in found:
                        continue
                    match = pattern.search(window)
                    if match:
                        found[key] = match.group(match.lastindex or 0)
                tail = window[-overlap:]
    except OSError:
        return {}
    return found


def _tiers_from_build(path: Path) -> tuple[ClaudeTier, ...]:
    found = _scan_binary(path, {
        "names": CLAUDE_TIER_NAMES,
        "descriptions": CLAUDE_TIER_DESCRIPTIONS,
        "defaults": CLAUDE_TIER_DEFAULTS,
    })
    if not found:
        return ()
    names = _JS_STRING.findall(found.get("names", b"").decode("utf-8", "replace"))
    if not names:
        return ()
    descriptions = {
        alias: text for alias, text
        in _JS_PAIR.findall(found.get("descriptions", b"").decode("utf-8", "replace"))
        if alias in names
    }
    model_ids = {
        alias: text for alias, text
        in _JS_PAIR.findall(found.get("defaults", b"").decode("utf-8", "replace"))
        if alias in names
    }
    # An object that parsed but describes something else is worse than no object: it would
    # report a confident, wrong version for every tier. Two agreeing keys is the floor.
    if len(model_ids) < 2:
        model_ids = {}
    # A tier the build names but exposes no default model for is not selectable - Claude
    # Code cannot resolve `--model <alias>` without one - so it is discovered, not offered.
    return tuple(
        ClaudeTier(name, descriptions.get(name, ""), model_ids.get(name, ""))
        for name in names
    )


def _build_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{path}|{stat.st_size}|{int(stat.st_mtime)}"


def discover_claude_tiers(command: str = "claude", refresh: bool = False) -> tuple[ClaudeTier, ...]:
    """Tiers the installed Claude Code build knows about, cached against the build itself.

    The cache key is the executable's path, size and mtime, so an update re-scans and
    nothing else does; the hook must not spend a filesystem sweep on every prompt.
    """
    executable = Path(resolve_command(command))
    fingerprint = _build_fingerprint(executable)
    cache_path = claude_tier_cache_path()
    if not refresh and fingerprint:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("build") == fingerprint:
                return tuple(ClaudeTier(**tier) for tier in cached["tiers"])
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if not fingerprint:
        return ()
    tiers = _tiers_from_build(executable)
    if tiers:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"build": fingerprint, "tiers": [vars(tier) for tier in tiers]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except OSError:
            pass
    return tiers


DEFAULT_CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# `claude --help` names a couple of aliases as *examples* ("e.g. 'fable', 'opus', or
# 'sonnet'"), never the full set, and it prints the effort ladder in full.
CLAUDE_ALIAS_EXAMPLES = re.compile(
    r"Provide\s+an\s+alias\s+for\s+the\s+latest\s+model\s*\(e\.g\.\s*([^\)]+)\)", re.I,
)
CLAUDE_EFFORT_LEVELS = re.compile(
    r"--effort\s+<level>\s+Effort\s+level\s+for\s+the\s+current\s+session\s*\(([^\)]+)\)", re.I,
)


def _claude_provider(config: dict[str, Any] | None) -> dict[str, Any]:
    return (config or {}).get("providers", {}).get("claude", {})


def _configured_claude_efforts(config: dict[str, Any] | None) -> tuple[str, ...]:
    configured = _claude_provider(config).get("default_efforts")
    return tuple(str(value).casefold() for value in configured) if configured else DEFAULT_CLAUDE_EFFORTS


def _split_advertised(value: str) -> list[str]:
    return [item.strip(" '\"") for item in re.split(r",|\bor\b", value) if item.strip(" '\"")]


def _configured_claude_models(config: dict[str, Any] | None, efforts: tuple[str, ...]) -> list[ModelInfo]:
    families = _claude_provider(config).get("verified_alias_families", {})
    return [
        ModelInfo(alias, alias, values.get("description", "configured Claude model alias"), efforts, priority)
        for priority, (alias, values) in enumerate(families.items(), start=1)
    ]


def load_claude_catalog(
    command: str = "claude", config: dict[str, Any] | None = None, refresh: bool = False,
) -> CatalogResult:
    """Claude Code publishes no machine-readable model catalog, so the configured tier table
    is the catalog and `--help` is only used to confirm and extend it.

    The help text lists example aliases, not the alias set, so a refresh that replaced the
    table with what it parsed dropped every tier the examples omitted - and a tier missing
    from the catalog is a tier the router silently reassigns to another model.
    """
    efforts = _configured_claude_efforts(config)
    models = _configured_claude_models(config, efforts)
    if not refresh:
        return CatalogResult("AVAILABLE" if models else "UNKNOWN", tuple(models))

    resolved_command = resolve_command(command)
    try:
        completed = subprocess.run(
            [resolved_command, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CatalogResult("UNKNOWN", (), str(exc))
    combined = f"{completed.stdout}\n{completed.stderr}"
    if any(marker in combined.upper() for marker in AUTH_MARKERS):
        return CatalogResult("AUTH_REQUIRED", (), combined.strip())
    if completed.returncode != 0:
        return CatalogResult("UNKNOWN", (), combined.strip())

    effort_match = CLAUDE_EFFORT_LEVELS.search(completed.stdout)
    if effort_match:
        advertised = tuple(value.strip().casefold() for value in effort_match.group(1).split(",") if value.strip())
        if advertised and advertised != efforts:
            efforts = advertised
            models = _configured_claude_models(config, efforts)

    alias_match = CLAUDE_ALIAS_EXAMPLES.search(completed.stdout)
    known = {model.slug.casefold() for model in models}
    # The build's own tier table is the complete list; --help only ever gives examples.
    advertised = _split_advertised(alias_match.group(1)) if alias_match else []
    discovered = [
        tier.alias for tier in discover_claude_tiers(command, refresh=True)
        # A tier with no default model id is named by the build but not selectable:
        # `--model <alias>` has nothing to resolve to, so offering it would route to a
        # model the CLI then rejects.
        if tier.model_id
    ]
    unknown = [
        alias for alias in dict.fromkeys(discovered + advertised)
        if alias.casefold() not in known
    ]
    # Unknown aliases rank behind every configured tier, so one the router cannot classify
    # never becomes a profile's first choice; it is reported instead.
    lowest = len(models)
    models.extend(
        ModelInfo(alias, alias, "runtime-advertised Claude model alias", efforts, lowest + index)
        for index, alias in enumerate(unknown, start=1)
    )
    message = (
        f"Claude Code exposes tiers this configuration does not classify: {', '.join(unknown)}. "
        "They rank behind every configured tier until a profile is assigned to them."
        if unknown else
        ("" if alias_match else "This CLI version advertises no model aliases in --help.")
    )
    return CatalogResult("AVAILABLE" if models else "UNKNOWN", tuple(models), message)


def load_current_claude_config(path: Path | None = None) -> tuple[str | None, str | None]:
    config_path = path or Path.home() / ".claude" / "settings.json"
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload.get("model"), payload.get("effortLevel") or payload.get("effort")
    except (OSError, json.JSONDecodeError):
        return None, None


def load_status_overrides(path: Path | None, provider: str = "codex") -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    provider_payload = payload.get("providers", {}).get(provider, payload)
    return {str(key): value for key, value in provider_payload.get("models", {}).items()}


def expand_path(value: str) -> Path:
    return Path(value).expanduser()


def family_definitions(config: dict[str, Any], provider: str = "codex") -> dict[str, dict[str, Any]]:
    return config.get("providers", {}).get(provider, {}).get("families", {})


def default_family(config: dict[str, Any], provider: str = "codex") -> str | None:
    families = family_definitions(config, provider)
    if not families:
        return None
    configured = config["providers"][provider].get("default_family")
    return configured if configured in families else next(iter(families))


def detect_family(slug: str | None, config: dict[str, Any], provider: str = "codex") -> str | None:
    """Return the model family a slug belongs to, using the configured slug patterns.

    The provider wrapper launches Codex with `--model deepseek-flash` instead of writing
    a marker file, so the running model's slug is the only reliable in-session signal of
    which family is actually serving the request.
    """
    if not slug:
        return None
    for family, definition in family_definitions(config, provider).items():
        if any(re.search(pattern, slug, re.I) for pattern in definition.get("slug_patterns", [])):
            return family
    return None


def load_family_catalog(
    family: str, config: dict[str, Any], provider: str = "codex", refresh: bool = False,
    command: str = "codex", cache_path: Path | None = None,
) -> CatalogResult:
    """Load one family's local model catalog; every family uses the Codex catalog schema."""
    definition = family_definitions(config, provider).get(family)
    if definition is None:
        return CatalogResult("UNKNOWN", (), f"Unknown model family: {family}")
    path = cache_path or expand_path(str(definition.get("catalog_path", "")))
    allow_refresh = refresh and bool(definition.get("refresh_via_cli"))
    return load_codex_catalog(command, refresh=allow_refresh, cache_path=path)


def load_family_catalogs(
    config: dict[str, Any], provider: str = "codex", refresh: bool = False, command: str = "codex",
) -> dict[str, CatalogResult]:
    return {
        family: load_family_catalog(family, config, provider, refresh, command)
        for family in family_definitions(config, provider)
    }


LEGACY_FAMILY = ""


def resolve_active_catalogs(
    config: dict[str, Any], current_model: str | None, provider: str = "codex",
    refresh: bool = False, command: str = "codex",
) -> tuple[str | None, dict[str, CatalogResult]]:
    """Return the family serving this session and every family's catalog.

    A config predating the family block still has to route, so the absence of families falls
    back to the single legacy catalog under `LEGACY_FAMILY`, which yields no counterparts.
    """
    if not family_definitions(config, provider):
        return None, {LEGACY_FAMILY: load_codex_catalog(command, refresh=refresh)}
    catalogs = load_family_catalogs(config, provider, refresh, command)
    active = detect_family(current_model, config, provider) or default_family(config, provider)
    return active, catalogs
