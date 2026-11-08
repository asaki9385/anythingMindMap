#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编号画像测试：样本推断 / 未知族泛化 / 重启作用域 / 低置信度回退。

运行：python knowledge-compiler/test_numbering_profile.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import numbering_profile as NP  # noqa: E402
from outline_skeleton import normalize_markdown_outline  # noqa: E402
from tree_builder import (parse_md_to_nodes, adjust_standalone_levels,  # noqa: E402
                          build_tree)
from hierarchy_repair import apply_hierarchy_repair  # noqa: E402

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


def build(text, profile):
    norm, rep = normalize_markdown_outline(text, profile=profile)
    nodes = parse_md_to_nodes(norm, profile=profile)
    nodes = adjust_standalone_levels(nodes)
    nodes = apply_hierarchy_repair(nodes, profile=profile)
    return norm, rep, build_tree(nodes)


def find(nodes, title):
    for n in nodes or []:
        if (n.get("title") or "").strip() == title:
            return n
        r = find(n.get("children"), title)
        if r:
            return r
    return None


def level_of(nodes, title):
    n = find(nodes, title)
    return n.get("level") if n else None


BASE = """# 第一章 教育概述
# 第一节 教育的概念
# 一、教育的定义
# （一）广义教育
# 1. 社会教育
# （1）家庭教育
# ① 教育目的
# 第二节 教育的发展
# 一、教育的历史
# 第二章 心理学
# 第一节 认知发展
# 一、感知
"""


def test_probe():
    print("\n1. 样本画像：族顺序 / 重启作用域")
    p = NP.probe_numbering_profile(BASE)
    check(p["effective"], "置信度达标", p["confidence"])
    check(p["order"][0] == "chapter_cn", "章在最前", p["order"])
    check("section_cn" in p["order"], "识别节", p["order"])
    check(p["restart_scope"].get("section_cn") == "chapter_cn",
          "节在新章后重启", p["restart_scope"])
    check(p["families"]["chapter_cn"]["count"] == 2, "章计数", p["families"])
    check("编号体系" in NP.profile_summary(p), "摘要可读", NP.profile_summary(p))


def test_generic_classify():
    print("\n2. 未知族泛化")
    cases = [
        ("第一讲 教育学", "unit_cn", 1),
        ("第二单元 教育", "unit_cn", 2),
        ("第三课 教学", "unit_cn", 3),
        ("专题四 课程", "topic_cn", 4),
        ("第五篇 教育史", "volume_cn", 5),
        ("Ⅰ. 概述", "roman_dotted", 1),
        ("（Ⅱ）发展", "roman_bracket", 2),
        ("A. 定义", "alpha_dotted", 1),
        ("b) 特点", "alpha_dotted", 2),
        ("㉑ 目的", "circle_ext", 21),
        ("1．2．3 细节", "arabic_dotted", None),
    ]
    for title, want, val in cases:
        info = NP.classify_generic(title)
        check(info is not None and info["type"] == want,
              f"{title} → {want}", info)
        if info and val is not None:
            check(info["path"][0] == val, f"{title} 值 = {val}", info["path"])


def test_promote_generic():
    print("\n3. 未知族进入解析链（第X讲 / 字母）")
    text = """第一讲 教育学
教育是研究教育现象的一门科学。

一、教育的概念
广义的教育指一切有目的的活动。

（一）定义
狭义的教育指学校教育。

1. 社会教育
社会教育是教育的一部分。

（1）家庭教育
家庭教育是基础。

第二讲 心理学
一、认知
感知是认知的起点。

A. 感知
感知包括视觉和听觉。

B. 记忆
记忆是信息的存储。
"""
    p = NP.probe_numbering_profile(text)
    norm, rep, roots = build(text, p)
    check(any(r["title"] == "第一讲 教育学" for r in rep["recovered"]),
          "第一讲被提升", rep["recovered"][:3])
    check(any(r["title"] == "A. 感知" for r in rep["recovered"]),
          "字母编号被提升", [r["title"] for r in rep["recovered"]])
    check(any(r["title"] == "一、认知" for r in rep["recovered"]
              if r.get("kind") == "generic"), "第二讲下的 一、被提升")
    check(level_of(roots, "第一讲 教育学") == 1, "第一讲 L1",
          level_of(roots, "第一讲 教育学"))
    check(level_of(roots, "一、教育的概念") == 2, "一、 L2",
          level_of(roots, "一、教育的概念"))
    check(level_of(roots, "（一）定义") == 3, "（一） L3",
          level_of(roots, "（一）定义"))
    check(level_of(roots, "A. 感知") == 3, "A. 感知在 一、 下",
          level_of(roots, "A. 感知"))
    check(find(roots, "第二讲 心理学") is not None, "第二讲存在")


def test_restart_scope():
    print("\n4. 重启作用域：新章后的节重启提升，同节内的知识点回退不提升")
    text = """# 第一章 教育
# 第一节 概念
# 知识点一 定义
知识点正文。
# 知识点二 特点
知识点正文。
知识点一 又一条
这行不应成为标题。
# 第二章 心理
第一节 认知
认知正文。
"""
    p = NP.probe_numbering_profile(text)
    norm, rep, roots = build(text, p)
    check(any(r["title"] == "第一节 认知" for r in rep["recovered"]),
          "新章后的节重启被提升", rep["recovered"])
    check(not any(r["title"] == "知识点一 又一条" for r in rep["recovered"]),
          "同节内知识点回退不提升", rep["recovered"])
    check(find(roots, "第一节 认知") is not None, "节节点存在")
    check(find(roots, "知识点一 又一条") is None, "回退行未成为节点")


def test_low_confidence():
    print("\n5. 低置信度回退")
    p = NP.probe_numbering_profile("随便一段话，没有编号。")
    check(not p["effective"], "无编号时 profile 不生效", p["confidence"])
    check(NP.profile_level(p, "chapter_cn") is None,
          "不生效时 profile_level 返回 None")
    # profile 不生效时，旧 15 族仍按原逻辑识别
    from hierarchy_repair import detect_numbering_type, normalize_numbering
    known = detect_numbering_type(normalize_numbering("第一章 教育"))
    check(known and known["type"] == "chapter_cn", "旧族识别不受影响")


def test_merge():
    print("\n6. 多文件画像合并")
    a = NP.probe_numbering_profile("# 第一章 A\n# 第一节 B\n# 一、C\n")
    b = NP.probe_numbering_profile("# 第一章 D\n# 第一节 E\n# 一、F\n")
    m = NP.merge_profiles([a, b])
    check(m and m["files"] == 2, "文件数", m and m["files"])
    check(m["families"]["chapter_cn"]["count"] == 2, "计数累加", m["families"])
    check(m["effective"], "合并后仍生效", m["confidence"])


def test_none_profile_compat():
    print("\n7. profile=None 完全兼容旧链路")
    text = "# 第一章 教育\n# 第一节 概念\n# 一、定义\n"
    nodes = parse_md_to_nodes(text, profile=None)
    nodes = apply_hierarchy_repair(nodes, profile=None)
    roots = build_tree(nodes)
    check(level_of(roots, "第一章 教育") == 1, "旧路径章 L1")
    check(level_of(roots, "第一节 概念") == 2, "旧路径节 L2")
    check(level_of(roots, "一、定义") == 3, "旧路径列表 L3")


def main():
    print("=" * 72)
    print("编号画像测试")
    print("=" * 72)
    test_probe()
    test_generic_classify()
    test_promote_generic()
    test_restart_scope()
    test_low_confidence()
    test_merge()
    test_none_profile_compat()
    print("\n" + "=" * 72)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
