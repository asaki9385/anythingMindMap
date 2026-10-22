#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资料标题生成 / 展示 / 编辑回归测试。"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import document_title as dt
import server

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


def test_clean_and_fallback():
    print("[1] 标题清洗与兜底")
    check(dt.clean_source_title("01_02.333大纲知识清单中教第三-五章.pdf")
          == "333大纲知识清单中教第三-五章", "去掉上传序号与零填充前缀")
    check(dt.clean_source_title("Part_1_tree_enhanced.json") == "", "老分片编号被清空")
    check(dt.clean_source_title("Part_1.pdf") == "", "Part 编号被清空")
    check(dt.clean_source_title("task_44694e2e") == "", "任务 ID 不当作标题")
    check(dt.clean_source_title("学习科学导论.docx") == "学习科学导论", "保留真实文件名")

    tree = {"title": "Knowledge Tree", "children": [
        {"title": "书", "children": [{"title": "第一章 教育概述"}, {"title": "第二章 教育与社会"}]}
    ]}
    check(dt.fallback_document_title(["01_02.333大纲.pdf"], tree)
          == "333大纲", "单文件兜底用清洗后的文件名")
    check(dt.fallback_document_title(["Part_1.pdf"], tree)
          == "教育概述（第一章–第二章）", "文件名无意义时用章节范围标题")
    check(dt.fallback_document_title(["a.pdf", "b.pdf"], tree)
          == "a 等 2 份资料", "多文件兜底标明份数")


def test_parse_and_apply():
    print("[2] LLM 响应解析与标题落树")
    check(dt.parse_title_response("《教育心理学》", "兜底") == "教育心理学", "去书名号")
    check(dt.parse_title_response("标题：教育心理学。", "兜底") == "教育心理学", "去标题前缀和句号")
    check(dt.parse_title_response("```text\n教育心理学\n```", "兜底") == "教育心理学", "去代码围栏")
    check(dt.parse_title_response('{"title": "教育心理学"}', "兜底") == "教育心理学", "兼容 JSON")
    check(dt.parse_title_response("抱歉，我无法生成", "兜底") == "兜底", "拒绝失败话术")
    check(dt.parse_title_response("", "兜底") == "兜底", "空响应回退")

    tree = {"title": "Knowledge Tree", "children": [
        {"title": "01_原始文件.pdf", "children": [{"title": "第一章"}]}
    ]}
    dt.apply_document_title(tree, "教育心理学")
    check(tree["title"] == "教育心理学", "根节点标题更新")
    check(tree["children"][0]["title"] == "教育心理学", "单册包装节点同步")

    multi = {"title": "Knowledge Tree", "children": [
        {"title": "A.pdf", "children": [{"title": "第一章"}]},
        {"title": "B.pdf", "children": [{"title": "第二章"}]},
    ]}
    dt.apply_document_title(multi, "两本书合集")
    check(multi["children"][0]["title"] == "A.pdf", "多文件不覆盖单个来源名")


