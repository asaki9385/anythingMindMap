#!/usr/bin/env python3
"""Build a self-contained macOS .app bundle and compressed DMG for AnythingMindmap.

Run on macOS. The release workflow passes a relocatable Python runtime produced
by uv/python-build-standalone. Dependencies are installed into the app bundle
with --target, so the DMG does not depend on the user's Python environment.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from build_windows_portable import (
    RUNTIME_DIRS,
    RUNTIME_DIR_FILE_FILTER,
    RUNTIME_FILES,
    ignore_runtime_junk,
)

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "AnythingMindmap.app"
BUNDLE_ID = "com.asaki9385.anythingmindmap"

LAUNCHER = r'''#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(cd "$HERE/../Resources" && pwd)"
DATA_ROOT="$HOME/Library/Application Support/AnythingMindmap"
LOG_DIR="$HOME/Library/Logs/AnythingMindmap"
LOG_FILE="$LOG_DIR/launcher.log"
VERSION="$(tr -d '\n' < "$RESOURCES/VERSION.txt" 2>/dev/null || echo dev)"
APP_DIR="$DATA_ROOT/app-$VERSION"

mkdir -p "$DATA_ROOT" "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1
printf '\n[%s] Starting AnythingMindmap %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$VERSION"

if [ ! -f "$APP_DIR/start.py" ]; then
  mkdir -p "$APP_DIR"
  rsync -a "$RESOURCES/app/" "$APP_DIR/"
  PREVIOUS_APP="$(find "$DATA_ROOT" -maxdepth 1 -type d -name 'app-*' ! -path "$APP_DIR" -print | sort | tail -n 1)"
  if [ -n "$PREVIOUS_APP" ] && [ -d "$PREVIOUS_APP/knowledge-compiler/data" ]; then
    mkdir -p "$APP_DIR/knowledge-compiler/data"
    rsync -a "$PREVIOUS_APP/knowledge-compiler/data/" "$APP_DIR/knowledge-compiler/data/"
  fi
fi

if [ -x "$RESOURCES/runtime/bin/python3" ]; then
  PYTHON="$RESOURCES/runtime/bin/python3"
else
  PYTHON="$(command -v python3 || true)"
fi

if [ -z "$PYTHON" ]; then
  osascript -e 'display alert "AnythingMindmap 启动失败" message "没有找到可用的 Python 3 运行时。请查看日志：~/Library/Logs/AnythingMindmap/launcher.log"'
  exit 1
fi

export PYTHONNOUSERSITE=1
export PYTHONPATH="$RESOURCES/site-packages"
export KT_SETTINGS_PATH="$DATA_ROOT/settings.json"

cd "$APP_DIR"
"$PYTHON" start.py &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -INT "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

set +e
wait "$SERVER_PID"
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  osascript -e 'display alert "AnythingMindmap 启动失败" message "请查看日志：~/Library/Logs/AnythingMindmap/launcher.log"'
fi
exit "$RC"
'''


def run(command: list[object], cwd: Path | None = None) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"+ {printable}", flush=True)
    subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        check=True,
    )


def copy_app_source(destination: Path) -> None:
    knowledge_dir = destination / "knowledge-compiler"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    for filename in RUNTIME_FILES:
        source = ROOT / "knowledge-compiler" / filename
        if not source.is_file():
            raise FileNotFoundError(f"Runtime file is missing: {source}")
        shutil.copy2(source, knowledge_dir / filename)

    for dirname in RUNTIME_DIRS:
        source = ROOT / "knowledge-compiler" / dirname
        if not source.is_dir():
            raise FileNotFoundError(f"Runtime directory is missing: {source}")
        allowed = RUNTIME_DIR_FILE_FILTER.get(dirname)
        if allowed is None:
            shutil.copytree(
                source,
                knowledge_dir / dirname,
                ignore=ignore_runtime_junk,
                symlinks=True,
            )
            continue
        target = knowledge_dir / dirname
        target.mkdir(parents=True, exist_ok=True)
        for filename in sorted(allowed):
            source_file = source / filename
            if not source_file.is_file():
                raise FileNotFoundError(f"Runtime file is missing: {source_file}")
            shutil.copy2(source_file, target / filename)

    (knowledge_dir / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "start.py", destination / "start.py")
    shutil.copy2(ROOT / "requirements.txt", destination / "requirements.txt")
    shutil.copy2(ROOT / "README.md", destination / "README.md")


def install_dependencies(runtime_python: Path, site_dir: Path) -> None:
    requirements = ROOT / "requirements.txt"
    site_dir.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        run([
            uv, "pip", "install",
            "--python", runtime_python,
            "--target", site_dir,
            "--no-cache",
            "-r", requirements,
        ])
        return
    run([
        runtime_python, "-m", "pip", "install",
        "--target", site_dir,
        "--no-cache-dir",
        "-r", requirements,
    ])


def stage_app(
    stage: Path,
    version: str,
    runtime_source: Path | None,
    skip_runtime: bool,
    skip_deps: bool,
) -> Path:
    app = stage / APP_NAME
    contents = app / "Contents"
    resources = contents / "Resources"
    macos = contents / "MacOS"
    app_source = resources / "app"
    resources.mkdir(parents=True, exist_ok=True)
    macos.mkdir(parents=True, exist_ok=True)
    copy_app_source(app_source)

    launcher = macos / "AnythingMindmap"
    launcher.write_text(LAUNCHER, encoding="utf-8", newline="\n")
    launcher.chmod(0o755)

    version_txt = resources / "VERSION.txt"
    version_txt.write_text(version + "\n", encoding="utf-8", newline="\n")
    (resources / "BUILD_INFO.txt").write_text(
        f"version={version}\n"
        f"commit={os.environ.get('GITHUB_SHA', 'unknown')}\n"
        f"built_at={datetime.now(timezone.utc).isoformat()}\n"
        f"arch={platform.machine()}\n",
        encoding="utf-8",
        newline="\n",
    )

    plist = {
        "CFBundleDisplayName": "AnythingMindmap",
        "CFBundleExecutable": "AnythingMindmap",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "AnythingMindmap",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": re.sub(r"[^0-9.]", "", version).strip(".") or "1.0",
        "CFBundleVersion": re.sub(r"[^0-9.]", "", version).strip(".") or "1.0",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(plist, f)

    if not skip_runtime:
        if runtime_source is None:
            raise RuntimeError("--runtime-source is required unless --skip-runtime is used")
        runtime_source = runtime_source.resolve()
        runtime_python = runtime_source / "bin" / "python3"
        if not runtime_python.is_file():
            raise FileNotFoundError(f"Bundled Python is missing: {runtime_python}")
        runtime_target = resources / "runtime"
        shutil.copytree(
            runtime_source,
            runtime_target,
            ignore=ignore_runtime_junk,
            symlinks=True,
        )
        if not skip_deps:
            install_dependencies(runtime_target / "bin" / "python3", resources / "site-packages")
    else:
        (resources / "site-packages").mkdir(parents=True, exist_ok=True)

    return app


def make_dmg(stage: Path, app: Path, output: Path, version: str) -> Path:
    dmg_root = stage / "dmg-root"
    dmg_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(app), str(dmg_root / APP_NAME))
    (dmg_root / "Applications").symlink_to("/Applications")
    (dmg_root / "README-MACOS.txt").write_text(
        "AnythingMindmap macOS 安装说明\n\n"
        "1. 将 AnythingMindmap.app 拖入 Applications。\n"
        "2. 首次打开若被 Gatekeeper 拦截，请右键选择“打开”。\n"
        "3. 应用会在 ~/Library/Application Support/AnythingMindmap 保存运行数据。\n"
        "4. 模型服务与 MinerU Token 请在应用内“设置”中配置。\n",
        encoding="utf-8",
    )

    run(["codesign", "--force", "--deep", "--sign", "-", dmg_root / APP_NAME])
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    run([
        "hdiutil", "create",
        "-volname", "AnythingMindmap",
        "-srcfolder", dmg_root,
        "-ov",
        "-format", "UDZO",
        output,
    ])
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build AnythingMindmap macOS DMG.")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=os.environ.get("GITHUB_REF_NAME", "dev"))
    parser.add_argument("--runtime-source", type=Path, default=None)
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--skip-deps", action="store_true")
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("macOS DMG must be built on macOS")

    arch = platform.machine()
    output = args.output.resolve()
    dmg_path = output / f"AnythingMindmap-macos-{arch}.dmg"

    with tempfile.TemporaryDirectory(prefix="anythingmindmap-dmg-") as tmp:
        stage = Path(tmp)
        app = stage_app(
            stage,
            args.version,
            args.runtime_source,
            args.skip_runtime,
            args.skip_deps,
        )
        make_dmg(stage, app, dmg_path, args.version)

    print(f"Built: {dmg_path}")
    print(f"Size: {dmg_path.stat().st_size / (1024 * 1024):.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
