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


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_config_path()
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
