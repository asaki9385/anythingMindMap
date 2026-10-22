#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构前置层（outline_skeleton）的离线测试。

覆盖：
  · 解析前置：丢失的大纲标题恢复（序列连续性驱动）、重复行/目录行不误收；
  · 解析前置：重复页眉剔除、章名补全、内容框标题不参与章名合并；
  · 骨架校验：同级序列不变量；知识点序列重启 → 「（未识别小节）」占位修复；
  · 守卫：改名不吞并节标题；「333 …」页眉不被当作编号清单；
  · 真实文件回归（存在时）：颜之推/韩愈、董仲舒/王充落在正确的节下。

运行：python3 knowledge-compiler/test_outline_skeleton.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from outline_skeleton import (
    normalize_markdown_outline, check_skeleton, repair_boundaries,
    audit_tree_skeleton,
)
from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree
from hierarchy_repair import apply_hierarchy_repair, detect_numbering_type
from placement_audit import fix_topic_placement

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" | {detail}" if detail else ""))


def build_pipeline(md, doc_hint=""):
    """复刻 server._build_trees_from_md 的解析链（不含 LLM 校验）。"""
    md, rep = normalize_markdown_outline(md, doc_hint=doc_hint)
    nodes = parse_md_to_nodes(md)
    nodes = adjust_standalone_levels(nodes)
    nodes = apply_hierarchy_repair(nodes)
    roots = build_tree(nodes)
    audit = audit_tree_skeleton(roots)
    try:
        fix_topic_placement(roots, log=lambda *a: None)
    except Exception:
        pass
    return roots, rep, audit


def find(nodes, kw):
    """DFS 找标题包含 kw 的第一个节点。"""
    for n in nodes or []:
        if kw in (n.get("title") or ""):
            return n
        r = find(n.get("children"), kw)
        if r:
            return r
    return None


def find_all(nodes, kw):
    out = []

    def walk(ns):
        for n in ns or []:
            if kw in (n.get("title") or ""):
                out.append(n)
            walk(n.get("children"))

    walk(nodes)
    return out


def direct_child(parent, title):
    for c in (parent.get("children") or []):
        if (c.get("title") or "").strip() == title:
            return c
    return None


def parent_of(nodes, kw, parent=None):
    for n in nodes or []:
        if kw in (n.get("title") or ""):
            return parent
        r = parent_of(n.get("children") or [], kw, n)
        if r is not None:
            return r
    return None


def titles(nodes):
    out = []

    def walk(ns):
        for n in ns or []:
            out.append(n.get("title") or "")
            walk(n.get("children"))

    walk(nodes)
    return out


# ─────────────────────────────────────────────────────────────
# 1. 丢失的小节标题恢复 + 序列守卫
# ─────────────────────────────────────────────────────────────

MD_LOST_SECTION = """## 第三章 儒学的初步形成

## 第一节 汉代的文教政策

内容一。

## 第二节 汉代的学校教育制度

制度正文……

## 第三节 隋唐时期的选士制度

## 知识点一 科举制度的萌芽与确立

科举萌芽的正文……

## 知识点二 科举考试的程序

程序的正文……

第四节 魏晋南北朝与隋唐时期的教育思想  
![](images/x.jpg)

## 知识点一 颜之推的教育思想

颜之推是魏晋南北朝时期梁朝人……

## 知识点二 韩愈的教育思想

韩愈是唐代著名的政治家、思想家……

第三节 隋唐时期的选士制度

全国举子定时集中到京都。
"""


