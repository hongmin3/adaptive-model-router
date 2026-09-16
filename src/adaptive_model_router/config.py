from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PACKAGED_CONFIG = Path(__file__).with_name("router_config.json")
SOURCE_CONFIG = Path(__file__).resolve().parents[2] / "router_config.json"


def user_config_path() -> Path:
    override = os.environ.get("ADAPTIVE_MODEL_ROUTER_CONFIG")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AdaptiveModelRouter" / "router_config.json"
    return Path.home() / ".adaptive-model-router" / "router_config.json"


def default_config_path() -> Path:
    user_config = user_config_path()
    if user_config.exists():
        return user_config
    if SOURCE_CONFIG.exists():
        return SOURCE_CONFIG
    return PACKAGED_CONFIG


def _read(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _merge_defaults(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Fill keys the override does not define, recursing into nested objects only.

    The user copy is written once at install time and deliberately never overwritten, so a
    key added to the packaged defaults after that copy was made would otherwise never reach
    a running installation. Lists stay atomic: an edited `rules` array is the user's answer
    in full, and merging entries into it would resurrect rules they removed.
    """
    merged = dict(override)
    for key, value in base.items():
        if key not in merged:
            merged[key] = value
        elif isinstance(value, dict) and isinstance(merged[key], dict):
            merged[key] = _merge_defaults(value, merged[key])
    return merged


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_config_path()
    config = _read(config_path)
    if config_path == PACKAGED_CONFIG:
        return config
    try:
        return _merge_defaults(_read(PACKAGED_CONFIG), config)
    except (OSError, json.JSONDecodeError):
        return config
