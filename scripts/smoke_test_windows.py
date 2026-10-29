#!/usr/bin/env python3
"""Smoke test the packaged Windows ZIP after extraction."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PACKAGE_DIR_NAME = "AnythingMindmap-windows-x64"


def http_ok(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=2) as response:
        return 200 <= response.status < 300


def fail(message: str) -> None:
    raise SystemExit(f"SMOKE TEST FAILED: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the Windows portable ZIP.")
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if os.name != "nt":
        fail("run this smoke test on Windows (or in the windows-latest Actions job)")

    zip_path = args.zip.resolve()
    if not zip_path.is_file():
        fail(f"ZIP not found: {zip_path}")

    with tempfile.TemporaryDirectory(prefix="Anything Mindmap 测试 ") as temp_dir:
        extract_root = Path(temp_dir)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)

        package_root = extract_root / PACKAGE_DIR_NAME
        if not package_root.is_dir():
            fail(f"missing top-level folder: {PACKAGE_DIR_NAME}")

        runtime_python = package_root / "runtime" / "python.exe"
        if not runtime_python.is_file():
            fail("runtime/python.exe is missing")
        if not (package_root / "app" / "knowledge-compiler" / "server.py").is_file():
            fail("app/knowledge-compiler/server.py is missing")
        if not (package_root / "app" / "launcher.py").is_file():
            fail("app/launcher.py is missing")
        if not (package_root / "start.bat").is_file():
            fail("start.bat is missing")
        if b"\r\n" not in (package_root / "start.bat").read_bytes():
            fail("start.bat must use CRLF line endings")

        forbidden_names = {"settings.json"}
        for path in package_root.rglob("*"):
            if path.name in forbidden_names:
                fail(f"forbidden runtime data file: {path.relative_to(package_root)}")
            if path.name.startswith(("test_", "benchmark_", "debug_")):
                fail(f"development file leaked into package: {path.relative_to(package_root)}")
            if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
                fail(f"bytecode/cache leaked into package: {path.relative_to(package_root)}")
            relative = str(path.relative_to(package_root))
            if any(token in relative for token in (".deck-", "open-claude-ui-main", "_archive", "design-qa.md")):
                fail(f"non-runtime asset leaked into package: {relative}")

        settings_text = (package_root / "app" / "knowledge-compiler" / "app_settings.py").read_text(
            encoding="utf-8"
        )
        if re.search(r'"api_key"\s*:\s*"[^"]+"', settings_text):
            fail("non-empty built-in API key found in packaged app_settings.py")
        if re.search(r'BUILTIN_MINERU_TOKEN\s*=\s*"[^"]+"', settings_text):
            fail("non-empty built-in MinerU token found in packaged app_settings.py")
        if "BUILTIN_MINERU_TOKEN = \"\"" not in settings_text:
            fail("packaged app_settings.py was not sanitized")

        app_dir = package_root / "app"
        runtime_env = os.environ.copy()
        runtime_env.pop("PYTHONHOME", None)
        runtime_env.pop("PYTHONPATH", None)
        runtime_env["PYTHONNOUSERSITE"] = "1"

        subprocess.run(
            [
                str(runtime_python),
                "-c",
                "import fastapi, uvicorn, pymupdf, httpx, requests, multipart, docx, chardet",
            ],
            cwd=str(app_dir),
            env=runtime_env,
            check=True,
        )
        subprocess.run([str(runtime_python), str(app_dir / "launcher.py"), "--help"], cwd=str(app_dir), check=True)

        launch_env = runtime_env.copy()
        launch_env["HOST"] = "127.0.0.1"
        launch_env["PORT"] = str(args.port)
        launch_env["NO_BROWSER"] = "1"

        server = subprocess.Popen(
            ["cmd.exe", "/c", "start.bat"],
            cwd=str(package_root),
            env=launch_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    output = server.stdout.read() if server.stdout else ""
                    fail(f"start.bat exited during startup\n{output}")
                try:
                    if http_ok(f"http://127.0.0.1:{args.port}/api/health"):
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                fail("start.bat did not bring the server up within 60 seconds")

            if not http_ok(f"http://127.0.0.1:{args.port}/ui/client.html"):
                fail("UI endpoint did not return a successful response")
        finally:
            if server.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()

    print("SMOKE TEST PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