def test_recovers_lost_section():
    print("[1] 解析前置：恢复丢失的小节标题，重复行不误收")
    roots, rep, audit = build_pipeline(MD_LOST_SECTION)

    sec4 = find(roots, "第四节 魏晋南北朝与隋唐时期的教育思想")
    check("丢失的第四节被恢复", sec4 is not None)
    if sec4:
        kids = [c.get("title") for c in sec4.get("children", [])]
        check("颜之推挂在新恢复的第四节下", any("颜之推的教育思想" in k for k in kids), str(kids))
        check("韩愈挂在新恢复的第四节下", any("韩愈的教育思想" in k for k in kids), str(kids))
    yz = find(roots, "颜之推的教育思想")
    chain = []
    if yz:
        def path_to(ns, kw, path):
            for n in ns or []:
                p = path + [n.get("title") or ""]
                if kw in (n.get("title") or ""):
                    return p
                r = path_to(n.get("children"), kw, p)
                if r:
                    return r
            return None
        chain = path_to(roots, "颜之推的教育思想", []) or []
    check("颜之推的祖先链里没有韩愈节点",
          chain and not any("韩愈" in t for t in chain[:-1]), str(chain))
    dup = [t for t in titles(roots) if t.startswith("第三节 隋唐时期的选士制度")]
    check("重复的第三节没有被提升成第二个节点", len(dup) == 1, str(dup))
    check("恢复报告含第四节", any("第四节" in r.get("title", "") for r in rep.get("recovered", [])),
          str(rep.get("recovered")))


def test_rejects_duplicate_and_toc():
    print("[2] 解析前置：重复行 / 目录行不提升")
    md = """## 第一节 教育的产生与发展

## 第二节 教育的发展

第二节 教育的发展

## 第三节 教育学的产生

第三节 教育学的产生 / 15
"""
    roots, rep, audit = build_pipeline(md)
    a = [t for t in titles(roots) if t.startswith("第二节 教育的发展")]
    b = [t for t in titles(roots) if t.startswith("第三节 教育学的产生")]
    check("重复的第二节只有 1 个节点", len(a) == 1, str(a))
    check("带页码的目录行不产生节点", len(b) == 1, str(b))
    check("没有把重复行当恢复上报", len(rep.get("recovered", [])) == 0, str(rep.get("recovered")))


# ─────────────────────────────────────────────────────────────
# 2. 页眉剔除 + 编号误判守卫
# ─────────────────────────────────────────────────────────────

HINT_333 = "01_000_02.333大纲知识清单中教第三-五章"


def test_running_header():
    print("[3] 解析前置：重复页眉剔除（333 …）")
    md = """## 第三章 儒学的初步形成

## 333 教育综合考研大纲知识清单（中国教育史分册）

## 丹丹导学

内容……

## 333 教育综合考研大纲知识清单（中国教育史分册）

## 第一节 汉代的文教政策

## 333 教育综合考研大纲知识清单（中国教育史分册）

## 第二节 汉代的学校教育制度
"""
    roots, rep, audit = build_pipeline(md, doc_hint=HINT_333)
    leaked = [t for t in titles(roots) if "333 教育综合" in t]
    check("页眉不再是节点", not leaked, str(leaked))
    check("剔除记录进报告", any("333" in d.get("text", "") for d in rep.get("dropped_headers", [])),
          str(rep.get("dropped_headers")))
    check("编号检测不再把「333 …」当清单", detect_numbering_type(
        "333 教育综合考研大纲知识清单（中国教育史分册）") is None)
    check("正常阿拉伯编号仍可用", (detect_numbering_type("23. 教育制度") or {}).get("type") == "arabic_dotted")


def test_keeps_legit_repeated_titles():
    print("[4] 解析前置：真框标题不误删（逻辑框架 / 课后作业），页眉按文件名识别")
    md = """## 考研政治 核心考案

## 第一章 绪论

内容……

## 课后作业

练习一

## 考研政治 核心考案

## 第二章 世界的物质性

## 课后作业

练习二

## 考研政治 核心考案

## 第三章 实践与认识

## 课后作业

练习三
"""
    roots, rep, audit = build_pipeline(md, doc_hint="01_000_26徐涛《核心考案》_前100页")
    box = [t for t in titles(roots) if t == "课后作业"]
    check("按章重复的框标题保留（3 个）", len(box) == 3, str(box))
    leaked = [t for t in titles(roots) if "核心考案" in t]
    check("与文件名同源的页眉被剔除", not leaked, str(leaked))


