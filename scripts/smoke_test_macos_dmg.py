#!/usr/bin/env python3
"""Mount a built macOS DMG, launch its app with an isolated HOME, and smoke test it."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health",
            timeout=1,
        ) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def wait_for_app(proc: subprocess.Popen, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if health_ok(port):
            return True
        time.sleep(0.5)
    return False


def stop_app(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=12)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test AnythingMindmap macOS DMG.")
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    dmg = args.dmg.resolve()
    if not dmg.is_file():
        raise SystemExit(f"DMG not found: {dmg}")

    with tempfile.TemporaryDirectory(prefix="anythingmindmap-dmg-smoke-") as tmp:
        root = Path(tmp)
        mount = root / "mount"
        home = root / "home"
        mount.mkdir()
        home.mkdir()
        attached = False
        proc = None
        try:
            subprocess.run([
                "hdiutil", "attach", str(dmg),
                "-nobrowse", "-readonly", "-mountpoint", str(mount),
            ], check=True, stdout=subprocess.DEVNULL)
            attached = True

            app = mount / "AnythingMindmap.app"
            launcher = app / "Contents" / "MacOS" / "AnythingMindmap"
            if not launcher.is_file():
                raise RuntimeError(f"Launcher missing in DMG: {launcher}")

            subprocess.run([
                "codesign", "--verify", "--deep", "--strict", str(app),
            ], check=True)

            port = free_port()
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "NO_BROWSER": "1",
                "PORT": str(port),
                "START_TIMEOUT": str(args.timeout),
            })
            proc = subprocess.Popen(
                [str(launcher)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            if not wait_for_app(proc, port, args.timeout):
                output = ""
                if proc.poll() is not None:
                    output = proc.stdout.read() if proc.stdout else ""
                log = home / "Library" / "Logs" / "AnythingMindmap" / "launcher.log"
                if log.is_file():
                    output += "\n" + log.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"App failed to become healthy\n{output[-3000:]}")

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/ui/client.html",
                timeout=3,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"UI returned HTTP {response.status}")

            app_data = home / "Library" / "Application Support" / "AnythingMindmap"
            if not any(app_data.glob("app-*")):
                raise RuntimeError("App data directory was not created")

            print(f"PASS health: http://127.0.0.1:{port}/api/health")
            print(f"PASS UI: http://127.0.0.1:{port}/ui/client.html")
            print(f"PASS data: {app_data}")
        finally:
            if proc is not None:
                stop_app(proc)
            if attached:
                subprocess.run(
                    ["hdiutil", "detach", str(mount), "-force"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    print("macOS DMG smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
