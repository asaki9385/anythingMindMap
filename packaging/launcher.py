#!/usr/bin/env python3
"""Portable launcher for AnythingMindmap on Windows."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def health_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/api/health"


def wait_for_server(proc: subprocess.Popen, host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            print(f"Server exited before becoming ready (exit code {code}).", file=sys.stderr)
            return False

        try:
            with urllib.request.urlopen(health_url(host, port), timeout=1) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception as exc:  # connection refused while the server starts
            last_error = exc

        time.sleep(0.5)

    print(f"Server did not become ready within {timeout:.0f}s: {last_error}", file=sys.stderr)
    return False


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the AnythingMindmap local server.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        print("PORT must be between 1 and 65535.", file=sys.stderr)
        return 2

    app_dir = Path(__file__).resolve().parent
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "knowledge-compiler.server:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]

    probe_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host

    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    print(f"Starting AnythingMindmap on http://{browser_host}:{args.port} ...", flush=True)
    try:
        proc = subprocess.Popen(command, cwd=str(app_dir), env=env)
    except OSError as exc:
        print(f"Failed to start server: {exc}", file=sys.stderr)
        return 1

    try:
        if not wait_for_server(proc, probe_host, args.port, args.timeout):
            stop_process(proc)
            return 1

        print(f"Server is ready: http://{browser_host}:{args.port}", flush=True)
        if not args.no_browser:
            webbrowser.open(f"http://{browser_host}:{args.port}/")
        return proc.wait()
    except KeyboardInterrupt:
        print("\nStopping server ...", flush=True)
        stop_process(proc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