# ─────────────────────────────────────────────────────────────
# 3. 章名补全 + 内容框合并守卫
# ─────────────────────────────────────────────────────────────

def test_chapter_subtitle_and_box_guard():
    print("[5] 解析前置：章名补全，内容框不参与合并")
    md = """![](images/cover.jpg)

## 第三章

儒学独尊与读经做官教育模式的初步形成

## 知识导图

![](images/map.jpg)

## 丹丹导学

本章的学习内容是……

## 第一节 汉代的文教政策

内容……
"""
    roots, rep, audit = build_pipeline(md)
    ch3 = [t for t in titles(roots) if t.startswith("第三章")]
    check("章标题补全为真实章名",
          any(t == "第三章 儒学独尊与读经做官教育模式的初步形成" for t in ch3), str(ch3))
    check("不存在「第三章 知识导图」合并", not any("知识导图" in t and "第三章" in t for t in titles(roots)),
          str(titles(roots)))
    check("知识导图仍作为独立节点存在", any(t == "知识导图" for t in titles(roots)))

    md_bare = """## 第三章

## 知识导图

地图内容

## 第一节 汉代的文教政策

内容……
"""
    roots2, rep2, audit2 = build_pipeline(md_bare)
    check("裸章号不与内容框合并", any(t == "第三章" for t in titles(roots2)), str(titles(roots2)))
    check("裸章号场景下无「第三章 知识导图」",
          not any("知识导图" in t and "第三章" in t for t in titles(roots2)))


def test_container_and_peer_headings():
    print("[5b] 容器族：无编号知识点 / 拆分容器 / 平级独立标题（考点链）")

    # A. 无编号「知识点 X」容器：子项不能被穿透、也不被上部框吸走
    md = """## 第三节 汉代的选士制度

## 精华提要

提要文字。

## 知识点 察举制度

## (一) 察举制度的内容

内容正文。

## 丹丹攻略

攻略文字。

## (二) 察举制度的评价

## 1. 成功之处

正文。

## 2. 缺陷

正文。
"""
    roots, rep, audit = build_pipeline(md)
    sec = find(roots, "第三节 汉代的选士制度")
    kp = find(roots, "知识点 察举制度")
    check("知识点容器在节下（不在精华提要里）",
          sec is not None and kp is not None and direct_child(sec, "知识点 察举制度") is not None
          and direct_child(sec, "精华提要") is not None)
    if kp:
        check("（一）是容器的子项", direct_child(kp, "（一）察举制度的内容") is not None)
        check("丹丹攻略是容器的子项", direct_child(kp, "丹丹攻略") is not None)
        check("（二）是容器的子项", direct_child(kp, "（二）察举制度的评价") is not None)
        ev = direct_child(kp, "（二）察举制度的评价")
        if ev:
            check("1./2. 是（二）的子项",
                  direct_child(ev, "1. 成功之处") is not None
                  and direct_child(ev, "2. 缺陷") is not None)
    check("（一）不再被穿透到节级别",
          sec is not None and direct_child(sec, "（一）察举制度的内容") is None)

    # B. 拆分容器：「知识点」+ 名称两行（OCR 拆行）
    md_split = """## 第四节 另一节

## 知识点

## 学习动机的培养与激发

## (一) 教学吸引

正文。
"""
    roots2, _, _ = build_pipeline(md_split)
    merged = find(roots2, "知识点 学习动机的培养与激发")
    check("裸「知识点」与名称合并为容器", merged is not None)
    if merged:
        check("（一）挂在合并后的容器下", direct_child(merged, "（一）教学吸引") is not None)

    # C. 平级独立标题不链式互套（考点 1 / 考点 2 是兄弟）
    md_peers = """## 第一章 马原

## 考点 1 甲

内容一。

## 考点 2 乙

内容二。
"""
    roots3, _, _ = build_pipeline(md_peers)
    ch = find(roots3, "第一章 马原")
    check("考点 1 / 考点 2 是同级兄弟",
          ch is not None and direct_child(ch, "考点 1 甲") is not None
          and direct_child(ch, "考点 2 乙") is not None,
          str([c.get("title") for c in (ch.get("children") or [])]) if ch else "missing")

    # D. 无编号平级标题：同级 → 兄弟；hash 更深 → 保持嵌套
    md_bare = """## 第一节 学校

## 文翁兴学

正文一。

## 六等黜陟法

正文二。

### 兴学细节

正文三。
"""
    roots4, _, _ = build_pipeline(md_bare)
    sec4 = find(roots4, "第一节 学校")
    check("无编号同级标题互为兄弟",
          sec4 is not None and direct_child(sec4, "文翁兴学") is not None
          and direct_child(sec4, "六等黜陟法") is not None,
          str([c.get("title") for c in (sec4.get("children") or [])]) if sec4 else "missing")
    deep = find(roots4, "兴学细节")
    check("hash 更深的标题保持嵌套", deep is not None
          and parent_of(roots4, "兴学细节") is not None
          and "六等黜陟法" in ((parent_of(roots4, "兴学细节") or {}).get("title") or ""))


