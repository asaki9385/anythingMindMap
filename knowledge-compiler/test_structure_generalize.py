#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构评分 / AI 骨架 / 原文锚定 的测试（离线可跑，不依赖真实 API）。

覆盖：
  1. 结构质量分级：真实教材判 regex，不良样本判 skeleton
  2. 段落模型可逆性
  3. 骨架响应严格校验（不合法一律拒绝）
  4. 原文锚定：合法区间 / 父带引言 / 结构壳 / 越界 / 空洞 —— **任何情况零丢字**
  5. 无骨架保底：按段落分块，同样零丢字
  6. 端到端：假 LLM 生成骨架 → 锚定 → 原文完整
  7. 无标题正文不再静默丢弃（regex 路径保留导言节点）

运行：python knowledge-compiler/test_structure_generalize.py
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import content_anchor as ca          # noqa: E402
import skeleton_builder as sb        # noqa: E402
import structure_score as ss         # noqa: E402

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


PARA_TEXT = ("教育学是研究教育现象的一门科学。\n\n广义的教育指一切有目的的活动。\n\n"
             "狭义的教育指学校教育。\n\n教育具有历史性。\n\n教育具有社会性。")
PARAS = ca.split_paragraphs(PARA_TEXT)


# ────────────────────────────────────────────
# 1. 结构质量分级
# ────────────────────────────────────────────

def test_scoring():
    print("\n1. 结构质量分级（阈值 %.2f）" % ss.THRESHOLD_SKELETON)
    cases = [
        ("散文无标题", "\n\n".join(PARA_TEXT.split("\n\n") * 4), "skeleton"),
        ("扁平编号清单", "\n".join(f"# {i}. 要点{i}：教育学基础" for i in range(1, 12)), "skeleton"),
        ("单标题巨文本", "# 导论\n\n" + PARA_TEXT * 3, "skeleton"),
        ("过度抬升(全标题)", "\n".join(f"# (i) 句子{i}。" for i in range(1, 40)), "skeleton"),
        ("编号大纲(真结构)", ("# 第一章 教育概述\n# 一、教育的概念\n# （一）定义\n"
                          "# 1. 广义\n# （1）社会教育\n# 一、教育的功能\n" + PARA_TEXT), "regex"),
    ]
    for name, text, want in cases:
        r = ss.score_fragment(text)
        check(r["route"] == want, f"{name} → {want}（实得 {r['route']}，score={r['score']}）",
              r["reasons"])


# ────────────────────────────────────────────
# 2. 段落模型
# ────────────────────────────────────────────

def test_paragraphs():
    print("\n2. 段落模型可逆")
    check(len(PARAS) == 5, f"切出 5 段（实得 {len(PARAS)}）")
    check(ca.paragraphs_text(PARAS) == "\n\n".join(p.strip() for p in PARA_TEXT.strip().split("\n\n")),
          "join 回原文一致")
    check(ca.split_paragraphs("") == [], "空文本 → 空列表")
    check(ca.split_paragraphs("  \n\n  \n ") == [], "纯空白 → 空列表")


# ────────────────────────────────────────────
# 3. 骨架响应校验
# ────────────────────────────────────────────

def test_skeleton_parsing():
    print("\n3. 骨架响应严格校验")
    good = [
        '{"title":"教育概述","children":[{"title":"定义","src_range":[0,2],"children":[]},'
        '{"title":"特性","src_range":[2,5],"children":[{"title":"历史","src_range":[3,4],"children":[]}]}]}',
        '```json\n{"title":"A","children":[{"title":"B","src_range":[0,1],"children":[]}]}\n```',
        '好的，结果：{"title":"A","children":[{"title":"B","src_range":[0,1],"children":[]}]}',
        '{"title":"A","children":[{"name":"name键兼容","src_range":[0,1],"children":[]}]}',
    ]
    for raw in good:
        r = sb.parse_skeleton_response(raw, n_paras=5)
        check(r is not None, f"接受合法骨架 {raw[:46]!r}")

    bad = [
        ("无 children", '{"title":"A"}'),
        ("children 非数组", '{"title":"A","children":"x"}'),
        ("区间倒置", '{"title":"A","children":[{"title":"B","src_range":[3,1],"children":[]}]}'),
        ("区间越界", '{"title":"A","children":[{"title":"B","src_range":[0,999],"children":[]}]}'),
        ("非整数区间", '{"title":"A","children":[{"title":"B","src_range":["a","b"],"children":[]}]}'),
        ("非 dict 子节点", '{"title":"A","children":[1,2]}'),
        ("空串", ''),
        ("乱码", '无法处理'),
        ("空 children 唯一节点", '{"title":"A","children":[]}'),
    ]
    for name, raw in bad:
        r = sb.parse_skeleton_response(raw, n_paras=5)
        check(r is None, f"拒绝{name}", str(r)[:40])

    deep = {"title": "A", "children": [{"title": "x", "children": []}]}
    for _ in range(8):
        deep = {"title": "d", "children": [deep]}
    check(sb.parse_skeleton_response(json.dumps(deep), n_paras=5) is None, "拒绝深度超限")


