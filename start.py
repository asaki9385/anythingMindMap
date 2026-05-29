import subprocess
import webbrowser
import time
import sys
import os

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = os.environ.get("PORT", "8000")
URL = f"http://localhost:{PORT}"

print(f"正在启动 KnowledgeTree 服务...")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "knowledge-compiler.server:app", "--host", HOST, "--port", PORT],
    cwd=os.path.dirname(os.path.abspath(__file__)),
)

time.sleep(2)
print(f"服务已启动，正在打开浏览器: {URL}")
webbrowser.open(URL)

try:
    proc.wait()
except KeyboardInterrupt:
    print("\n正在关闭服务...")
    proc.terminate()
    proc.wait()
    print("已关闭。")