# ─────────────────────────────────────────────────────────────
# 4. 骨架校验 + 占位修复
# ─────────────────────────────────────────────────────────────

def test_restart_placeholder():
    print("[6] 骨架校验：知识点序列重启 → 占位分隔")
    roots = [{
        "title": "第三节 隋唐时期的选士制度", "level": 2, "content": "", "children": [
            {"title": "知识点一 科举制度的萌芽与确立", "level": 3, "content": "x", "children": []},
            {"title": "知识点二 科举考试的程序", "level": 3, "content": "y", "children": []},
            {"title": "知识点一 颜之推的教育思想", "level": 3, "content": "z", "children": []},
            {"title": "知识点二 韩愈的教育思想", "level": 3, "content": "w", "children": []},
        ],
    }]
    before = check_skeleton(roots)
    check("重启被检出（error）",
          any(v.get("type") == "kp_restart" and v.get("severity") == "error" for v in before),
          str(before[:3]))
    audit = audit_tree_skeleton(roots)
    kids = roots[0]["children"]
    check("节内只留自己的知识点",
          [c.get("title") for c in kids] == ["知识点一 科举制度的萌芽与确立", "知识点二 科举考试的程序"],
          str([c.get("title") for c in kids]))
    ph = [c for c in roots if c.get("title") == "（未识别小节）"]
    check("占位提升为节的兄弟", len(ph) == 1, str([c.get("title") for c in roots]))
    if ph:
        moved = [c.get("title") for c in ph[0].get("children", [])]
        check("后段条目移入占位节点",
              any("颜之推" in m for m in moved) and any("韩愈" in m for m in moved), str(moved))
    after = check_skeleton(roots)
    check("修复后无错误级违规",
          not [v for v in after if v.get("severity") == "error"], str(after[:3]))
    check("报告记录占位修复", len(audit.get("placeholders", [])) == 1, str(audit.get("placeholders")))


def test_restart_placeholder_nested():
    print("[6b] 骨架校验：章内节缺失 → 占位提升到章下（与节同级）")
    roots = [{
        "title": "第一章 教育制度", "level": 1, "content": "", "children": [
            {"title": "第一节 学校", "level": 2, "content": "", "children": [
                {"title": "知识点一 学校的产生", "level": 3, "content": "x", "children": []},
                {"title": "知识点二 学校的发展", "level": 3, "content": "y", "children": []},
                {"title": "知识点一 学制", "level": 3, "content": "z", "children": []},
                {"title": "知识点二 学制的演变", "level": 3, "content": "w", "children": []},
            ]},
            {"title": "第二节 考试", "level": 2, "content": "", "children": []},
        ],
    }]
    audit = audit_tree_skeleton(roots)
    ch = roots[0]
    titles = [c.get("title") for c in ch["children"]]
    check("占位是章的直接子节点", "（未识别小节）" in titles, str(titles))
    idx_sec1 = titles.index("第一节 学校")
    idx_ph = titles.index("（未识别小节）")
    check("占位紧跟第一节（层级和位置都对）", idx_ph == idx_sec1 + 1, str(titles))
    ph = ch["children"][idx_ph]
    moved = [c.get("title") for c in ph.get("children", [])]
    check("后段知识点跟随占位", any("学制" in m for m in moved), str(moved))
    after = check_skeleton(roots)
    check("修复后无错误级违规",
          not [v for v in after if v.get("severity") == "error"], str(after[:3]))


