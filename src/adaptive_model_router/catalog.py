from __future__ import annotations

import json
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


def load_claude_catalog(
    command: str = "claude", config: dict[str, Any] | None = None, refresh: bool = False,
) -> CatalogResult:
    families = (config or {}).get("providers", {}).get("claude", {}).get("verified_alias_families", {})
    if not refresh:
        models = [
            ModelInfo(alias, alias, values.get("description", "configured Claude model alias"),
                      ("low", "medium", "high", "xhigh", "max"), priority)
            for priority, (alias, values) in enumerate(families.items(), start=1)
        ]
        return CatalogResult("AVAILABLE" if models else "UNKNOWN", tuple(models))

    resolved_command = resolve_command(command)
    try:
        completed = subprocess.run(
            [resolved_command, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CatalogResult("UNKNOWN", (), str(exc))
    combined = f"{completed.stdout}\n{completed.stderr}"
    upper = combined.upper()
    if any(marker in upper for marker in AUTH_MARKERS):
        return CatalogResult("AUTH_REQUIRED", (), combined.strip())
    if completed.returncode != 0:
        return CatalogResult("UNKNOWN", (), combined.strip())

    match = re.search(
        r"Provide\s+an\s+alias\s+for\s+the\s+latest\s+model\s*\(e\.g\.\s*([^\)]+)\)",
        completed.stdout,
        re.I,
    )
    if not match:
        return CatalogResult("UNKNOWN", (), "Claude model aliases were not advertised by this CLI version.")
    aliases = [value.strip(" '\"") for value in re.split(r",|\bor\b", match.group(1)) if value.strip(" '\"")]
    models = []
    for priority, alias in enumerate(aliases, start=1):
        family = next((value for name, value in families.items() if name.casefold() in alias.casefold()), {})
        description = family.get("description", "runtime-advertised Claude model alias")
        models.append(ModelInfo(alias, alias, description, ("low", "medium", "high", "xhigh", "max"), priority))
    return CatalogResult("AVAILABLE" if models else "UNKNOWN", tuple(models))


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
