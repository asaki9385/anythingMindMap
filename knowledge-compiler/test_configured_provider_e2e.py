#!/usr/bin/env python3
"""统一配置端到端：设置页保存服务商 → resolve_chat → 实际学习模式调用。

使用本机临时 OpenAI 兼容服务，不访问外网、不需要真实 API Key。
运行：python test_configured_provider_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient

import app_settings as settings
import server

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" | {detail}" if detail else ""))


class MockModelHandler(BaseHTTPRequestHandler):
    captured: list[dict] = []

    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        self.captured.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization", ""),
            "payload": payload,
        })

        if self.headers.get("Authorization") != "Bearer test-key":
            self.send_response(401)
            self.end_headers()
            return

        if payload.get("stream"):
            chunks = [
                'data: {"choices":[{"delta":{"content":"配置"},"finish_reason":null}]}\n\n',
                'data: {"choices":[{"delta":{"content":"成功"},"finish_reason":"stop"}]}\n\n',
                "data: [DONE]\n\n",
            ]
            body = "".join(chunks).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockModelHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    td = tempfile.TemporaryDirectory()
    old_path = settings.PATH
    old_enhanced_dir = server.ENHANCED_DIR
    old_key = os.environ.pop("OPENAI_API_KEY", None)
    old_base = os.environ.pop("OPENAI_BASE_URL", None)
    old_model = os.environ.pop("MODEL", None)
    try:
        root = Path(td.name)
        tree_dir = root / "trees"
        tree_dir.mkdir()
        (tree_dir / "demo_ai.json").write_text(json.dumps({
            "title": "人工智能基础",
            "children": [{
                "title": "第一章 机器学习基础",
                "content": "监督学习使用带标签数据训练模型。",
                "children": [],
            }],
        }, ensure_ascii=False), encoding="utf-8")

        settings.PATH = str(root / "settings.json")
        server.ENHANCED_DIR = str(tree_dir)
        client = TestClient(server.app)
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"

        print("[1] 通过统一配置保存服务商")
        resp = client.post("/api/settings", json={
            "op": "upsert_provider",
            "provider": {
                "name": "本地 Mock",
                "base_url": base_url,
                "model": "mock-model",
                "api_key": "test-key",
            },
        })
        saved = resp.json()
        check("保存接口成功", resp.status_code == 200 and saved.get("ok") is True,
              str(saved))
        provider = (saved.get("settings", {}).get("providers") or [{}])[0]
        check("服务商成为默认", provider.get("active") is True
              and provider.get("base_url", "").endswith("/chat/completions"),
              json.dumps(provider, ensure_ascii=False))
        check("接口只返回脱敏 Key",
              provider.get("has_key") is True and "test-key" not in json.dumps(saved),
              json.dumps(provider, ensure_ascii=False))

        print("[2] 设置页测试连接")
        tested = client.post("/api/settings", json={
            "op": "test_provider",
            "id": saved.get("id"),
        }).json()
        check("测试连接成功", tested.get("ok") is True and tested.get("reply") == "OK",
              json.dumps(tested, ensure_ascii=False))

        print("[3] 真实学习模式调用走已保存配置")
        learned = client.post("/api/review/learn", json={
            "file": "demo_ai.json",
            "question": "什么是监督学习？",
        })
        data = learned.json()
        check("学习接口成功", learned.status_code == 200
              and data.get("reply") == "配置成功"
              and data.get("degraded") is False,
              json.dumps(data, ensure_ascii=False))
        check("响应标记实际服务商", data.get("provider") == "本地 Mock",
              json.dumps(data, ensure_ascii=False))

        stream_calls = [x for x in MockModelHandler.captured
                        if x["payload"].get("stream")]
        check("实际调用使用保存的 Key / 模型 / 端点",
              bool(stream_calls)
              and stream_calls[-1]["authorization"] == "Bearer test-key"
              and stream_calls[-1]["payload"].get("model") == "mock-model"
              and stream_calls[-1]["path"] == "/v1/chat/completions",
              json.dumps(stream_calls[-1] if stream_calls else {}, ensure_ascii=False)[:300])
    finally:
        settings.PATH = old_path
        server.ENHANCED_DIR = old_enhanced_dir
        if old_key is not None:
            os.environ["OPENAI_API_KEY"] = old_key
        if old_base is not None:
            os.environ["OPENAI_BASE_URL"] = old_base
        if old_model is not None:
            os.environ["MODEL"] = old_model
        td.cleanup()
        httpd.shutdown()
        httpd.server_close()

    print("\n" + "=" * 44)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