def test_check_sequence_violations():
    print("[7] 骨架校验：同级序列不变量（节断档 / 知识点跳号）")
    roots = [{
        "title": "第二章 教育制度", "level": 1, "content": "", "children": [
            {"title": "第一节 学校", "level": 2, "content": "", "children": []},
            {"title": "第三节 学制", "level": 2, "content": "", "children": [
                {"title": "知识点一 学制的历史", "level": 3, "content": "", "children": []},
                {"title": "知识点三 学制的改革", "level": 3, "content": "", "children": []},
            ]},
        ],
    }]
    vs = check_skeleton(roots)
    types = {v.get("type") for v in vs}
    check("节断档被检出", "section_gap" in types, str(types))
    check("知识点跳号被检出", "kp_gap" in types, str(types))


# ─────────────────────────────────────────────────────────────
# 5. 守卫：改名 / 幂等
# ─────────────────────────────────────────────────────────────

def test_rename_guard():
    print("[8] 守卫：坏标题改名不得吞并节标题")
    from structure_repair import _rename_allowed
    check("拒绝「第五章 → 第五章 第一节 …」",
          _rename_allowed("第五章", "第五章 第一节 科举制度的演变和文教政策的发展") is False)
    check("允许补全章名",
          _rename_allowed("第五章", "第五章 理学教育思想和学校的改革与发展") is True)
    check("允许知识点补名", _rename_allowed("知识点四", "知识点四 学制") is True)


def test_merge_reports():
    print("[9b] 报告合并：键齐全、不抛异常（server 路径防回归）")
    from outline_skeleton import merge_reports, new_report
    mt = {"title": "第二章 X", "level": 1, "children": [
        {"title": "知识点一 A", "content": "", "children": []},
        {"title": "知识点一 B", "content": "", "children": []},
    ]}
    audit = audit_tree_skeleton([mt])
    merged = merge_reports([new_report(), audit, None])
    check("合并成功且保留审计计数", merged["placeholders"] and merged["errors_before"] >= 1,
          str({k: merged[k] for k in ("placeholders", "errors_before", "errors_after")}))
    check("合并报告键齐全",
          all(k in merged for k in ("recovered", "dropped_headers", "chapter_titles",
                                    "deduped_titles", "duplicates", "placeholders",
                                    "warnings", "violations_before", "violations_after",
                                    "errors_before", "errors_after")))


def test_normalize_idempotent():
    print("[9] 幂等：normalize 两次结果一致")
    md1, rep1 = normalize_markdown_outline(MD_LOST_SECTION)
    md2, rep2 = normalize_markdown_outline(md1)
    check("两次结果一致", md1 == md2)
    check("第二次不再重复恢复", len(rep2.get("recovered", [])) == 0, str(rep2.get("recovered")))


# ─────────────────────────────────────────────────────────────
# 6. 真实文件回归
# ─────────────────────────────────────────────────────────────

REAL_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "data/_temp_uploads/1f7ec904/markdown/01_000_02.333大纲知识清单中教第三-五章.md")
REAL_HINT = "01_000_02.333大纲知识清单中教第三-五章"


