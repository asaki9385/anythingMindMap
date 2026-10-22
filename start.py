"""Cross-platform launcher: start uvicorn, wait for /api/health, open the browser."""

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except AttributeError:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = os.environ.get("PORT", "8000")
TIMEOUT = float(os.environ.get("START_TIMEOUT", "60"))
PROBE_HOST = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
URL = f"http://{PROBE_HOST}:{PORT}"
HEALTH_URL = f"{URL}/api/health"


def health_ok():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def wait_for_server(proc):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if health_ok():
            # A server on this port may answer while ours is still failing to bind.
            time.sleep(0.5)
            return proc.poll() is None
        time.sleep(0.5)
    return False


def stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    if health_ok():
        print(f"服务已在运行: {URL}", flush=True)
        if os.environ.get("NO_BROWSER") != "1":
            webbrowser.open(f"{URL}/ui/client.html")
        return 0

    print("正在启动 KnowledgeTree 服务...", flush=True)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "knowledge-compiler.server:app",
            "--host",
            HOST,
            "--port",
            PORT,
        ],
        cwd=BASE_DIR,
    )

    try:
        if not wait_for_server(proc):
            stop_process(proc)
            print(f"服务启动失败或超时（{TIMEOUT:.0f} 秒）。请查看上方日志。", file=sys.stderr)
            return 1

        print(f"服务已启动: {URL}", flush=True)
        if os.environ.get("NO_BROWSER") != "1":
            webbrowser.open(f"{URL}/ui/client.html")
        return proc.wait()
    except KeyboardInterrupt:
        print("\n正在关闭服务...", flush=True)
        stop_process(proc)
        print("已关闭。", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
