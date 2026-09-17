from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
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


def config_state(path: Path | None = None) -> dict[str, Any]:
    """Which configuration is actually in effect, and whether it predates the packaged one.

    The user copy is written once and never overwritten, and `_merge_defaults` keeps lists
    atomic, so a `rules` array or a `families` block added to the packaged defaults after
    that copy was made never reaches an existing installation.  Nothing fails when that
    happens - the router simply keeps routing by the older rules - so the mismatch has to
    be reported somewhere a person looks.
    """
    active = path or default_config_path()
    try:
        active_version = int(_read(active).get("version", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        active_version = 0
    try:
        packaged_version = int(_read(PACKAGED_CONFIG).get("version", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        packaged_version = 0
    return {
        "path": active,
        "version": active_version,
        "packaged_version": packaged_version,
        "stale": active != PACKAGED_CONFIG and active_version < packaged_version,
    }


def refresh_user_config(backup_root: Path | None = None) -> tuple[Path | None, Path]:
    """Replace the installed user config with the packaged defaults, keeping a backup.

    Returns (backup path or None, destination).  Deliberately a full replacement rather
    than a merge: the user copy is a whole answer, and merging a rules array would
    resurrect entries the user removed while still missing entries they never saw.
    """
    destination = user_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if destination.exists():
        root = backup_root or destination.parent / "backups"
        directory = root / datetime.now().strftime("%Y%m%d-%H%M%S")
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / destination.name
        shutil.copy2(destination, backup)
    shutil.copy2(PACKAGED_CONFIG, destination)
    return backup, destination
