#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""六个保守修复的针对性回归测试。"""
import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import server
import skeleton_builder as sb
import tree_builder as tb
import node_enhancer as ne
import content_anchor as ca

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f" | {detail}" if detail else ""))
    return cond


def test_skeleton_long_fragment():
    print("[1] 长片段骨架不再静默丢尾部")
    paras = [f"段落{i}" for i in range(130)]
    text = "\n\n".join(paras)
    skeleton = {"title": "root", "children": [
        {"title": "a", "src_range": [0, 120], "children": []}
    ]}
    root, rep = sb.fragment_to_tree(text, skeleton, "测试")
    chk = ca.assert_no_content_loss(root, paras)
    check(rep.get("lost") == 0, "报告 lost=0", rep)
    check(not chk["lost"], "原文零丢失", chk)
    check(any(c.get("title") == "（后续内容）" for c in root.get("children", [])),
          "尾部接入了续节点")
    check(rep.get("route") == "skeleton_continuation", "路由标记为续节点", rep.get("route"))


def test_markdown_preservation():
    print("[2] Markdown 解析保留导言/图片/代码/HTML/前言")
    md = """开头导语。

![](images/a.jpg)

```python
# 代码里的注释不是标题
print(1)
```

<div>普通 HTML</div>

# 第一章 教育
正文。

# 前言
前言正文。

# 333
"""
    nodes = tb.parse_md_to_nodes(md)
    by_title = {n["title"]: n for n in nodes}
    check("文档导言" in by_title, "首个标题前的导言保留")
    check("![](images/a.jpg)" in by_title["文档导言"]["content"], "图片引用保留")
    check("# 代码里的注释不是标题" in by_title["文档导言"]["content"], "代码块内容保留")
    check("<div>普通 HTML</div>" in by_title["文档导言"]["content"], "普通 HTML 保留")
    check("前言" in by_title and "前言正文" in by_title["前言"]["content"],
          "内容型“前言”标题及其正文保留")
    check("333" not in by_title, "空噪音标题仍被丢弃")


def test_guide_projection():
    print("[3] 导学使用实际模块投影")
    tree = {"title": "root", "children": [
        {"title": "一本书", "children": [
            {"title": "第一章", "children": []},
            {"title": "第二章", "children": []},
        ]}
    ]}
    mods = ne.guide_modules(tree)
    check([m["title"] for m in mods] == ["第一章", "第二章"],
          "单册包装被解包为章节", [m.get("title") for m in mods])

    # 证明 generate_guides 真的使用了同一个投影，而不是测试只测了 helper。
    recorded = []

    async def fake_module(client, node, titles, idx, api_key, semaphore,
                          base_url=None, model=None, usage=None):
        recorded.append(node.get("title"))

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *args, **kwargs):
            raise RuntimeError("offline test")

    old_client = ne.httpx.AsyncClient
    old_gen = ne.generate_module_guide
    try:
        ne.httpx.AsyncClient = lambda *a, **k: FakeClient()
        ne.generate_module_guide = fake_module
        asyncio.run(ne.generate_guides(tree, "", concurrency=1,
                                       base_url="http://offline", model="test"))
    finally:
        ne.httpx.AsyncClient = old_client
        ne.generate_module_guide = old_gen
    check(recorded == ["第一章", "第二章"],
          "generate_guides 按章节生成导学", recorded)


def test_update_node_task_aware():
    print("[4] 节点编辑支持 task_<id>")
    old_temp = server.TEMP_DIR
    old_tasks = dict(server.tasks)
    try:
        with tempfile.TemporaryDirectory() as td:
            task_id = "abc12345"
            jd = Path(td) / task_id / "json"
            jd.mkdir(parents=True)
            tree = {"title": "root", "children": [
                {"title": "ch1", "content": "old", "children": []}
            ]}
            (jd / "knowledge_tree.json").write_text(
                json.dumps(tree, ensure_ascii=False), encoding="utf-8")
            server.TEMP_DIR = td

            class Live:
                status = "done"
                result = None
                cache_key = ""
            live = Live()
            server.tasks.clear()
            server.tasks[task_id] = live
            resp = asyncio.run(server.api_update_node({
                "filename": f"task_{task_id}", "path": [0],
                "title": "ch1", "content": "new",
            }))
            saved = json.loads((jd / "knowledge_tree.json").read_text(encoding="utf-8"))
            check(resp.get("status") == "ok", "接口成功", resp)
            check(saved["children"][0]["content"] == "new", "磁盘已更新")
            check(live.result["children"][0]["content"] == "new", "内存结果已同步")
    finally:
        server.TEMP_DIR = old_temp
        server.tasks.clear()
        server.tasks.update(old_tasks)