def test_real_file_regression():
    print("[10] 真实文件回归（333 中教第三-五章）")
    if not os.path.exists(REAL_MD):
        print("  SKIP 真实文件不存在（临时数据被清理）")
        return
    with open(REAL_MD, encoding="utf-8") as f:
        md = f.read()
    roots, rep, audit = build_pipeline(md, doc_hint=REAL_HINT)

    ch4 = find(roots, "第四章")
    sec_yz = find(roots, "第四节 魏晋南北朝与隋唐时期的教育思想")
    check("第四章存在且含正确的第四节", ch4 is not None and sec_yz is not None)
    if sec_yz:
        kids = [c.get("title") for c in sec_yz.get("children", [])]
        check("颜之推在第四节下", any("颜之推的教育思想" in k for k in kids), str(kids))
        check("韩愈在第四节下", any("韩愈的教育思想" in k for k in kids), str(kids))
    if ch4:
        ch4_kids = [c.get("title") for c in ch4.get("children", [])]
        check("第四章含第一节 魏晋南北朝时期的教育",
              any("第一节 魏晋南北朝时期的教育" in k for k in ch4_kids), str(ch4_kids))

    ch3 = find(roots, "第三章")
    check("第三章标题补全", ch3 is not None and ch3.get("title") == "第三章 儒学独尊与读经做官教育模式的初步形成",
          ch3.get("title") if ch3 else "missing")
    sec_ds = find(roots, "第四节 汉代的教育思想")
    check("第三章含第四节 汉代的教育思想", sec_ds is not None)
    if sec_ds:
        kids = [c.get("title") for c in sec_ds.get("children", [])]
        check("董仲舒在汉代教育思想节下", any("董仲舒" in k for k in kids), str(kids))
        check("王充在汉代教育思想节下", any("王充" in k for k in kids), str(kids))

    check("页眉不成为节点", not [t for t in titles(roots) if "333 教育综合" in t])
    check("无「第三章 知识导图」", not [t for t in titles(roots) if "知识导图" in t and "第三章" in t])
    errs = [v for v in check_skeleton(roots) if v.get("severity") == "error"]
    check("修复后无错误级违规", not errs, str(errs[:3]))
    check("占位修复为 0（标题都被找回，无需占位）", len(audit.get("placeholders", [])) == 0,
          str(audit.get("placeholders")))
    check("恢复数量 >= 3", len(rep.get("recovered", [])) >= 3, str(rep.get("recovered")))

    # 容器族回归：知识点 察举制度 / 知识点 汉代的文教政策
    sec3 = find(roots, "第三节 汉代的选士制度")
    kp_cj = find(roots, "知识点 察举制度")
    check("知识点 察举制度 是节的直接子节点",
          sec3 is not None and kp_cj is not None and direct_child(sec3, "知识点 察举制度") is not None)
    if kp_cj:
        check("（一）察举制度的内容 挂在容器下", direct_child(kp_cj, "（一）察举制度的内容") is not None)
        check("（二）察举制度的评价 挂在容器下", direct_child(kp_cj, "（二）察举制度的评价") is not None)
        ev = direct_child(kp_cj, "（二）察举制度的评价")
        if ev:
            check("1. 察举制度的成功之处 在（二）下", direct_child(ev, "1. 察举制度的成功之处") is not None)
    sec1 = find(roots, "第一节 汉代的文教政策")
    kp_wj = find(roots, "知识点 汉代的文教政策")
    check("知识点 汉代的文教政策 是节的直接子节点",
          sec1 is not None and kp_wj is not None and direct_child(sec1, "知识点 汉代的文教政策") is not None)
    if kp_wj:
        check("（一）汉初文教政策 挂在容器下", direct_child(kp_wj, "（一）汉初文教政策") is not None)


if __name__ == "__main__":
    test_recovers_lost_section()
    test_rejects_duplicate_and_toc()
    test_running_header()
    test_keeps_legit_repeated_titles()
    test_chapter_subtitle_and_box_guard()
    test_container_and_peer_headings()
    test_restart_placeholder()
    test_restart_placeholder_nested()
    test_check_sequence_violations()
    test_rename_guard()
    test_merge_reports()
    test_normalize_idempotent()
    test_real_file_regression()
    print(f"\n{'=' * 44}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
