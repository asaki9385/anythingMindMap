#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 骨架质量闭环测试：严格契约 / 标题接地 / 窗口拼接 / 确定性降级。

运行：python knowledge-compiler/test_skeleton_quality.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import skeleton_quality as SQ  # noqa: E402
from content_anchor import split_paragraphs, assert_no_content_loss  # noqa: E402
from skeleton_builder import fragment_to_tree  # noqa: E402

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


PARAS = [
    "第一章 教育", "第一章正文。",
    "第一节 概念", "第一节正文。",
    "一、定义", "定义正文。",
    "二、特点", "特点正文。",
    "第二节 发展", "发展正文。",
]


def test_validate_contract():
    print("\n1. 严格契约")
    good = {"title": "t", "children": [
        {"title": "a", "src_range": [0, 2], "children": []},
        {"title": "b", "src_range": [2, 5], "children": []},
    ]}
    v = SQ.validate_skeleton(good, 5)
    check(v["ok"], "全覆盖/不重叠 → ok", v)
    check(v["coverage"] == 1.0, "coverage=1.0", v["coverage"])

    gap = {"title": "t", "children": [
        {"title": "a", "src_range": [0, 2], "children": []},
        {"title": "b", "src_range": [3, 5], "children": []},
    ]}
    v = SQ.validate_skeleton(gap, 5)
    check(not v["ok"] and any(e["code"] == "coverage_gap" for e in v["errors"]),
          "空洞被拒", v["errors"])

    overlap = {"title": "t", "children": [
        {"title": "a", "src_range": [0, 3], "children": []},
        {"title": "b", "src_range": [2, 5], "children": []},
    ]}
    v = SQ.validate_skeleton(overlap, 5)
    check(not v["ok"] and any(e["code"] == "overlap" for e in v["errors"]),
          "重叠被拒", v["errors"])

    bad_parent = {"title": "t", "children": [
        {"title": "p", "src_range": [0, 2], "children": [
            {"title": "c", "src_range": [2, 4], "children": []}]},
        {"title": "d", "src_range": [4, 5], "children": []},
    ]}
    v = SQ.validate_skeleton(bad_parent, 5)
    check(not v["ok"] and any(e["code"] == "parent_range" for e in v["errors"]),
          "子区间越出父区间被拒", v["errors"])

    deep = {"title": "d0", "children": []}
    for i in range(8):
        deep = {"title": f"d{i}", "children": [deep], "src_range": [0, 5]}
    v = SQ.validate_skeleton(deep, 5)
    check(not v["ok"] and any(e["code"] == "depth" for e in v["errors"]),
          "深度超限被拒", v["errors"])


def test_grounding():
    print("\n2. 标题接地")
    sk = {"title": "教育", "children": [
        {"title": "第一章 教育", "src_range": [0, 2], "children": []},
        {"title": "完全不相关的标题", "src_range": [2, 4], "children": []},
    ]}
    g = SQ.title_grounding(sk, PARAS[:4])
    check(g["rate"] is not None and g["rate"] < 1.0, "幻觉标题被标出", g)
    check("完全不相关的标题" in g["ungrounded"], "未接地列表", g["ungrounded"])


def test_windows():
    print("\n3. 长文窗口 + 拼接")
    paras = [f"段落{i}内容。" for i in range(130)]
    windows = SQ.plan_windows(paras)
    check(len(windows) == 2, "130 段切成 2 个窗口", windows)
    parts = []
    for w in windows:
        n = w["end"] - w["start"]
        parts.append({"title": f"w{w['index']}", "children": [
            {"title": f"块{w['index']}", "src_range": [0, n], "children": []}]})
    st = SQ.stitch_skeletons(parts, windows)
    check(st is not None, "拼接成功")
    v = SQ.validate_skeleton(st, len(paras))
    check(v["ok"] and v["coverage"] == 1.0, "拼接后全覆盖", v)


def test_deterministic():
    print("\n4. 无模型确定性骨架")
    sk = SQ.deterministic_skeleton(PARAS)
    check(sk is not None, "生成骨架")
    v = SQ.validate_skeleton(sk, len(PARAS))
    check(v["ok"], "契约通过", v)
    g = SQ.title_grounding(sk, PARAS)
    check(g["rate"] == 1.0, "标题全部接地", g)
    check(v["depth"] >= 2, "至少两层", v["depth"])

    prose = [f"这是第{i}段普通散文，没有编号。" for i in range(12)]
    sk2 = SQ.deterministic_skeleton(prose)
    v2 = SQ.validate_skeleton(sk2, len(prose))
    check(sk2 is not None and v2["ok"], "无编号时按块分组且全覆盖", v2)
    root, rep = fragment_to_tree("\n\n".join(prose), sk2, "散文")
    chk = assert_no_content_loss(root, split_paragraphs("\n\n".join(prose)))
    check(chk["kept"] == chk["total"], "锚定后零丢字", chk)


def test_quality():
    print("\n5. 质量分")
    good = {"title": "t", "children": [
        {"title": "第一章 教育", "src_range": [0, 2], "children": []},
        {"title": "第一节 概念", "src_range": [2, 5], "children": []},
    ]}
    v = SQ.validate_skeleton(good, 5)
    g = SQ.title_grounding(good, ["第一章 教育", "正文", "第一节 概念", "正文", "正文"])
    q = SQ.quality_score(good, v, g)
    check(q >= 0.8, "合格骨架高分", q)

    gap = {"title": "t", "children": [
        {"title": "a", "src_range": [0, 2], "children": []}]}
    v2 = SQ.validate_skeleton(gap, 5)
    q2 = SQ.quality_score(gap, v2, {"rate": 0.5})
    check(q2 < q, "缺口骨架低分", (q, q2))


def main():
    print("=" * 72)
    print("AI 骨架质量闭环测试")
    print("=" * 72)
    test_validate_contract()
    test_grounding()
    test_windows()
    test_deterministic()
    test_quality()
    print("\n" + "=" * 72)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
