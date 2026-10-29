#!/usr/bin/env python3
"""Smoke test source-folder auto setup on Windows."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"WINDOWS SOURCE SMOKE TEST FAILED: {message}")


def http_ok(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=3) as response:
        return 200 <= response.status < 300


def runtime_env_for(root: Path) -> dict:
    """复现启动时给 Python 的最小环境，避免命中用户级 site-packages。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["HOST"] = "127.0.0.1"
    return env


def ignore_test_copy(directory: str, names: list[str]) -> set[str]:
    directory_path = Path(directory)
    ignored = {
        name
        for name in names
        if name in {".git", ".runtime", "__pycache__", "dist", "node_modules"}
    }
    if directory_path.name == "knowledge-compiler" and "data" in names:
        ignored.add("data")
    return ignored


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Windows source auto setup.")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--mode",
        choices=["embedded", "auto"],
        default="embedded",
        help=(
            "embedded: force the project-local embedded Python (fast, deterministic). "
            "auto: let the launcher choose, which exercises system-Python detection, "
            "the venv path and the installer fallback chain."
        ),
    )
    args = parser.parse_args()

    if os.name != "nt":
        fail("run this smoke test on Windows or in the windows-latest Actions job")

    with tempfile.TemporaryDirectory(prefix="AnythingMindmap 自动环境测试 ") as temp_dir:
        test_root = Path(temp_dir) / "mindmap"
        shutil.copytree(ROOT, test_root, ignore=ignore_test_copy)
        log_path = Path(temp_dir) / "bootstrap-smoke.log"

        env = os.environ.copy()
        env["HOST"] = "127.0.0.1"
        env["PORT"] = str(args.port)
        env["NO_BROWSER"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        if args.mode == "embedded":
            env["MINDMAP_FORCE_EMBEDDED_PYTHON"] = "1"
        else:
            # 不预设，走和用户双击 start.bat 完全相同的决策路径。
            env.pop("MINDMAP_FORCE_EMBEDDED_PYTHON", None)
            env.pop("MINDMAP_FORCE_LOCAL_PYTHON", None)
            env.pop("MINDMAP_PYTHON", None)

        server = None
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            server = subprocess.Popen(
                ["cmd.exe", "/c", "start.bat"],
                cwd=str(test_root),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

            try:
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        log_file.flush()
                        output = log_path.read_text(encoding="utf-8", errors="replace")
                        fail(f"start.bat exited during environment setup\n{output}")
                    try:
                        if http_ok(f"http://127.0.0.1:{args.port}/api/health"):
                            break
                    except Exception:
                        time.sleep(1)
                else:
                    log_file.flush()
                    output = log_path.read_text(encoding="utf-8", errors="replace")
                    fail(
                        f"start.bat did not become healthy within {args.timeout} seconds\n{output}"
                    )

                # 不论哪种模式，这两样是"依赖装好了"的硬证据。
                shared_required = [
                    test_root / ".runtime" / "bootstrap" / "requirements.sha256",
                    test_root / ".runtime" / "bootstrap" / "selected-python.txt",
                ]
                for path in shared_required:
                    if not path.is_file():
                        fail(f"auto setup did not create {path.relative_to(test_root)}")

                # 解释器形态随模式而变：embedded 必须产出项目内嵌入式 Python，
                # auto 允许落到 venv（复用系统 Python），此时不应强制要求 .runtime\python。
                if args.mode == "embedded":
                    required = [
                        test_root / ".runtime" / "python" / "python.exe",
                        test_root / ".runtime" / "python" / ".mindmap-embedded",
                    ]
                    for path in required:
                        if not path.is_file():
                            fail(f"auto setup did not create {path.relative_to(test_root)}")
                else:
                    venv_python = test_root / ".runtime" / "venv" / "Scripts" / "python.exe"
                    embedded_python = test_root / ".runtime" / "python" / "python.exe"
                    if not venv_python.is_file() and not embedded_python.is_file():
                        fail(
                            "auto mode produced neither a project venv nor an embedded Python; "
                            "the launcher did not finish setting up an interpreter"
                        )

                # selected-python.txt 第二行是实际用于装依赖的解释器，必须真的存在。
                selected = (test_root / ".runtime" / "bootstrap" / "selected-python.txt")
                lines = [ln.strip() for ln in selected.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                if len(lines) < 2:
                    fail("selected-python.txt is missing the interpreter actually used for dependencies")
                used_python = Path(lines[1])
                if not used_python.is_file():
                    fail(f"selected interpreter does not exist: {used_python}")

                # 用装好的解释器复验依赖：这是"第一次就装对了"的直接证据。
                probe = subprocess.run(
                    [str(used_python), "-c", "import fastapi, uvicorn, pymupdf, httpx, requests, multipart, docx, chardet"],
                    cwd=str(test_root),
                    env=runtime_env_for(test_root),
                    capture_output=True,
                    text=True,
                )
                if probe.returncode != 0:
                    fail(
                        "the interpreter chosen by the launcher cannot import the required "
                        f"dependencies\n{probe.stdout}\n{probe.stderr}"
                    )

                server_probe = subprocess.run(
                    [str(used_python), "-c",
                     "import sys; sys.path.insert(0, 'knowledge-compiler'); import server"],
                    cwd=str(test_root),
                    env=runtime_env_for(test_root),
                    capture_output=True,
                    text=True,
                )
                if server_probe.returncode != 0:
                    fail(
                        "the application module cannot be imported after installation\n"
                        f"{server_probe.stdout}\n{server_probe.stderr}"
                    )

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
                        server.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        server.kill()

    print("WINDOWS SOURCE SMOKE TEST PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