# ────────────────────────────────────────────
# 4. 原文锚定（零丢字是硬性不变式）
# ────────────────────────────────────────────

def test_anchoring():
    print("\n4. 原文锚定：各种骨架形态下零丢字")
    scenarios = [
        ("父带引言+子正文", {"title": "教育概述", "children": [
            {"title": "定义", "src_range": [0, 2], "children": []},
            {"title": "特性", "src_range": [2, 3], "children": [
                {"title": "历史性", "src_range": [3, 4], "children": []},
                {"title": "社会性", "src_range": [4, 5], "children": []}]}]}, "range"),
        ("父声明整段(结构壳)", {"title": "T", "children": [
            {"title": "A", "src_range": [0, 5], "children": [
                {"title": "A1", "src_range": [0, 2], "children": []},
                {"title": "A2", "src_range": [2, 5], "children": []}]}]}, "range"),
        ("区间越界", {"title": "T", "children": [
            {"title": "A", "src_range": [0, 99], "children": []}]}, "positional"),
        ("无区间声明", {"title": "T", "children": [
            {"title": "A", "children": []}, {"title": "B", "children": []}]}, "positional"),
        ("空洞过多", {"title": "T", "children": [
            {"title": "A", "src_range": [0, 1], "children": []},
            {"title": "B", "src_range": [4, 5], "children": []}]}, "positional"),
    ]
    for name, sk, want_by in scenarios:
        root, rep = ca.anchor_skeleton(sk, PARAS)
        chk = ca.assert_no_content_loss(root, PARAS)
        check(rep["lost_paragraphs"] == 0 and chk["kept"] == chk["total"],
              f"{name}：零丢字（kept {chk['kept']}/{chk['total']}）")
        check(rep["anchored_by"] == want_by, f"{name}：锚定方式 {want_by}",
              rep["anchored_by"])

    # 父带引言：引言应归父节点
    root, _ = ca.anchor_skeleton({"title": "教育概述", "children": [
        {"title": "定义", "src_range": [0, 2], "children": []},
        {"title": "特性", "src_range": [2, 3], "children": [
            {"title": "历史性", "src_range": [3, 4], "children": []},
            {"title": "社会性", "src_range": [4, 5], "children": []}]}]}, PARAS)
    feats = [n for n in ca.iter_nodes(root) if n["title"] == "特性"][0]
    check(feats["content"].strip() == "狭义的教育指学校教育。",
          "父节点自带的引言段归父节点", repr(feats["content"]))
    # 结构壳父节点不得重复承载正文
    root2, _ = ca.anchor_skeleton({"title": "T", "children": [
        {"title": "A", "src_range": [0, 5], "children": [
            {"title": "A1", "src_range": [0, 2], "children": []},
            {"title": "A2", "src_range": [2, 5], "children": []}]}]}, PARAS)
    a = [n for n in ca.iter_nodes(root2) if n["title"] == "A"][0]
    check(a["content"] == "", "结构壳父节点不重复承载正文", repr(a["content"]))


# ────────────────────────────────────────────
# 5. 无骨架保底
# ────────────────────────────────────────────

def test_fallback():
    print("\n5. 无骨架保底（按段落分块）")
    root = ca.paragraphs_to_tree(PARAS, "无结构文本")
    chk = ca.assert_no_content_loss(root, PARAS)
    check(chk["kept"] == chk["total"], f"保底零丢字（{chk['kept']}/{chk['total']}）")
    check(root["title"] == "无结构文本", "根标题保留")
    check(len(root["children"]) >= 1, "至少分出一块")
    check(ca.paragraphs_to_tree([], "空")["children"] == [], "空段落安全")