def test_api_rename():
    print("[3] /api/rename 写回 task_state + tree + cache")
    old_temp, old_cache, old_base = server.TEMP_DIR, server.CACHE_DIR, server.BASE_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            server.TEMP_DIR = str(root / "tasks")
            server.CACHE_DIR = str(root / "cache")
            server.BASE_DIR = str(root)
            task_dir = root / "tasks" / "t1"
            (task_dir / "json").mkdir(parents=True)
            (task_dir / "uploads").mkdir()
            (task_dir / "uploads" / "01_原始文件.pdf").write_bytes(b"x")
            tree = {"title": "01_原始文件", "children": [
                {"title": "01_原始文件", "children": [{"title": "第一章", "children": []}]}
            ]}
            (task_dir / "json" / "knowledge_tree.json").write_text(
                json.dumps(tree, ensure_ascii=False), encoding="utf-8")
            state = {"task_id": "t1", "filename": "原始文件.pdf", "title": "原始文件",
                     "status": "done", "cache_key": "ck1", "updated_at": 0}
            (task_dir / "task_state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8")
            cache_dir = root / "cache" / "ck1"
            (cache_dir / "json").mkdir(parents=True)
            (cache_dir / "json" / "knowledge_tree.json").write_text(
                json.dumps(tree, ensure_ascii=False), encoding="utf-8")
            (cache_dir / "meta.json").write_text(
                json.dumps({"title": "原始文件"}, ensure_ascii=False), encoding="utf-8")

            res = asyncio.run(server.api_rename({"task_id": "t1", "title": "教育心理学"}))
            check(res.get("status") == "ok" and res.get("title") == "教育心理学", "接口返回成功", res)
            saved = json.loads((task_dir / "json" / "knowledge_tree.json").read_text(encoding="utf-8"))
            check(saved["title"] == "教育心理学", "任务树根标题已更新")
            check(saved["children"][0]["title"] == "教育心理学", "单册包装标题已更新")
            state2 = json.loads((task_dir / "task_state.json").read_text(encoding="utf-8"))
            check(state2["title"] == "教育心理学", "task_state 已更新")
            cached = json.loads((cache_dir / "json" / "knowledge_tree.json").read_text(encoding="utf-8"))
            check(cached["title"] == "教育心理学", "缓存树已更新")
            meta = json.loads((cache_dir / "meta.json").read_text(encoding="utf-8"))
            check(meta["title"] == "教育心理学", "缓存 meta 已更新")

            files = asyncio.run(server.api_files())["files"]
            title = next((f.get("title") for f in files if f.get("task_id") == "t1"), "")
            check(title == "教育心理学", "/api/files 返回新标题", files)
            tasks_res = asyncio.run(server.api_tasks())["tasks"]
            title = next((t.get("title") for t in tasks_res if t.get("task_id") == "t1"), "")
            check(title == "教育心理学", "/api/tasks 返回新标题", tasks_res)

            # 旧本地树
            legacy_dir = root / "data" / "tree_parts_enhanced_fixed"
            legacy_dir.mkdir(parents=True)
            legacy = {"title": "Part_1_tree_enhanced", "children": [{"title": "旧书"}]}
            (legacy_dir / "Part_1_tree_enhanced.json").write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            res = asyncio.run(server.api_rename({"filename": "Part_1_tree_enhanced.json", "title": "旧树新名"}))
            check(res.get("status") == "ok", "旧树重命名成功", res)
            saved = json.loads((legacy_dir / "Part_1_tree_enhanced.json").read_text(encoding="utf-8"))
            check(saved["title"] == "旧树新名", "旧树根标题已更新")
    finally:
        server.TEMP_DIR, server.CACHE_DIR, server.BASE_DIR = old_temp, old_cache, old_base


def test_ai_title():
    print("[4] 有模型时解析 AI 标题并计 token")
    from parse_report import UsageMeter

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "《教育心理学》"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            }

    class ChapterResp(FakeResp):
        def json(self):
            return {"choices": [{"message": {"content": "第一章 教育心理学概述"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResp()

    tree = {"title": "Knowledge Tree", "children": [
        {"title": "书", "children": [{"title": "第一章 教育心理学概述"}]}
    ]}
    real_client = dt.httpx.AsyncClient
    dt.httpx.AsyncClient = FakeClient
    try:
        usage = UsageMeter()
        title = asyncio.run(dt.generate_document_title(
            tree, ["01_教育心理学.pdf"],
            {"api_key": "sk-test", "base_url": "http://test/v1", "model": "m"}, usage=usage))
        check(title == "教育心理学", "AI 标题被采用", title)
        check(usage.calls == 1 and usage.prompt_tokens == 12, "标题调用计入 token", usage.to_dict())

        # 多章资料只返回第一章标题 → 退回章节范围标题
        class ChapterClient(FakeClient):
            async def post(self, *args, **kwargs):
                return ChapterResp()

        dt.httpx.AsyncClient = ChapterClient
        multi_tree = {"title": "Knowledge Tree", "children": [
            {"title": "01.pdf", "children": [
                {"title": "第一章 教育心理学概述"},
                {"title": "第二章 学习理论"},
            ]}
        ]}
        title = asyncio.run(dt.generate_document_title(
            multi_tree, ["01.pdf"],
            {"api_key": "sk-test", "base_url": "http://test/v1", "model": "m"}))
        check(title == "教育心理学概述（第一章–第二章）", "拒绝把单章标题当资料标题", title)
    finally:
        dt.httpx.AsyncClient = real_client


def test_no_ai_fallback():
    print("[5] 无模型时直接使用兜底标题")
    tree = {"title": "Knowledge Tree", "children": [
        {"title": "书", "children": [{"title": "第一章 教育"}]}
    ]}
    title = asyncio.run(dt.generate_document_title(tree, ["01_课程讲义.pdf"], None))
    check(title == "课程讲义", "无模型返回确定性标题", title)


if __name__ == "__main__":
    print("=" * 64)
    print("资料标题回归")
    print("=" * 64)
    test_clean_and_fallback()
    test_parse_and_apply()
    test_api_rename()
    test_ai_title()
    test_no_ai_fallback()
    print("=" * 64)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
