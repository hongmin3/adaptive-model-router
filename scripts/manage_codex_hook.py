from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


MARKER = "adaptive_model_router.hook"


def hook_file(home: Path) -> Path:
    return home / ".codex" / "hooks.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"description": "User-level Codex hooks", "hooks": {}}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("hooks.json root must be an object")
    payload.setdefault("hooks", {})
    return payload


def _is_router_entry(group: dict[str, Any]) -> bool:
    return any(MARKER in str(hook.get("command", "")) or MARKER in str(hook.get("commandWindows", ""))
               for hook in group.get("hooks", []))


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = path.parent / "adaptive-model-router" / "backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    destination = backup_dir / path.name
    shutil.copy2(path, destination)
    return destination


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def install(home: Path, python_executable: str) -> tuple[Path | None, bool]:
    path = hook_file(home)
    payload = _load(path)
    groups = payload["hooks"].setdefault("UserPromptSubmit", [])
    if any(_is_router_entry(group) for group in groups):
        return None, False
    backup = _backup(path)
    quoted_python = f'"{python_executable}"'
    groups.append({
        "hooks": [{
            "type": "command",
            "command": "python -m adaptive_model_router.hook",
            "commandWindows": f"{quoted_python} -m adaptive_model_router.hook",
            "timeout": 10,
            "statusMessage": "Selecting model locally",
        }]
    })
    _write_atomic(path, payload)
    return backup, True


def uninstall(home: Path) -> tuple[Path | None, bool]:
    path = hook_file(home)
    payload = _load(path)
    groups = payload["hooks"].get("UserPromptSubmit", [])
    retained = [group for group in groups if not _is_router_entry(group)]
    if len(retained) == len(groups):
        return None, False
    backup = _backup(path)
    if retained:
        payload["hooks"]["UserPromptSubmit"] = retained
    else:
        payload["hooks"].pop("UserPromptSubmit", None)
    _write_atomic(path, payload)
    return backup, True


def status(home: Path) -> bool:
    payload = _load(hook_file(home))
    return any(_is_router_entry(group) for group in payload["hooks"].get("UserPromptSubmit", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or remove the global Codex router hook")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.status:
        installed = status(args.home)
        print(f"Adaptive Model Router hook: {'INSTALLED' if installed else 'NOT INSTALLED'}")
        return 0 if installed else 1
    backup, changed = install(args.home, sys.executable) if args.install else uninstall(args.home)
    print("Hook configuration updated." if changed else "No change needed.")
    if backup:
        print(f"Backup: {backup}")
    print(f"Config: {hook_file(args.home)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
