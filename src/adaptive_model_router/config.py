from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "router_config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
