#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地图片引用清理回归测试。

MinerU 输出的 `![](images/<hash>)` 不能作为正文展示；应当收集到节点的
`images` 元数据字段，正文保持干净。代码围栏和远程图片 URL 不受影响。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import server
import tree_builder as tb

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f" | {detail}" if detail else ""))


def test_text():
    print("[1] 单段正文清理")
    text = "\n".join([
        "人——重视知识分子",
        "![](images/5bdb1cc9cfe9135161dc538eb637e9ac6d4e640492c359cc0f5e9c916bc7eb9d.jpg)",
        "正文前 ![](images/abc.jpg) 正文后",
        "images/5bdb1cc9cfe9135161dc538eb637e9ac",
        '<img src="images/html.png" />说明',
        "```",
        "![](images/code.jpg)",
        "```",
        "远程 ![x](https://example.com/a.png)",
    ])
    clean, images = tb.sanitize_text_images(text)
    check("正文中的本地图片路径已移除",
          "5bdb1cc9" not in clean and "abc.jpg" not in clean and "html.png" not in clean, clean)
    check("保留正文文字", "人——重视知识分子" in clean and "正文前" in clean and "正文后" in clean)
    check("收集全部本地图片", len(images) == 4, images)
    check("图片路径去重", len(images) == len(set(images)), images)
    check("代码围栏内图片原样保留", "![](images/code.jpg)" in clean, clean)
    check("远程图片 URL 不被误删", "https://example.com/a.png" in clean, clean)


def test_tree():
    print("[2] 整树递归清理")
    tree = {
        "title": "root",
        "content": "根正文",
        "children": [{
            "title": "章",
            "content": "正文\n![](images/a.jpg)",
            "summary": "摘要 ![](images/b.jpg)",
            "children": [{
                "title": "节",
                "content": "images/5bdb1cc9cfe9135161dc538eb637e9ac",
                "exam_points": ["考点 ![](images/c.jpg)"],
            }],
        }],
    }
    tb.sanitize_tree_images(tree)
    chapter = tree["children"][0]
    section = chapter["children"][0]
    check("章正文清理", "images/" not in chapter["content"], chapter["content"])
    check("章摘要清理", "images/" not in chapter["summary"], chapter["summary"])
    check("节正文清理", section["content"] == "", section["content"])
    check("考点清理", section["exam_points"] == ["考点"], section["exam_points"])
    check("章图片元数据", chapter.get("images") == ["images/a.jpg", "images/b.jpg"], chapter.get("images"))
    check("节图片元数据", section.get("images") == ["images/5bdb1cc9cfe9135161dc538eb637e9ac", "images/c.jpg"], section.get("images"))


def test_final_json():
    print("[3] 最终 JSON 防线")
    tree = {
        "title": "Knowledge Tree",
        "children": [{
            "title": "知识点",
            "content": "正文\n![](images/x.jpg)",
            "children": [],
        }],
    }
    out = server._prepare_final_json(tree)
    node = out["children"][0]
    check("最终树正文已清理", "images/" not in node.get("content", ""), node.get("content"))
    check("最终树保留图片元数据", node.get("images") == ["images/x.jpg"], node.get("images"))
    check("最终树字段完整", "summary" in node and "keywords" in node and "children" in node)


if __name__ == "__main__":
    print("=" * 64)
    print("本地图片引用清理回归")
    print("=" * 64)
    test_text()
    test_tree()
    test_final_json()
    print("=" * 64)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