def test_task_state_and_cache():
    print("[5] 任务状态持久化 + 内容缓存")
    old_temp, old_cache = server.TEMP_DIR, server.CACHE_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            server.TEMP_DIR = str(root / "tasks")
            server.CACHE_DIR = str(root / "cache")
            os.makedirs(server.TEMP_DIR)
            os.makedirs(server.CACHE_DIR)

            task = server.TaskProgress("state123", "x.pdf")
            task.set_stage("building_tree", 45, "构建中")
            state = server.load_task_state("state123")
            check(state and state["stage"] == "building_tree", "状态已落盘", state)
            state_path = root / "tasks" / "state123" / "task_state.json"
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            raw["status"] = "processing"
            state_path.write_text(json.dumps(raw), encoding="utf-8")
            recovered = server.load_task_state("state123", recover_interrupted=True)
            check(recovered["status"] == "error", "重启遗留 processing 被标为中断")
            check("服务重启" in recovered["error"], "中断原因可读")

            src = root / "a.pdf"
            src.write_bytes(b"abc123")
            files = [{"path": str(src), "original_name": "a.pdf",
                      "sha256": server._file_sha256(str(src))}]
            key = server._build_cache_key(files, None, None)
            check(key == server._build_cache_key(files, None, None), "缓存键稳定")
            wd = root / "tasks" / "src"
            (wd / "json").mkdir(parents=True)
            (wd / "markdown").mkdir()
            (wd / "trees").mkdir()
            tree = {"title": "Knowledge Tree", "children": [
                {"title": "书", "children": [
                    {"title": "章", "content": "正文\n![](images/x.jpg)", "children": []}
                ]}
            ]}
            (wd / "json" / "knowledge_tree.json").write_text(
                json.dumps(tree, ensure_ascii=False), encoding="utf-8")
            (wd / "report.json").write_text(
                json.dumps({"task_id": "src", "nodes": {"total": 3}}), encoding="utf-8")
            server._store_cache(key, str(wd))
            check((root / "cache" / key / "json" / "knowledge_tree.json").is_file(),
                  "缓存已落盘")
            hit = server.TaskProgress("hit123", "a.pdf")
            hit.cache_key = key
            ok = server._restore_cache(hit, files, 0)
            check(ok and hit.cache_hit and hit.status == "done", "缓存命中并完成任务")
            check((root / "tasks" / "hit123" / "json" / "knowledge_tree.json").is_file(),
                  "缓存结果复制到任务目录")
            restored = json.loads((root / "tasks" / "hit123" / "json" / "knowledge_tree.json").read_text(encoding="utf-8"))
            chapter = restored["children"][0]["children"][0]
            check("images/" not in chapter.get("content", "")
                  and chapter.get("images") == ["images/x.jpg"],
                  "缓存命中时清理正文图片路径", chapter)
            edited = json.loads(json.dumps(tree))
            edited["children"][0]["title"] = "改过的书"
            server._update_cached_tree(key, edited)
            cached_tree = json.loads((root / "cache" / key / "json" / "knowledge_tree.json").read_text(encoding="utf-8"))
            check(cached_tree["children"][0]["title"] == "改过的书", "编辑后缓存树同步")
    finally:
        server.TEMP_DIR, server.CACHE_DIR = old_temp, old_cache


def test_security_defaults():
    print("[6] 安全默认值")
    import app_settings as S
    check(not S.BUILTIN_CHAT.get("api_key"), "仓库不内置模型 Key")
    check(not S.BUILTIN_MINERU_TOKEN, "仓库不内置 MinerU Token")
    view = S.public_view()
    dumped = json.dumps(view, ensure_ascii=False)
    check("设置接口不回显仓库凭证",
          not S.BUILTIN_CHAT.get("api_key") and not S.BUILTIN_MINERU_TOKEN
          and '"api_key": "' not in dumped)
    old_pw = server.AUTH_PASSWORD
    try:
        server.AUTH_PASSWORD = "secret"
        from fastapi.testclient import TestClient
        client = TestClient(server.app)
        r = client.get("/api/health")
        check(r.status_code == 200, "健康检查不受认证影响", r.status_code)
        r = client.get("/api/health", headers={"Origin": "http://evil.example"})
        check(r.headers.get("access-control-allow-origin") is None,
              "默认不返回通配 CORS 头", r.headers.get("access-control-allow-origin"))
        r = client.get("/api/settings")
        check(r.status_code == 401, "未带认证被拒绝", r.status_code)
        token = base64.b64encode(b"admin:secret").decode("ascii")
        r = client.get("/api/settings", headers={"Authorization": f"Basic {token}"})
        check(r.status_code == 200, "正确 Basic Auth 可通过", r.status_code)
    finally:
        server.AUTH_PASSWORD = old_pw


def main():
    print("=" * 64)
    print("六个保守修复回归")
    print("=" * 64)
    test_skeleton_long_fragment()
    test_markdown_preservation()
    test_guide_projection()
    test_update_node_task_aware()
    test_task_state_and_cache()
    test_security_defaults()
    print("=" * 64)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
