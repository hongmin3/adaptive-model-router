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

from .config import PACKAGED_CONFIG, user_config_path


REPOSITORY = "hongmin3/adaptive-model-router"
ASSET_NAME = "adaptive-model-router-codex-windows-x64.zip"
TASK_NAME = "AdaptiveModelRouterUpdate"
ARCHIVE_TARGETS = {
    "codex.exe": Path("bin/codex.exe"),
    "bin/codex.exe": Path("bin/codex.exe"),
    "codex-code-mode-host.exe": Path("bin/codex-code-mode-host.exe"),
    "bin/codex-code-mode-host.exe": Path("bin/codex-code-mode-host.exe"),
    "rg.exe": Path("codex-path/rg.exe"),
    "codex-path/rg.exe": Path("codex-path/rg.exe"),
    "codex-command-runner.exe": Path("codex-resources/codex-command-runner.exe"),
    "codex-resources/codex-command-runner.exe": Path("codex-resources/codex-command-runner.exe"),
    "codex-windows-sandbox-setup.exe": Path("codex-resources/codex-windows-sandbox-setup.exe"),
    "codex-resources/codex-windows-sandbox-setup.exe": Path("codex-resources/codex-windows-sandbox-setup.exe"),
}


def install_root() -> Path:
    override = os.environ.get("ADAPTIVE_MODEL_ROUTER_INSTALL_DIR")
    return Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / "AdaptiveModelRouter"


def ensure_user_config() -> Path:
    destination = user_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(PACKAGED_CONFIG, destination)
    return destination


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


def install_archive(
    archive: Path,
    version: str,
    *,
    release_asset_digest: str | None = None,
) -> Path:
    root = install_root()
    backup_dir = root / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    installed_files: list[Path] = []
    with zipfile.ZipFile(archive) as bundle:
        members: dict[Path, str] = {}
        for name in bundle.namelist():
            normalized = name.replace("\\", "/").lstrip("./")
            target = ARCHIVE_TARGETS.get(normalized.casefold())
            if target is not None:
                members[target] = name
        if Path("bin/codex.exe") not in members:
            raise RuntimeError("Archive does not contain codex.exe")
        for relative, member in members.items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
            temporary = destination.with_suffix(".tmp")
            with bundle.open(member) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
            temporary.replace(destination)
            installed_files.append(relative)
    destination = root / "bin" / "codex.exe"
    (root / "installed.json").write_text(
        json.dumps(
            {
                "version": version,
                "release_asset_digest": release_asset_digest,
                "sha256": _sha256(destination),
                "files": [str(path).replace("\\", "/") for path in installed_files],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _prepend_user_path(root / "bin")
    ensure_user_config()
    return destination


def install_latest() -> tuple[Path | None, str]:
    release = latest_release()
    version = str(release["tag_name"])
    asset = _asset(release, ASSET_NAME)
    release_asset_digest = str(asset.get("digest") or asset.get("id") or "")
    state_path = install_root() / "installed.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("version") == version and state.get("release_asset_digest") == release_asset_digest:
            return None, version
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
        return install_archive(
            archive,
            version,
            release_asset_digest=release_asset_digest,
        ), version


def install_update_task() -> None:
    ensure_user_config()
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
    return {
        "installed": installed,
        "update_task": task.returncode == 0,
        "root": str(install_root()),
        "config": str(user_config_path()),
        "config_exists": user_config_path().exists(),
    }


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
