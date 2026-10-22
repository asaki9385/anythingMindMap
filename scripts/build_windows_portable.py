#!/usr/bin/env python3
"""Build a runtime-only portable Windows ZIP for AnythingMindmap.

Run this on Windows (GitHub Actions windows-latest is recommended).  The script
copies a relocatable Python installation into the package, installs runtime
dependencies, stages only the files used by the server, removes demo
credentials from the copied settings module, and creates a ZIP.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR_NAME = "AnythingMindmap-windows-x64"

# Explicit runtime allowlist. Keep this list small; update it when the server
# starts importing another top-level module.
RUNTIME_FILES = [
    "server.py",
    "app_settings.py",
    "content_anchor.py",
    "document_title.py",
    "hierarchy_repair.py",
    "node_enhancer.py",
    "outline_skeleton.py",
    "parse_report.py",
    "placement_audit.py",
    "review_engine.py",
    "skeleton_builder.py",
    "structure_repair.py",
    "structure_score.py",
    "tree_builder.py",
]
RUNTIME_DIRS = ["ui", "parser", "mineru_adapter"]
RUNTIME_DIR_FILE_FILTER = {
    # convert.py is a developer utility; the server only imports client.py.
    "mineru_adapter": {"__init__.py", "client.py"},
}

SANITIZED_CHAT_BLOCK = '''BUILTIN_CHAT = {
    "provider_name": "未配置",
    "base_url": "",
    "model": "",
    "api_key": "",
}'''


def ignore_runtime_junk(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in {"__pycache__", ".DS_Store"}:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo", ".pdb")):
            ignored.add(name)
    return ignored


def run(command: list[object], cwd: Path | None = None) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"+ {printable}", flush=True)
    subprocess.run([str(part) for part in command], cwd=str(cwd) if cwd else None, check=True)


def sanitize_app_settings(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text, chat_count = re.subn(
        r"BUILTIN_CHAT\s*=\s*\{.*?\n\}",
        SANITIZED_CHAT_BLOCK,
        text,
        count=1,
        flags=re.DOTALL,
    )
    text, token_count = re.subn(
        r'BUILTIN_MINERU_TOKEN\s*=\s*"[^"]*"',
        'BUILTIN_MINERU_TOKEN = ""',
        text,
        count=1,
    )
    if chat_count != 1 or token_count != 1:
        raise RuntimeError("Could not locate BUILTIN_CHAT / BUILTIN_MINERU_TOKEN in app_settings.py")

    # A non-empty literal here would mean a secret leaked into the artifact.
    if re.search(r'"api_key"\s*:\s*"[^"]+"', text):
        raise RuntimeError("A non-empty built-in API key remains in the staged app_settings.py")
    if re.search(r'BUILTIN_MINERU_TOKEN\s*=\s*"[^"]+"', text):
        raise RuntimeError("A non-empty built-in MinerU token remains in the staged app_settings.py")

    path.write_text(text, encoding="utf-8", newline="\n")


def copy_text_with_crlf(source: Path, destination: Path) -> None:
    text = source.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    destination.write_bytes(text.encode("utf-8"))


def stage_app(stage: Path, version: str) -> Path:
    app_dir = stage / "app"
    knowledge_dir = app_dir / "knowledge-compiler"
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

        allowed_files = RUNTIME_DIR_FILE_FILTER.get(dirname)
        if allowed_files is None:
            shutil.copytree(source, knowledge_dir / dirname, ignore=ignore_runtime_junk)
            continue

        target = knowledge_dir / dirname
        target.mkdir(parents=True, exist_ok=True)
        for filename in sorted(allowed_files):
            file_source = source / filename
            if not file_source.is_file():
                raise FileNotFoundError(f"Runtime file is missing: {file_source}")
            shutil.copy2(file_source, target / filename)

    # Keep an empty runtime data directory. The server creates the remaining
    # subdirectories on demand, but this makes the data location obvious.
    (knowledge_dir / "data").mkdir(parents=True, exist_ok=True)

    sanitize_app_settings(knowledge_dir / "app_settings.py")

    shutil.copy2(ROOT / "packaging" / "launcher.py", app_dir / "launcher.py")
    copy_text_with_crlf(ROOT / "packaging" / "start.bat", stage / "start.bat")
    shutil.copy2(ROOT / "packaging" / "README-WINDOWS.txt", stage / "README-WINDOWS.txt")

    (stage / "VERSION.txt").write_text(version + "\n", encoding="utf-8", newline="\n")
    commit = os.environ.get("GITHUB_SHA", "unknown")
    build_time = datetime.now(timezone.utc).isoformat()
    (stage / "BUILD_INFO.txt").write_text(
        f"version={version}\ncommit={commit}\nbuilt_at={build_time}\npython={sys.version.split()[0]}\n",
        encoding="utf-8",
        newline="\n",
    )
    return app_dir


def copy_python_runtime(stage: Path, runtime_source: Path | None, install_dependencies: bool) -> Path:
    source = (runtime_source or Path(sys.base_prefix)).resolve()
    python_exe = source / "python.exe"
    if not python_exe.is_file():
        raise RuntimeError(
            f"{python_exe} was not found. Run this build on Windows or pass --runtime-source."
        )

    runtime_dir = stage / "runtime"
    shutil.copytree(source, runtime_dir, ignore=ignore_runtime_junk)
    packaged_python = runtime_dir / "python.exe"

    for license_name in ("LICENSE.txt", "LICENSE", "LICENSE.rst"):
        license_path = runtime_dir / license_name
        if license_path.is_file():
            license_dir = stage / "THIRD_PARTY_LICENSES"
            license_dir.mkdir(exist_ok=True)
            shutil.copy2(license_path, license_dir / "PYTHON.txt")
            break

    if install_dependencies:
        run(
            [
                packaged_python,
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "-r",
                ROOT / "requirements.txt",
            ],
            cwd=ROOT,
        )

    return packaged_python


def verify_staged_app(stage: Path, packaged_python: Path | None) -> None:
    if packaged_python is None:
        return

    app_dir = stage / "app"
    run(
        [
            packaged_python,
            "-c",
            "import fastapi, uvicorn, fitz, httpx, requests, multipart, docx, chardet",
        ],
        cwd=app_dir,
    )
    run(
        [
            packaged_python,
            "-c",
            "import sys; sys.path.insert(0, 'knowledge-compiler'); import server; print('server import ok')",
        ],
        cwd=app_dir,
    )
    run([packaged_python, app_dir / "launcher.py", "--help"], cwd=app_dir)


def make_zip(stage: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for directory in sorted(stage.rglob("*")):
            if not directory.is_dir():
                continue
            arcname = directory.relative_to(stage.parent).as_posix().rstrip("/") + "/"
            archive.writestr(arcname, b"")

        for file_path in sorted(stage.rglob("*")):
            if not file_path.is_file():
                continue
            arcname = file_path.relative_to(stage.parent).as_posix()
            archive.write(file_path, arcname)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the AnythingMindmap Windows portable ZIP.")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=os.environ.get("GITHUB_REF_NAME", "dev"))
    parser.add_argument("--runtime-source", type=Path, default=None)
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Stage the app only; useful for local validation on non-Windows hosts.",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Do not run pip/import checks (only valid with a prepared runtime).",
    )
    args = parser.parse_args()

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / PACKAGE_DIR_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    stage_app(stage, args.version)

    packaged_python: Path | None = None
    if not args.skip_runtime:
        packaged_python = copy_python_runtime(
            stage,
            args.runtime_source,
            install_dependencies=not args.skip_deps,
        )
    elif args.skip_deps is False:
        print("Skipping dependency and import checks because --skip-runtime was requested.", flush=True)

    if packaged_python is not None and not args.skip_deps:
        verify_staged_app(stage, packaged_python)

    zip_path = output_dir / f"{PACKAGE_DIR_NAME}.zip"
    make_zip(stage, zip_path)
    print(f"\nBuilt: {zip_path}", flush=True)
    print(f"Size: {zip_path.stat().st_size / (1024 * 1024):.1f} MiB", flush=True)
    print(f"Staged folder: {stage}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