# ────────────────────────────────────────────
# 6. 端到端：假 LLM
# ────────────────────────────────────────────

class _SkeletonLLM(BaseHTTPRequestHandler):
    """返回一个合法骨架，区间正确覆盖全部段落。"""

    seen = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        prompt = json.loads(self.rfile.read(n).decode("utf-8"))["messages"][0]["content"]
        _SkeletonLLM.seen.append(prompt)
        # 从提示词里数出段落数
        m = re.search(r'切成的 (\d+) 个段落', prompt)
        total = int(m.group(1)) if m else 5
        mid = max(1, total // 2)
        payload = {"title": "AI 骨架", "children": [
            {"title": "前半", "src_range": [0, mid], "children": []},
            {"title": "后半", "src_range": [mid, total], "children": []},
        ]}
        out = json.dumps({
            "choices": [{"message": {"role": "assistant",
                                    "content": json.dumps(payload, ensure_ascii=False)}}],
        }, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _GarbageLLM(BaseHTTPRequestHandler):
    """返回乱码，用来验证降级路径同样零丢字。"""

    def do_POST(self):
        out = json.dumps({"choices": [{"message": {"role": "assistant",
                                                  "content": "这不是JSON"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_end_to_end():
    print("\n6. 端到端（假 LLM）")
    import asyncio

    for cls, name in ((_SkeletonLLM, "正常骨架"), (_GarbageLLM, "乱码响应")):
        srv = HTTPServer(("127.0.0.1", 0), cls)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        async def go():
            import httpx
            async with httpx.AsyncClient() as client:
                return await sb.generate_skeleton(
                    client, PARAS, f"http://127.0.0.1:{port}/chat/completions",
                    "k", "m")

        sk = asyncio.run(go())
        srv.shutdown()
        root, rep = sb.fragment_to_tree(PARA_TEXT, sk, "片段")
        chk = ca.assert_no_content_loss(root, PARAS)
        check(chk["kept"] == chk["total"],
              f"{name}：端到端零丢字（kept {chk['kept']}/{chk['total']}）")
        check(rep["route"] in ("skeleton", "prose_fallback"),
              f"{name}：路由 {rep['route']}")
        if name == "正常骨架":
            check(sk is not None, "正常骨架被接受")
            titles = [n["title"] for n in root["children"]]
            check(titles == ["前半", "后半"], "骨架标题被采用", titles)
        else:
            check(sk is None, "乱码响应被拒绝")
            check(rep["route"] == "prose_fallback", "降级到段落分块保底", rep["route"])


# ────────────────────────────────────────────
# 7. 无标题全文丢弃
# ────────────────────────────────────────────

def test_no_silent_loss():
    print("\n7. 「无标题全文丢弃」已堵死")
    import tree_builder as tb

    prose_only = "\n\n".join(PARA_TEXT.split("\n\n") * 3)
    regex_nodes = tb.parse_md_to_nodes(prose_only)
    check(len(regex_nodes) == 1 and regex_nodes[0]["title"] == "文档导言",
          "regex 路径也会保留无标题正文（文档导言节点）", str(regex_nodes)[:120])
    check(PARA_TEXT.split("\n\n")[0][:20] in regex_nodes[0].get("content", ""),
          "regex 路径导言节点含原文")
    root, rep = sb.fragment_to_tree(prose_only, None, "散文")
    chk = ca.assert_no_content_loss(root, ca.split_paragraphs(prose_only))
    check(chk["kept"] == chk["total"],
          f"但骨架/保底路径零丢字（{chk['kept']}/{chk['total']}）")
    check(rep["route"] == "prose_fallback", "路由为降级保底", rep["route"])


def main():
    print("=" * 72)
    print("结构泛用性测试（评分 / 骨架 / 锚定）")
    print("=" * 72)
    test_scoring()
    test_paragraphs()
    test_skeleton_parsing()
    test_anchoring()
    test_fallback()
    test_end_to_end()
    test_no_silent_loss()
    print("\n" + "=" * 72)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
