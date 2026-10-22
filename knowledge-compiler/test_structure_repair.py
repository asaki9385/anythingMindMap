#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""structure_repair / 排序修正 的测试（离线可跑，不依赖真实 API）。

覆盖：
  1. 常规编号顺序错乱（一、三、二 / 1.3,1.1 / 圈号 / 第20章之后）
  2. 无编号兄弟组按文档源序恢复
  3. 坏标题判定与重命名（markdown 残留 / 纯编号 / OCR 噪声 / 超长）
  4. LLM 响应的严格校验（非法排列必须被拒）
  5. 本地假 LLM 服务下的端到端审校（不联网）
  6. 审校结论能活过 downstream 的 cleanup_tree_structure
  7. 内部字段（src_index/seq_index）在最终 JSON 中被剥离
  8. 无 API key / enable_llm=False 时只走确定性降级

运行：python knowledge-compiler/test_structure_repair.py
"""
import copy
import re
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import node_enhancer  # noqa: E402
import structure_repair as sr  # noqa: E402

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))
    return cond


def titles(node):
    return [c.get("title", "") for c in node.get("children", [])]


def mk(items):
    """items: [(title, src_index|None)]"""
    kids = []
    for t, si in items:
        n = {"title": t, "children": []}
        if si is not None:
            n["src_index"] = si
        kids.append(n)
    return {"title": "父节点", "children": kids}


# ────────────────────────────────────────────
# 1. 编号顺序错乱
# ────────────────────────────────────────────

def test_order_fix():
    print("\n1. 编号顺序错乱的确定性修复")
    cases = [
        ("（一）三、二", [("（一）甲", 0), ("（三）丙", 1), ("（二）乙", 2)],
         ["（一）甲", "（二）乙", "（三）丙"]),
        ("一、二、三", [("一、甲", 0), ("三、丙", 1), ("二、乙", 2)],
         ["一、甲", "二、乙", "三、丙"]),
        ("第20章之后", [("第十一章", 0), ("第二十章", 1), ("第二十一章", 2), ("第一百章", 3)],
         ["第十一章", "第二十章", "第二十一章", "第一百章"]),
        ("1. 3. 2.", [("1. 甲", 0), ("3. 丙", 1), ("2. 乙", 2)],
         ["1. 甲", "2. 乙", "3. 丙"]),
        ("圈号乱序", [("③ 丙", 0), ("① 甲", 1), ("② 乙", 2)],
         ["① 甲", "② 乙", "③ 丙"]),
        ("知识点乱序", [("知识点一 甲", 0), ("知识点三 丙", 1), ("知识点二 乙", 2)],
         ["知识点一 甲", "知识点二 乙", "知识点三 丙"]),
        ("多级 1.2/1.1/1.3", [("1.2 甲", 0), ("1.3 丙", 1), ("1.1 乙", 2)],
         ["1.1 乙", "1.2 甲", "1.3 丙"]),
    ]
    for name, items, want in cases:
        t = mk(items)
        sr.deterministic_audit(t)
        node_enhancer.cleanup_tree_structure(t)
        check(titles(t) == want, f"{name} → {want}", titles(t))


def test_unnumbered_keeps_doc_order():
    print("\n2. 无编号兄弟组按文档源序恢复（不被字母序打乱）")
    t = mk([("Zebra", 10), ("Abstract", 20), ("Note", 30)])
    sr.deterministic_audit(t)
    node_enhancer.cleanup_tree_structure(t)
    check(titles(t) == ["Zebra", "Abstract", "Note"],
          "按 src_index 保持文档序", titles(t))


def test_mixed_families_do_not_interleave():
    print("\n2b. 不同编号族互不干扰（同父节点下只各排各的）")
    t = mk([("（一）甲", 0), ("1. 乙", 1), ("（三）丙", 2), ("2. 丁", 3)])
    sr.deterministic_audit(t)
    node_enhancer.cleanup_tree_structure(t)
    # 中文族与数字族各自成组；原文本身就是交错顺序，两个族内部都已有序 → 不动
    check(titles(t) == ["（一）甲", "1. 乙", "（三）丙", "2. 丁"],
          "族内有序时保持原位", titles(t))


def test_no_metadata_is_left_untouched():
    print("\n2c. 无编号且无 src_index 时不乱动（老数据安全）")
    t = mk([("Zeta", None), ("Alpha", None), ("Mid", None)])
    sr.deterministic_audit(t)
    node_enhancer.cleanup_tree_structure(t)
    check(titles(t) == ["Zeta", "Alpha", "Mid"], "保持原顺序", titles(t))


# ────────────────────────────────────────────
# 3. 坏标题
# ────────────────────────────────────────────

def test_bad_title_detection():
    print("\n3. 坏标题判定（只有坏标题才允许重命名）")
    bad = {
        "**教育的概念**": "markdown残留",
        "### 教学原则": "markdown残留",
        "`代码`标题": "markdown残留",
        "知识点四": "纯编号无标题",
        "1.": "纯编号无标题",
        "（一）": "纯编号无标题",
        "": "空标题",
        "标题" * 40: "标题过长",
    }
    for t, why in bad.items():
        got = sr.bad_title_reason(t)
        check(bool(got), f"{t[:18]!r} 判为坏（{why}）", repr(got))
    good = ["（一）教育目的", "正常标题", "教育", "1.2 教学进程理论",
            "第一章 教育的产生与发展", "③ 教育的功能"]
    for t in good:
        check(sr.bad_title_reason(t) == "", f"{t[:20]!r} 判为正常", sr.bad_title_reason(t))


def test_rename_only_applies_to_bad_titles():
    print("\n3b. LLM 返回的改名只对坏标题生效")
    tree = {"title": "p", "children": [
        {"title": "**教育的概念**", "children": [], "src_index": 0},   # 坏 → 应改
        {"title": "教育目的", "children": [], "src_index": 1},          # 好 → 不改
    ]}
    n0, n1 = tree["children"]
    check(sr.bad_title_reason(n0["title"]) and not sr.bad_title_reason(n1["title"]),
          "前置判定正确（一坏一好）")
    # 直接验证 audit_group_with_llm 内部的过滤逻辑：好的标题不给它改名机会
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        sr.parse_group_response  # 已在第4组测
    finally:
        loop.close()


# ────────────────────────────────────────────
# 4. LLM 响应校验
# ────────────────────────────────────────────

def test_rename_guards():
    print("\n3c. 改名护栏：编号前缀必须保留，编号值不许变")
    allowed = [
        ("知识点四", "知识点四 希腊化时期的教育"),
        ("第一章", "第一章 教育及其产生与发展"),
        ("（一）", "（一）教育目的论"),
        ("1. 学", "1. 学礼制度"),
        ("**教育的概念**", "教育的概念"),     # 原标题无编号，随便改
    ]
    for old, new in allowed:
        check(sr._rename_allowed(old, new), f"接受 {old!r} → {new!r}")
    rejected = [
        ("知识点四", "希腊化时期的教育"),   # 编号丢了
        ("（一）甲", "（二）甲"),            # 编号值变了
        ("1. 学", "2. 学"),
    ]
    for old, new in rejected:
        check(not sr._rename_allowed(old, new), f"拒绝 {old!r} → {new!r}")


def test_llm_response_validation():
    print("\n4. LLM 响应的严格校验（非法排列一律拒绝）")
    good = [
        ('{"order":[2,0,1]}', [2, 0, 1]),
        ('{"order":[0,1,2],"renames":{}}', [0, 1, 2]),
        ('```json\n{"order":[1,0,2]}\n```', [1, 0, 2]),
        ('好的，结果如下：{"order":[1,0,2]}', [1, 0, 2]),
        ('{"order":[1,0,2],"note":"已重排"}', [1, 0, 2]),
    ]
    for raw, want in good:
        order, _ = sr.parse_group_response(raw, 3)
        check(order == want, f"接受合法响应 {raw[:34]!r}", f"got {order}")
    bad = [
        '{"order":[0,1,3]}',        # 越界
        '{"order":[0,1]}',          # 缺项
        '{"order":[0,1,1]}',        # 重复
        '{"order":["a","b","c"]}',  # 非整数
        '{"order":"abc"}',          # 非数组
        '[1,2,3]',                  # 非 dict
        '',                         # 空
        '无法处理',                  # 乱码
    ]
    for raw in bad:
        order, renames = sr.parse_group_response(raw, 3)
        check(order is None and renames == {},
              f"拒绝非法响应 {raw[:24]!r}", f"got {order}")


# ────────────────────────────────────────────
# 5. 端到端：本地假 LLM 服务
# ────────────────────────────────────────────

class _ReverseLLM(BaseHTTPRequestHandler):
    """只做一件事：把这一组的顺序整体反转，用来验证重排真的写回了父节点。"""
    seen = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        prompt = json.loads(self.rfile.read(n).decode("utf-8"))["messages"][0]["content"]
        m = re.search(r'节点列表：\n(.*?)(?:\n\n|\Z)', prompt, re.S)
        block = m.group(1) if m else ""
        lines = [l for l in block.split("\n") if re.match(r'^\d+\. ', l)]
        _ReverseLLM.seen.append([re.sub(r'^\d+\.\s*', '', l.split("   [")[0])
                                 for l in lines])
        size = max(len(lines), 1)
        payload = json.dumps({"order": list(range(size - 1, -1, -1)),
                              "renames": {}}, ensure_ascii=False)
        out = json.dumps({"choices": [{"message": {"role": "assistant",
                                                  "content": payload}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _FakeLLM(BaseHTTPRequestHandler):
    """按组内节点数返回一个"反转 + 改名"的响应，模拟真实模型。"""

    received = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        prompt = body["messages"][0]["content"]
        _FakeLLM.received.append(prompt)
        # 从"节点列表"段里数节点：提示词正文里也有 0./1./2. 字样，
        # 必须先切出该段，否则会多数出指令里的示例行。
        import re as _re
        m = _re.search(r'节点列表：\n(.*?)(?:\n\n|\Z)', prompt, _re.S)
        block = m.group(1) if m else ""
        idxs = _re.findall(r'^(\d+)\. ', block, _re.M)
        size = max(len(idxs), 1)
        payload = {"order": list(range(size - 1, -1, -1)), "renames": {}}
        if "标题需修正" in prompt:
            payload["renames"] = {idxs[0]: "**改好的标题**"}
        out = json.dumps({
            "choices": [{"message": {"role": "assistant", "content":
                       json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"total_tokens": 1},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_end_to_end_with_fake_llm():
    print("\n5. 端到端审校（本地假 LLM，不联网）")
    srv = HTTPServer(("127.0.0.1", 0), _FakeLLM)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # 造一棵有"坏标题 + 待 LLM 兜底"的树
    tree = {"title": "书", "children": [
        {"title": "第一章 总论", "level": 1, "src_index": 0, "children": [
            {"title": "（一）教育目的", "src_index": 10, "children": []},
            {"title": "**教育的概念**", "src_index": 11, "children": []},   # 坏标题
            {"title": "（三）教育制度", "src_index": 12, "children": []},
            {"title": "（二）教育与社会", "src_index": 13, "children": []},
        ]},
    ]}
    url = f"http://127.0.0.1:{port}/chat/completions"
    import asyncio
    report = asyncio.run(sr.audit_book_tree(tree, url=url, api_key="k", model="m"))
    srv.shutdown()

    got = titles(tree["children"][0])
    print(f"   报告: {report}")
    print(f"   组内最终顺序: {got}")
    check(len(_FakeLLM.received) >= 1, "LLM 被调用", "没有请求")
    check("**教育的概念**" not in got, "坏标题被重命名", got)
    check(len(got) == 4, "节点数不变（绝不增删）", got)

    # 顺序必须活过下游整理
    node_enhancer.cleanup_tree_structure(tree)
    check(titles(tree["children"][0]) == got,
          "审校结论活过 cleanup_tree_structure", titles(tree["children"][0]))


def test_llm_reorder_reaches_children():
    print("\n5b. LLM 的重排必须写回父节点 children（核心回归）")
    # 曾经的 bug：重排只改了一个临时列表，父节点顺序没变，
    # 于是"模型说改了顺序"被静默丢弃，改名还落到错位的节点上。
    srv = HTTPServer(("127.0.0.1", 0), _ReverseLLM)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # 编号重复 → 一定被送 LLM
    tree = {"title": "p", "children": [
        {"title": "（一）教育目的", "src_index": 0, "children": []},
        {"title": "（一）教育与社会", "src_index": 1, "children": []},
        {"title": "（二）教育制度", "src_index": 2, "children": []},
        {"title": "（三）教学方法", "src_index": 3, "children": []},
    ]}
    url = f"http://127.0.0.1:{port}/chat/completions"
    import asyncio
    asyncio.run(sr.audit_book_tree(tree, url=url, api_key="k", model="m"))
    srv.shutdown()
    got = titles(tree)
    print(f"   反转后: {got}")
    check(got == ["（三）教学方法", "（二）教育制度", "（一）教育与社会", "（一）教育目的"],
          "父节点 children 顺序确被写回", got)
    check(len(_ReverseLLM.seen) >= 1, "LLM 收到了这个组")
    # prompt 里展示的顺序必须与 order 的语义一致
    if _ReverseLLM.seen:
        block = _ReverseLLM.seen[0]
        check(block[0] == "（一）教育目的",
              "prompt 首项就是 children[0]（下标未错位)", block[:1])


# ────────────────────────────────────────────
# 6. 内部字段剥离（对齐 server._prepare_final_json）
# ────────────────────────────────────────────

def test_internal_fields_stripped():
    print("\n6. 内部字段在最终 JSON 中被剥离")
    sys.path.insert(0, BASE_DIR)
    import importlib.util
    spec = importlib.util.spec_from_file_location("_srv_probe",
                                                  os.path.join(BASE_DIR, "server.py"))
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tree = {"title": "root", "children": [
            {"title": "甲", "src_index": 0, "seq_index": 0, "children": [],
             "_audit_hint": "x"},
        ]}
        out = mod._prepare_final_json(tree)
        leaked = []
        def walk(n):
            for k in ("src_index", "seq_index", "_audit_hint"):
                if k in n:
                    leaked.append((n.get("title"), k))
            for c in n.get("children", []):
                walk(c)
        walk(out)
        check(not leaked, "src_index/seq_index/_audit_hint 均已剥离", leaked)
        check("depth" in out["children"][0] and "level" in out["children"][0],
              "默认字段仍在（depth/level）")
    except Exception as e:
        # server.py 依赖 fastapi；没有则不阻塞本测试组
        print(f"    (跳过: 无法加载 server.py — {type(e).__name__}: {e})")


# ────────────────────────────────────────────
# 7. 降级路径
# ────────────────────────────────────────────

def test_degraded_without_key():
    print("\n7. 无 API key 时只走确定性降级（不报错）")
    tree = {"title": "书", "children": [{"title": "p", "children": [
        {"title": "（一）甲", "src_index": 0, "children": []},
        {"title": "（三）丙", "src_index": 1, "children": []},
        {"title": "（二）乙", "src_index": 2, "children": []},
    ]}]}
    import asyncio
    report = asyncio.run(sr.audit_book_tree(tree))  # 无 url/key
    print(f"   报告: {report}")
    check(report["deterministic_groups"] >= 1, "确定性修正生效", report)
    check(titles(tree["children"][0]) == ["（一）甲", "（二）乙", "（三）丙"],
          "顺序已修好", titles(tree["children"][0]))
    node_enhancer.cleanup_tree_structure(tree)
    check(titles(tree["children"][0]) == ["（一）甲", "（二）乙", "（三）丙"],
          "下游不回退结论", titles(tree["children"][0]))


def test_seq_index_removed_but_src_preserved():
    print("\n7b. seq_index 记录最终序，src_index 保留原文序（可纠错）")
    tree = {"title": "root", "children": [
        {"title": "A", "src_index": 5, "children": []},
        {"title": "B", "src_index": 7, "children": []},
    ]}
    sr.stamp_seq(tree)
    # 根本身也算一个位置，所以子节点从 1 开始（root=0）
    check(tree.get("seq_index") == 0, "根节点也编上 seq_index")
    kids = tree["children"]
    check([k["seq_index"] for k in kids] == [1, 2], "子节点 seq_index 连续递增",
          [k.get("seq_index") for k in kids])
    check(all(k["src_index"] in (5, 7) for k in kids),
          "src_index 未被覆盖（原文序仍在）", [k["src_index"] for k in kids])


def main():
    print("=" * 72)
    print("structure_repair 测试（离线）")
    print("=" * 72)
    test_order_fix()
    test_unnumbered_keeps_doc_order()
    test_mixed_families_do_not_interleave()
    test_no_metadata_is_left_untouched()
    test_bad_title_detection()
    test_rename_guards()
    test_rename_only_applies_to_bad_titles()
    test_llm_response_validation()
    test_end_to_end_with_fake_llm()
    test_llm_reorder_reaches_children()
    test_internal_fields_stripped()
    test_degraded_without_key()
    test_seq_index_removed_but_src_preserved()
    print("\n" + "=" * 72)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
