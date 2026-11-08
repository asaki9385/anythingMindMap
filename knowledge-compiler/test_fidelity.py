#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原文保真度审计测试（离线）。

验收口径：
  · 干净文档段落召回 / 字符覆盖 = 100%；
  · 删掉一段必须被报出来，不能因为模糊匹配而虚高；
  · 重复段落按出现次数计算；
  · TOC / 页眉 / 页码 / 纯图片段落在 excluded，不算丢失；
  · 合成节点（来源PDF / 衔接 / 占位）不污染标题精确率；
  · 顺序倒置会被 order_score 捕捉；
  · 空输入不抛异常。

运行：python knowledge-compiler/test_fidelity.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import fidelity as F  # noqa: E402

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


def build_tree(text):
    from tree_builder import (parse_md_to_nodes, adjust_standalone_levels,
                              build_tree as tree_build)
    from hierarchy_repair import apply_hierarchy_repair
    nodes = parse_md_to_nodes(text)
    nodes = adjust_standalone_levels(nodes)
    nodes = apply_hierarchy_repair(nodes)
    return {"title": "book", "children": tree_build(nodes)}


CLEAN = """# 第一章 教育概述

教育是研究教育现象的一门科学，广义的教育指一切有目的的活动。

## 第一节 教育的概念

狭义的教育指学校教育。教育具有历史性。

## 第二节 教育的发展

教育具有社会性。学校教育是教育的主要形式。"""


def test_clean():
    print("\n1. 干净文档：全覆盖")
    tree = build_tree(CLEAN)
    r = F.audit_unit(CLEAN, tree, route="regex")
    check(r["paragraph_recall"] == 1.0, "段落召回 100%", r["paragraph_recall"])
    check(r["char_coverage"] == 1.0, "字符覆盖 100%", r["char_coverage"])
    check(r["heading_recall"] == 1.0, "标题召回 100%", r["heading_recall"])
    check(r["order_score"] == 1.0, "顺序保真 100%", r["order_score"])
    counts = r["coverage_counts"]
    check(counts["paragraphs"]["matched"] == counts["paragraphs"]["total"],
          "段落命中计数完整", counts["paragraphs"])
    check(counts["chars"]["matched"] == counts["chars"]["total"],
          "字符命中计数完整", counts["chars"])
    check(counts["headings"]["matched"] == counts["headings"]["total"],
          "标题命中计数完整", counts["headings"])
    check(r["verdict"]["level"] == "high", "判定 high", r["verdict"])


def test_missing():
    print("\n2. 丢失检测")
    tree = build_tree(CLEAN)
    # 清空第二节节点的 content
    target = None
    def walk(n):
        nonlocal target
        if "第二节" in (n.get("title") or ""):
            target = n
        for c in n.get("children") or []:
            walk(c)
    for c in tree["children"]:
        walk(c)
    check(target is not None, "找到第二节节点")
    target["content"] = ""
    r = F.audit_unit(CLEAN, tree, route="regex")
    check(r["paragraph_recall"] < 1.0, "段落召回下降", r["paragraph_recall"])
    counts = r["coverage_counts"]
    check(counts["paragraphs"]["matched"] < counts["paragraphs"]["total"],
          "段落缺口计数一致", counts["paragraphs"])
    check(counts["chars"]["matched"] < counts["chars"]["total"],
          "字符缺口计数一致", counts["chars"])
    lost = " ".join(x["text"] for x in r["lost_paragraphs"])
    check("社会性" in lost or "学校教育" in lost, "丢失段落被列出", lost)


def test_toc_and_noise():
    print("\n3. TOC / 页眉 / 页码 / 图片不计入丢失")
    text = """# 第一章 教育概述 / 1
## 第一节 教育的概念 / 2
## 第二节 教育的发展 / 5

# 第一章 教育概述

教育是研究教育现象的一门科学。

![](images/hash.png)

12

## 第一节 教育的概念

狭义的教育指学校教育。"""
    tree = build_tree(text)
    r = F.audit_unit(text, tree, route="regex")
    ex = r["source"]["excluded"]
    check(ex["toc"] > 0, "TOC 被排除", ex)
    check(ex["images"] > 0, "纯图片段被排除", ex)
    check(ex["page_numbers"] > 0, "页码段被排除", ex)
    check(r["paragraph_recall"] == 1.0, "排除后仍 100%", r["paragraph_recall"])


def test_duplicates():
    print("\n4. 重复段落按出现次数")
    text = "重复段落内容。\n\n唯一段落。\n\n重复段落内容。"
    tree_once = {"title": "book", "children": [
        {"title": "a", "content": "重复段落内容。\n\n唯一段落。", "children": []}]}
    r1 = F.audit_unit(text, tree_once, route="regex")
    check(r1["paragraph_recall"] < 1.0, "只出现一次 → 第二次算丢失", r1["paragraph_recall"])

    tree_twice = {"title": "book", "children": [
        {"title": "a", "content": text, "children": []}]}
    r2 = F.audit_unit(text, tree_twice, route="regex")
    check(r2["paragraph_recall"] == 1.0, "出现两次 → 100%", r2["paragraph_recall"])


def test_synthetic():
    print("\n5. 合成节点不污染标题精确率")
    text = "# 第一章 教育概述\n\n教育是研究教育现象的一门科学。\n\n## 第一节 教育的概念\n\n狭义的教育指学校教育。"
    base = build_tree(text)
    r1 = F.audit_unit(text, base, route="regex")

    synthetic = build_tree(text)
    synthetic["children"].insert(0, {
        "title": "来源PDF：某文件", "content": "来源PDF：某文件", "children": []})
    synthetic["children"].insert(0, {
        "title": "与《上一份》的上下文衔接", "content": "衔接文本", "children": []})
    r2 = F.audit_unit(text, synthetic, route="regex")
    check(r2["heading_precision"] == r1["heading_precision"],
          "标题精确率不受合成节点影响", (r1["heading_precision"], r2["heading_precision"]))


def test_order():
    print("\n6. 顺序保真")
    text = "甲段内容。\n\n乙段内容。"
    tree = {"title": "book", "children": [
        {"title": "乙", "content": "乙段内容。", "children": []},
        {"title": "甲", "content": "甲段内容。", "children": []},
    ]}
    r = F.audit_unit(text, tree, route="regex")
    check(r["order_score"] < 1.0, "倒置被捕捉", r["order_score"])


def test_empty():
    print("\n7. 空输入")
    r = F.audit_unit("", {"title": "t", "children": []}, route="regex")
    check(r["paragraph_recall"] == 1.0, "空文档不抛异常")


def test_multifile():
    print("\n8. 多文件聚合")
    a = "# 第一章\n\n甲文件内容。"
    b = "# 第二章\n\n乙文件内容。"
    tree = {"title": "book", "children": [
        {"title": "第一章", "content": "甲文件内容。", "children": []},
        {"title": "第二章", "content": "乙文件内容。", "children": []},
    ]}
    r = F.audit_final([{"name": "a.md", "text": a}, {"name": "b.md", "text": b}], tree)
    check(r["paragraph_recall"] == 1.0, "跨文件 100%", r["paragraph_recall"])
    check(len(r["by_file"]) == 2, "按文件分列", r["by_file"])
    check(all(f["paragraph_recall"] == 1.0 for f in r["by_file"]),
          "每个文件 100%", r["by_file"])


def main():
    print("=" * 72)
    print("原文保真度审计测试")
    print("=" * 72)
    test_clean()
    test_missing()
    test_toc_and_noise()
    test_duplicates()
    test_synthetic()
    test_order()
    test_empty()
    test_multifile()
    print("\n" + "=" * 72)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
