from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY = "hongmin3/adaptive-model-router"
ASSET_NAME = "adaptive-model-router-codex-windows-x64.zip"
TASK_NAME = "AdaptiveModelRouterUpdate"


def install_root() -> Path:
    override = os.environ.get("ADAPTIVE_MODEL_ROUTER_INSTALL_DIR")
    return Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / "AdaptiveModelRouter"


def _github_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "adaptive-model-router"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def latest_release() -> dict[str, Any]:
    return _github_json(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")


def _asset(release: dict[str, Any], name: str) -> dict[str, Any]:
    for item in release.get("assets", []):
        if item.get("name") == name:
            return item
    raise RuntimeError(f"Release asset not found: {name}")


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "adaptive-model-router"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepend_user_path(path: Path) -> None:
    current = subprocess.run(
        ["reg", "query", r"HKCU\Environment", "/v", "Path"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    value = ""
    if current.returncode == 0:
        for line in current.stdout.splitlines():
            if "REG_" in line:
                value = line.split("REG_", 1)[1].split(None, 1)[-1].strip()
                break
    target = str(path.resolve())
    entries = [entry for entry in value.split(";") if entry and Path(entry).resolve() != Path(target)]
    new_value = ";".join([target, *entries])
    subprocess.run(
        ["reg", "add", r"HKCU\Environment", "/v", "Path", "/t", "REG_EXPAND_SZ", "/d", new_value, "/f"],
        check=True,
        capture_output=True,
    )
    os.environ["PATH"] = os.pathsep.join([target, os.environ.get("PATH", "")])


def install_archive(archive: Path, version: str) -> Path:
    root = install_root()
    bin_dir = root / "bin"
    backup_dir = root / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    bin_dir.mkdir(parents=True, exist_ok=True)
    destination = bin_dir / "codex.exe"
    if destination.exists():
        backup_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(destination, backup_dir / destination.name)
    with zipfile.ZipFile(archive) as bundle:
        member = next((name for name in bundle.namelist() if Path(name).name == "codex.exe"), None)
        if member is None:
            raise RuntimeError("Archive does not contain codex.exe")
        with bundle.open(member) as source:
            temporary = destination.with_suffix(".tmp")
            with temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
            temporary.replace(destination)
    (root / "installed.json").write_text(
        json.dumps({"version": version, "sha256": _sha256(destination)}, indent=2) + "\n",
        encoding="utf-8",
    )
    _prepend_user_path(bin_dir)
    return destination


def install_latest() -> tuple[Path | None, str]:
    release = latest_release()
    version = str(release["tag_name"])
    state_path = install_root() / "installed.json"
    if state_path.exists() and json.loads(state_path.read_text(encoding="utf-8")).get("version") == version:
        return None, version
    asset = _asset(release, ASSET_NAME)
    checksum_asset = _asset(release, ASSET_NAME + ".sha256")
    with tempfile.TemporaryDirectory(prefix="adaptive-model-router-") as temp:
        archive = Path(temp) / ASSET_NAME
        checksum = Path(temp) / (ASSET_NAME + ".sha256")
        _download(str(asset["browser_download_url"]), archive)
        _download(str(checksum_asset["browser_download_url"]), checksum)
        expected = checksum.read_text(encoding="utf-8").split()[0].casefold()
        actual = _sha256(archive).casefold()
        if actual != expected:
            raise RuntimeError("Release checksum verification failed")
        return install_archive(archive, version), version


def install_update_task() -> None:
    command = f'"{sys.executable}" -m adaptive_model_router.native_manager --install-latest'
    subprocess.run(
        [
            "schtasks", "/Create", "/TN", TASK_NAME, "/SC", "DAILY", "/ST", "10:00",
            "/TR", command, "/F",
        ],
        check=True,
        capture_output=True,
    )


def status() -> dict[str, Any]:
    state_path = install_root() / "installed.json"
    installed = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    task = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True, errors="replace"
    )
    return {"installed": installed, "update_task": task.returncode == 0, "root": str(install_root())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install and update the native adaptive Codex build")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--install-latest", action="store_true")
    actions.add_argument("--install-archive", type=Path)
    actions.add_argument("--install-update-task", action="store_true")
    actions.add_argument("--status", action="store_true")
    parser.add_argument("--version", default="local")
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.install_update_task:
        install_update_task()
        print(f"Scheduled task installed: {TASK_NAME}")
        return 0
    if args.install_archive:
        print(install_archive(args.install_archive, args.version))
        return 0
    installed, version = install_latest()
    print(f"Already current: {version}" if installed is None else f"Installed {version}: {installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
