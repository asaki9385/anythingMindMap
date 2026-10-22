#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""层级恢复系统化测试：破坏矩阵 × 随机书 × 全语料 × 模糊输入。

用户口径的目标：**无论输入的层级有什么问题，恢复出的树都要与"正确的层级"一致**。
本测试把这句话拆成可验证的断言：

A. 随机生成结构正确的"书"（章 > 节 > 知识点 > （一）子项，各带唯一正文标记），
   渲染成 markdown；然后施加 19 类破坏（丢标题标记 / 整行删标题 / 重复行 /
   重复标题行 / 页眉 / 目录（带页码、不带页码、空行分隔）/ 标题带句读 /
   编号带空格 / 代码块与表格干扰 / 小节乱序 / 大混合），
   跑完整恢复链后逐节点核对父级关系、唯一性、正文零丢失、幂等。
   · 标题整行缺失（信息不可从文本恢复）时：必须用「（未识别小节）」占位
     把后段内容放回正确的**层级位置**（章的直接子节点），不得深埋或错挂。
B. 全语料回归：所有真实 md 跑恢复链 → 零错误级违规；已知案例点对点断言。
C. 模糊输入：空文档 / 垃圾字符 / 深层噪声 → 不抛异常、原文不丢。

运行：python3 knowledge-compiler/test_hierarchy_recovery.py
"""

import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from outline_skeleton import (
    normalize_markdown_outline, check_skeleton, audit_tree_skeleton,
    PLACEHOLDER_TITLE,
)
from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree
from hierarchy_repair import apply_hierarchy_repair
import structure_repair
from node_enhancer import cleanup_tree_structure
from placement_audit import fix_topic_placement

SEEDS = int(os.environ.get("RECOVERY_SEEDS", "24"))
HINT = "01_000_02.333大纲知识清单中教第三-五章"

CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
      "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八"]


def cn(n):
    return CN[n]


def noop(*a, **k):
    pass


# =============================================================================
# 随机书生成 / 渲染
# =============================================================================

def make_book(rng):
    book = {"chapters": []}
    for ci in range(1, rng.randint(2, 3) + 1):
        ch = {"title": f"第{cn(ci)}章 章目{ci}", "sections": []}
        for si in range(1, rng.randint(2, 4) + 1):
            sec = {"title": f"第{cn(si)}节 小节{ci}-{si}", "kps": []}
            for ki in range(1, rng.randint(2, 3) + 1):
                kp = {"title": f"知识点{cn(ki)} 要点{ci}-{si}-{ki}", "subs": []}
                for bi in range(1, rng.randint(1, 2) + 1):
                    kp["subs"].append(f"（{cn(bi)}）子项{ci}-{si}-{ki}-{bi}")
                sec["kps"].append(kp)
            ch["sections"].append(sec)
        book["chapters"].append(ch)
    return book


def content_markers(book):
    """所有正文标记（用于"零丢失"断言）。"""
    marks = set()
    for ci, ch in enumerate(book["chapters"], 1):
        marks.add(f"@M{ci}-0@")
        for si, sec in enumerate(ch["sections"], 1):
            marks.add(f"@M{ci}-{si}@")
            for ki, kp in enumerate(sec["kps"], 1):
                marks.add(f"@M{ci}-{si}-{ki}@")
                for bi, _sub in enumerate(kp["subs"], 1):
                    marks.add(f"@M{ci}-{si}-{ki}-{bi}@")
    return marks


def render(book):
    lines = []
    for ci, ch in enumerate(book["chapters"], 1):
        lines += [f"## {ch['title']}", "", f"章前言 @M{ci}-0@ 的内容。", ""]
        for si, sec in enumerate(ch["sections"], 1):
            lines += [f"## {sec['title']}", "", f"节前言 @M{ci}-{si}@ 的内容。", ""]
            for ki, kp in enumerate(sec["kps"], 1):
                lines += [f"## {kp['title']}", "", f"知识点正文 @M{ci}-{si}-{ki}@ 的内容。", ""]
                for bi, sub in enumerate(kp["subs"], 1):
                    lines += [f"## {sub}", "", f"子项正文 @M{ci}-{si}-{ki}-{bi}@ 的内容。", ""]
    return "\n".join(lines)


# =============================================================================
# 破坏函数（md, rng, book → (md2, spec)）
# =============================================================================

SEC_RE = re.compile(r'^## (第[一二三四五六七八九十]+节 .+)$')
KP_RE = re.compile(r'^## (知识点[一二三四五六七八九十]+ .+)$')
CH_RE = re.compile(r'^## (第[一二三四五六七八九十]+章 .+)$')


def _strip_marks(lines, pattern, rng, prob, skip_range=None):
    out, lost = [], []
    for i, ln in enumerate(lines):
        m = pattern.match(ln)
        if skip_range and skip_range[0] <= i <= skip_range[1]:
            out.append(ln)
            continue
        if m and rng.random() < prob:
            out.append(m.group(1))
            lost.append(m.group(1))
        else:
            out.append(ln)
    return out, lost


def _sec_span(lines, sec_title):
    """某节标题行到下一「节/章」标题前的行号区间（含两端；知识点/子项不是边界）。"""
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == f"## {sec_title}":
            start = i
            break
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        if re.match(r'^## 第[一二三四五六七八九十]+[章节]', lines[j]):
            return (start, j - 1)
    return (start, len(lines) - 1)


def corr_clean(md, rng, book):
    return md, {"check": "full", "absent": []}


def corr_lost_section_marks(md, rng, book):
    lines, _ = _strip_marks(md.split("\n"), SEC_RE, rng, 0.6)
    return "\n".join(lines), {"check": "full", "absent": []}


def corr_lost_kp_marks(md, rng, book):
    lines, _ = _strip_marks(md.split("\n"), KP_RE, rng, 0.6)
    return "\n".join(lines), {"check": "full", "absent": []}


def corr_lost_chapter_marks(md, rng, book):
    lines, _ = _strip_marks(md.split("\n"), CH_RE, rng, 0.6)
    return "\n".join(lines), {"check": "full", "absent": []}


def corr_lost_all_marks(md, rng, book):
    lines = md.split("\n")
    for pat in (CH_RE, SEC_RE, KP_RE):
        lines, _ = _strip_marks(lines, pat, rng, 1.0)
    return "\n".join(lines), {"check": "full", "absent": []}


def corr_lost_everything(md, rng, book):
    lines, _ = _strip_marks(md.split("\n"), SEC_RE, rng, 1.0)
    lines, _ = _strip_marks(lines, KP_RE, rng, 1.0)
    lines, _ = _strip_marks(lines, CH_RE, rng, 1.0)
    lines, _ = _strip_marks(lines, re.compile(r'^## (（[一二三四五六七八九十]+）子项.+)$'), rng, 1.0)
    return "\n".join(lines), {"check": "full", "lost_subs": True, "absent": []}


def _delete_section_title(md, rng, book, pick=None):
    lines = md.split("\n")
    chs = book["chapters"]
    while True:
        ci = rng.randrange(len(chs))
        si = rng.randrange(len(chs[ci]["sections"]))
        if pick is None:
            break
        if pick(chs[ci]["sections"][si]):
            break
    sec = chs[ci]["sections"][si]
    target = f"## {sec['title']}"
    lines = [ln for ln in lines if ln.strip() != target]
    return "\n".join(lines), {"check": "deleted", "deleted": {(ci, si)}, "absent": []}


def corr_delete_middle_section(md, rng, book):
    return _delete_section_title(md, rng, book)


def corr_delete_first_section(md, rng, book):
    lines = md.split("\n")
    ci = rng.randrange(len(book["chapters"]))
    sec = book["chapters"][ci]["sections"][0]
    target = f"## {sec['title']}"
    lines = [ln for ln in lines if ln.strip() != target]
    return "\n".join(lines), {"check": "deleted", "deleted": {(ci, 0)}, "absent": []}


def corr_delete_two_consecutive(md, rng, book):
    lines = md.split("\n")
    chs = book["chapters"]
    ci = rng.randrange(len(chs))
    secs = chs[ci]["sections"]
    if len(secs) < 3:
        si = 0
    else:
        si = rng.randrange(len(secs) - 1)
    deleted = {(ci, si), (ci, si + 1)}
    targets = {f"## {secs[si]['title']}", f"## {secs[si + 1]['title']}"}
    lines = [ln for ln in lines if ln.strip() not in targets]
    return "\n".join(lines), {"check": "deleted", "deleted": deleted, "absent": []}


HEADER = "333 综合大纲知识清单（页眉版）"


def corr_running_header(md, rng, book):
    lines = md.split("\n")
    for _ in range(4):
        pos = rng.randrange(len(lines))
        lines[pos:pos] = ["", f"## {HEADER}", ""]
    return "\n".join(lines), {"check": "full", "absent": [HEADER]}


def _toc_lines(book, with_pages, blank_between):
    toc = ["## 目录", ""]
    for ci, ch in enumerate(book["chapters"], 1):
        toc.append(f"第{cn(ci)}章 章目{ci}" + (f" / {ci}" if with_pages else ""))
        if blank_between:
            toc.append("")
        for si, sec in enumerate(ch["sections"], 1):
            toc.append(f"第{cn(si)}节 小节{ci}-{si}" + (f" / {ci * 10 + si}" if with_pages else ""))
            if blank_between:
                toc.append("")
    toc.append("")
    return toc


def corr_toc_with_pages(md, rng, book):
    lines = _toc_lines(book, True, False)
    return "\n".join(lines + md.split("\n")), {"check": "full", "absent": []}


def corr_toc_no_pages_consecutive(md, rng, book):
    lines = _toc_lines(book, False, False)
    return "\n".join(lines + md.split("\n")), {"check": "full", "absent": []}


def corr_toc_no_pages_spaced(md, rng, book):
    lines = _toc_lines(book, False, True)
    return "\n".join(lines + md.split("\n")), {"check": "full", "absent": []}


def corr_dup_plain_runs(md, rng, book):
    """导图残留 / 页眉残留的老实模拟：已出现过的真实标题在原文之后被原样重复。

    序号不会重置 → 重复行必须被拒绝（不产生第二个节点）。
    另加大编号假标题（序列不予接受）与正文行重复。
    """
    lines = md.split("\n")
    picks = []
    for _ in range(3):
        ci = rng.randrange(len(book["chapters"]))
        ch = book["chapters"][ci]
        sec = ch["sections"][rng.randrange(len(ch["sections"]))]
        if rng.random() < 0.5:
            picks.append(sec["title"])
        else:
            kp = sec["kps"][rng.randrange(len(sec["kps"]))]
            picks.append(kp["title"])
    for t in picks:
        idx = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {t}"), None)
        if idx is None:
            continue
        pos = min(len(lines), idx + 6)
        lines[pos:pos] = ["", t, "", "全国举子定时集中到京都。", ""]
    pos = rng.randrange(len(lines))
    lines[pos:pos] = ["", "第七节 导图残留小节", "", "知识点七 导图残留要点", ""]
    pos = rng.randrange(len(lines))
    lines[pos:pos] = ["", "全国举子定时集中到京都。", ""]
    return "\n".join(lines), {"check": "full", "absent": ["导图残留"]}


def corr_dup_heading_lines(md, rng, book):
    lines = md.split("\n")
    out = []
    inserted = 0
    for ln in lines:
        out.append(ln)
        if inserted < 4 and (SEC_RE.match(ln) or KP_RE.match(ln)) and rng.random() < 0.25:
            out.append(ln)             # 紧贴重复（无空行）
            out.append("")             # 或空行后重复
            out.append(ln)
            inserted += 1
    return "\n".join(out), {"check": "full", "absent": []}


def corr_title_with_punct(md, rng, book):
    lines, _ = _strip_marks(md.split("\n"), SEC_RE, rng, 0.5)
    out = []
    for ln in lines:
        m = re.match(r'^(第[一二三四五六七八九十]+节 .+)$', ln)
        if m and rng.random() < 0.5:
            out.append(m.group(1) + "。")
        else:
            out.append(ln)
    return "\n".join(out), {"check": "full", "absent": []}


def corr_spaced_numbers(md, rng, book):
    lines, _ = _strip_marks(md.split("\n"), SEC_RE, rng, 0.5)
    out = []
    for ln in lines:
        m = re.match(r'^(?:## )?(第)([一二三四五六七八九十]+)(节 .+)$', ln)
        m2 = re.match(r'^(?:## )?(知识点)([一二三四五六七八九十]+)( .+)$', ln)
        if m and rng.random() < 0.6:
            out.append(ln.replace(f"第{m.group(2)}节", f"第 {m.group(2)} 节"))
        elif m2 and rng.random() < 0.6:
            out.append(ln.replace(f"知识点{m2.group(2)}", f"知识点 {m2.group(2)}"))
        else:
            out.append(ln)
    return "\n".join(out), {"check": "full", "absent": []}


def corr_mermaid_decoy(md, rng, book):
    lines = md.split("\n")
    decoy = ["```mermaid", "第三节 假节一", "知识点二 假要点二", "```", ""]
    target = None
    for i, ln in enumerate(lines):
        if ln.startswith("## 第二节 "):
            target = i
            break
    if target is None:
        target = len(lines) // 2
    lines[target:target] = decoy
    return "\n".join(lines), {"check": "full", "absent": ["假节", "假要点"]}


def corr_table_decoy(md, rng, book):
    lines = md.split("\n")
    decoy = ["<table>", "第四节 假节表", "<tr><td>单元格</td></tr>", "</table>", ""]
    target = None
    for i, ln in enumerate(lines):
        if ln.startswith("## 第三节 "):
            target = i
            break
    if target is None:
        target = len(lines) // 2
    lines[target:target] = decoy
    return "\n".join(lines), {"check": "full", "absent": ["假节"]}


def corr_reorder_sections(md, rng, book):
    lines = md.split("\n")
    chs = book["chapters"]
    ci = rng.randrange(len(chs))
    secs = chs[ci]["sections"]
    if len(secs) < 2:
        return md, {"check": "full", "absent": []}
    si = rng.randrange(len(secs) - 1)
    s1, s2 = secs[si]["title"], secs[si + 1]["title"]
    i1 = next(i for i, ln in enumerate(lines) if ln.strip() == f"## {s1}")
    i2 = next(i for i, ln in enumerate(lines) if ln.strip() == f"## {s2}")
    # 块边界 = 下一个「节/章」标题（知识点/子项标题不是块边界）
    i3 = next((i for i in range(i2 + 1, len(lines))
               if re.match(r'^## 第[一二三四五六七八九十]+[章节]', lines[i])), len(lines))
    block1 = lines[i1:i2]
    block2 = lines[i2:i3]
    lines[i1:i3] = block2 + block1
    return "\n".join(lines), {"check": "full", "order": True, "absent": []}


def _retitle_kps(md, book, fn):
    """把每个「知识点N 名称」标题按 fn 改名（同步改写 md 与期望 book）。"""
    for ci, ch in enumerate(book["chapters"], 1):
        for si, sec in enumerate(ch["sections"], 1):
            for ki, kp in enumerate(sec["kps"], 1):
                old = kp["title"]
                new = fn(ci, si, ki, old)
                if new != old:
                    md = md.replace(f"## {old}", f"## {new}", 1)
                    kp["title"] = new
    return md


def corr_unnumbered_kp_containers(md, rng, book):
    """「知识点一 要点…」→「知识点 要点…」：无编号容器（察举制度同款）。"""
    md = _retitle_kps(md, book, lambda ci, si, ki, t: re.sub(r'^知识点[一二三四五六七八九十]+ ', '知识点 ', t))
    return md, {"check": "full", "absent": []}


def corr_exam_point_style(md, rng, book):
    """「知识点一 要点…」→「考点 N 要点…」：《核心考案》同款容器族。"""
    md = _retitle_kps(md, book, lambda ci, si, ki, t: re.sub(r'^知识点[一二三四五六七八九十]+ ', f'考点 {ki} ', t))
    return md, {"check": "full", "absent": []}


def corr_mixed_big(md, rng, book):
    lines = md.split("\n")
    chs = book["chapters"]
    ci = rng.randrange(len(chs))
    secs = chs[ci]["sections"]
    si = rng.randrange(len(secs))
    deleted = {(ci, si)}
    skip = _sec_span(lines, secs[si]["title"])
    # 丢标记（跳过被删小节的范围）
    lines, _ = _strip_marks(lines, SEC_RE, rng, 0.5, skip_range=skip)
    lines, _ = _strip_marks(lines, KP_RE, rng, 0.5, skip_range=skip)
    # 删除小节标题行（先定位再删）
    target = f"## {secs[si]['title']}"
    lines = [ln for ln in lines if ln.strip() != target]
    # 页眉 + 目录 + 重复行
    for _ in range(4):
        pos = rng.randrange(len(lines))
        lines[pos:pos] = ["", f"## {HEADER}", ""]
    lines = _toc_lines(book, True, False) + lines
    pos = rng.randrange(len(lines))
    lines[pos:pos] = ["", "第七节 导图残留小节", "", "知识点七 导图残留要点", ""]
    return "\n".join(lines), {"check": "deleted", "deleted": deleted, "absent": [HEADER, "导图残留"]}


MODES = [
    ("clean", corr_clean),
    ("lost_section_marks", corr_lost_section_marks),
    ("lost_kp_marks", corr_lost_kp_marks),
    ("lost_chapter_marks", corr_lost_chapter_marks),
    ("lost_ch_kp_marks", corr_lost_all_marks),
    ("lost_everything", corr_lost_everything),
    ("delete_section_title", corr_delete_middle_section),
    ("delete_first_section_title", corr_delete_first_section),
    ("delete_two_sections", corr_delete_two_consecutive),
    ("running_header_x4", corr_running_header),
    ("toc_with_pages", corr_toc_with_pages),
    ("toc_no_pages_consec", corr_toc_no_pages_consecutive),
    ("toc_no_pages_spaced", corr_toc_no_pages_spaced),
    ("dup_plain_runs", corr_dup_plain_runs),
    ("dup_heading_lines", corr_dup_heading_lines),
    ("title_with_punct", corr_title_with_punct),
    ("spaced_numbers", corr_spaced_numbers),
    ("mermaid_decoy", corr_mermaid_decoy),
    ("table_decoy", corr_table_decoy),
    ("reorder_sections", corr_reorder_sections),
    ("mixed_big", corr_mixed_big),
    ("unnumbered_kp_containers", corr_unnumbered_kp_containers),
    ("exam_point_style", corr_exam_point_style),
]


# =============================================================================
# 恢复链 + 校验器
# =============================================================================

def run_chain(md, hint=HINT):
    md1, rep = normalize_markdown_outline(md, doc_hint=hint)
    md2, rep2 = normalize_markdown_outline(md1, doc_hint=hint)
    idem_errs = []
    if md2 != md1:
        idem_errs.append("normalize 非幂等")
    if rep2.get("recovered"):
        idem_errs.append(f"二次 normalize 又恢复: {rep2['recovered'][:2]}")
    nodes = parse_md_to_nodes(md1)
    nodes = adjust_standalone_levels(nodes)
    nodes = apply_hierarchy_repair(nodes)
    roots = build_tree(nodes)
    audit = audit_tree_skeleton(roots)
    try:
        fix_topic_placement(roots, log=noop)
    except Exception:
        pass
    wrapper = {"title": "book", "children": roots}
    structure_repair.deterministic_audit(wrapper)
    cleanup_tree_structure(wrapper)
    return roots, rep, audit, idem_errs


def _direct(children, title):
    for c in children or []:
        if (c.get("title") or "").strip() == title:
            return c
    return None


def _find(roots, title):
    for c in roots or []:
        if (c.get("title") or "").strip() == title:
            return c
        r = _find(c.get("children") or [], title)
        if r:
            return r
    return None


def _parent_of(roots, title, parent=None):
    for c in roots or []:
        if (c.get("title") or "").strip() == title:
            return parent
        r = _parent_of(c.get("children") or [], title, c)
        if r is not None:
            return r
    return None


def _count_title(roots, title):
    n = 0
    for c in roots or []:
        if (c.get("title") or "").strip() == title:
            n += 1
        n += _count_title(c.get("children") or [], title)
    return n


def _collect_markers(roots):
    found = set()
    def walk(ns):
        for n in ns or []:
            for field in ("title", "content"):
                s = n.get(field) or ""
                found.update(re.findall(r'@M[\d\-]+@', s))
            walk(n.get("children") or [])
    walk(roots)
    return found


def _all_titles(roots):
    out = []
    def walk(ns):
        for n in ns or []:
            out.append((n.get("title") or "").strip())
            walk(n.get("children") or [])
    walk(roots)
    return out


def _check_markers(roots, book, errs):
    missing = content_markers(book) - _collect_markers(roots)
    if missing:
        errs.append(f"正文丢失 {sorted(missing)[:3]}")


def _check_absent(roots, absent, errs):
    for kw in absent:
        hit = [t for t in _all_titles(roots) if kw in t]
        if hit:
            errs.append(f"不应存在的节点: {hit[:2]}")


def verify_full(roots, book, spec, errs, find_ch=None):
    find_ch = find_ch or (lambda t: _direct(roots, t))
    for ci, ch in enumerate(book["chapters"], 1):
        if _count_title(roots, ch["title"]) != 1 or find_ch(ch["title"]) is None:
            errs.append(f"章缺失/重复: {ch['title']}")
            continue
        chn = find_ch(ch["title"])
        for si, sec in enumerate(ch["sections"], 1):
            if _count_title(roots, sec["title"]) != 1 or _direct(chn.get("children"), sec["title"]) is None:
                errs.append(f"节缺失/层级错: {ch['title']} > {sec['title']}")
                continue
            secn = _direct(chn["children"], sec["title"])
            for ki, kp in enumerate(sec["kps"], 1):
                if _count_title(roots, kp["title"]) != 1 or _direct(secn.get("children"), kp["title"]) is None:
                    errs.append(f"知识点缺失/层级错: {sec['title']} > {kp['title']}")
                    continue
                if spec.get("lost_subs"):
                    continue
                kpn = _direct(secn["children"], kp["title"])
                for sub in kp["subs"]:
                    if _count_title(roots, sub) != 1 or _direct(kpn.get("children"), sub) is None:
                        errs.append(f"子项缺失/层级错: {kp['title']} > {sub}")
    if not spec.get("lost_subs"):
        if _count_title(roots, PLACEHOLDER_TITLE) != 0:
            errs.append("出现不该有的占位节点")


def verify_deleted(roots, book, spec, errs, find_ch=None):
    find_ch = find_ch or (lambda t: _direct(roots, t))
    deleted = spec["deleted"]   # 0-based {(chapter_idx, section_idx)}
    for ci, ch in enumerate(book["chapters"]):
        chn = find_ch(ch["title"])
        if chn is None or _count_title(roots, ch["title"]) != 1:
            errs.append(f"章缺失/重复: {ch['title']}")
            continue
        for si, sec in enumerate(ch["sections"]):
            if (ci, si) in deleted:
                for kp in sec["kps"]:
                    kpn = _find(roots, kp["title"])
                    if kpn is None or _count_title(roots, kp["title"]) != 1:
                        errs.append(f"被删节的条目丢失: {kp['title']}")
                        continue
                    parent = _parent_of(roots, kp["title"])
                    if parent is None or (parent.get("title") or "") != PLACEHOLDER_TITLE:
                        errs.append(f"被删节条目未落入占位: {kp['title']} 父={parent and parent.get('title')}")
                    elif _direct(chn.get("children"), PLACEHOLDER_TITLE) is None:
                        errs.append(f"占位不是章的直接子节点: {ch['title']} > {kp['title']}")
            else:
                if _direct(chn.get("children"), sec["title"]) is None:
                    errs.append(f"正常节丢失/层级错: {sec['title']}")
                    continue
                secn = _direct(chn["children"], sec["title"])
                for kp in sec["kps"]:
                    if _direct(secn.get("children"), kp["title"]) is None:
                        errs.append(f"知识点层级错: {sec['title']} > {kp['title']}")
    if _count_title(roots, PLACEHOLDER_TITLE) != len(deleted):
        errs.append(f"占位数量不符: {_count_title(roots, PLACEHOLDER_TITLE)} != {len(deleted)}")


def verify_order(roots, book, errs):
    for ch in book["chapters"]:
        chn = _direct(roots, ch["title"])
        if chn is None:
            continue
        titles = [c.get("title") for c in chn.get("children") or []]
        pos = []
        for sec in ch["sections"]:
            pos.append(titles.index(sec["title"]) if sec["title"] in titles else 10 ** 6)
        if pos != sorted(pos):
            errs.append(f"节顺序未恢复: {ch['title']} {pos}")


def verify_common(roots, res):
    errs = []
    errs.extend(res["idem_errs"])
    vs = check_skeleton(roots)
    errors = [v for v in vs if v.get("severity") == "error"]
    if errors:
        errs.append(f"错误级违规未清零: {errors[:2]}")
    return errs


def check_book(md, book, spec):
    try:
        roots, rep, audit, idem_errs = run_chain(md)
    except Exception as e:
        return [f"恢复链异常: {type(e).__name__}: {e}"]
    errs = verify_common(roots, {"idem_errs": idem_errs})
    if spec.get("check") == "deleted":
        verify_deleted(roots, book, spec, errs)
    else:
        verify_full(roots, book, spec, errs)
    if spec.get("order"):
        verify_order(roots, book, errs)
    _check_markers(roots, book, errs)
    _check_absent(roots, spec.get("absent", []), errs)
    return errs


# =============================================================================
# 主驱动
# =============================================================================

def run_matrix():
    print("════ A. 破坏矩阵（每模式 = SEEDS 本随机书）════")
    ok = fail = 0
    for name, fn in MODES:
        mode_fail = []
        for seed in range(SEEDS):
            rng = random.Random(seed * 7919 + sum(ord(c) for c in name) % 100003)
            book = make_book(rng)
            md = render(book)
            corrupted, spec = fn(md, rng, book)
            errs = check_book(corrupted, book, spec)
            if errs:
                mode_fail.append((seed, errs[:3]))
        if mode_fail:
            fail += 1
            print(f"  FAIL {name}: {SEEDS - len(mode_fail)}/{SEEDS} 通过")
            for seed, errs in mode_fail[:3]:
                print(f"       seed={seed}: {'; '.join(str(e) for e in errs)}")
        else:
            ok += 1
            print(f"  PASS {name}: {SEEDS}/{SEEDS}")
    return ok, fail


def run_real_corpus():
    print("════ B. 全语料回归 ════")
    import glob
    ok = fail = 0
    files = [f for f in sorted(glob.glob("data/_temp_uploads/*/markdown/*.md"))]
    for f in files:
        text = open(f, encoding="utf-8").read()
        try:
            roots, rep, audit, idem_errs = run_chain(text, hint=os.path.basename(f))
        except Exception as e:
            print(f"  FAIL {os.path.basename(f)}: 崩溃 {e}")
            fail += 1
            continue
        errs = list(idem_errs)
        errors = [v for v in check_skeleton(roots) if v.get("severity") == "error"]
        if errors:
            errs.append(f"错误级违规: {errors[:2]}")
        if errs:
            print(f"  FAIL {os.path.basename(f)}: {errs[:3]}")
            fail += 1
        else:
            ok += 1
    print(f"  语料文件: {ok} 通过 / {fail} 失败（共 {len(files)}）")

    # 已知案例点对点
    f1 = "data/_temp_uploads/1f7ec904/markdown/01_000_02.333大纲知识清单中教第三-五章.md"
    if os.path.exists(f1):
        roots, _, _, _ = run_chain(open(f1, encoding="utf-8").read(), hint=os.path.basename(f1))
        chain_ok = True
        def chain(roots, kw):
            out = []
            def walk(ns, path):
                for n in ns:
                    p = path + [n.get("title") or ""]
                    if kw in (n.get("title") or ""):
                        out.append(p)
                    walk(n.get("children") or [], p)
            walk(roots, [])
            return out
        yz = chain(roots, "颜之推的教育思想")
        hn = chain(roots, "韩愈的教育思想")
        dzy = chain(roots, "董仲舒的教育实践")
        wc = chain(roots, "王充的教育思想")
        chain_ok = (yz and yz[0][-2] == "第四节 魏晋南北朝与隋唐时期的教育思想"
                    and hn and hn[0][-2] == "第四节 魏晋南北朝与隋唐时期的教育思想"
                    and dzy and dzy[0][-2] == "第四节 汉代的教育思想"
                    and wc and wc[0][-2] == "第四节 汉代的教育思想")
        print(("  PASS" if chain_ok else "  FAIL") + " 已知案例：颜之推/韩愈/董仲舒/王充 父链")
        if not chain_ok:
            fail += 1
        else:
            ok += 1

    # 已知案例 2（容器族）：察举制度容器层级（c3986895，用户当天重跑数据）
    f2 = "data/_temp_uploads/c3986895/markdown/01_000_02.333大纲知识清单中教第三-五章.md"
    if os.path.exists(f2):
        roots, _, _, _ = run_chain(open(f2, encoding="utf-8").read(), hint=os.path.basename(f2))
        sec3 = _find(roots, "第三节 汉代的选士制度")
        kp = _find(roots, "知识点 察举制度")
        ok2 = bool(sec3 and kp
                   and _direct(sec3.get("children"), "知识点 察举制度") is not None
                   and _direct(kp.get("children"), "（一）察举制度的内容") is not None
                   and _direct(kp.get("children"), "（二）察举制度的评价") is not None)
        sec1 = _find(roots, "第一节 汉代的文教政策")
        kp1 = _find(roots, "知识点 汉代的文教政策")
        ok2 = ok2 and bool(sec1 and kp1
                           and _direct(sec1.get("children"), "知识点 汉代的文教政策") is not None
                           and _direct(kp1.get("children"), "（一）汉初文教政策") is not None)
        print(("  PASS" if ok2 else "  FAIL") + " 已知案例2：察举制度/文教政策容器层级（c3986895）")
        ok += 1 if ok2 else 0
        fail += 0 if ok2 else 1

    # 已知案例 3：连续「考点」平级（31825f95《核心考案》），不得链式互套
    f3 = "data/_temp_uploads/31825f95/markdown/01_000_26徐涛《核心考案》_前100页.md"
    if os.path.exists(f3):
        roots, _, _, _ = run_chain(open(f3, encoding="utf-8").read(), hint=os.path.basename(f3))
        p24 = _parent_of(roots, "考点 24 必然和偶然（唯物辩证法第四对基本范畴）（单选题 $^{[12]}$ ）")
        p25 = _parent_of(roots, "考点 25 可能和现实（唯物辩证法第五对基本范畴）（单选题 $^{[13]}$ ）")
        ok3 = (p24 is not None and p25 is not None and p24 is p25)
        print(("  PASS" if ok3 else "  FAIL") + " 已知案例3：考点 24/25 平级兄弟（31825f95）")
        ok += 1 if ok3 else 0
        fail += 0 if ok3 else 1

    return ok, fail


def run_fuzz():
    print("════ C. 模糊输入鲁棒性 ════")
    rng = random.Random(42)
    pool = "第一二三四五六七八九十章节知识点（）0123456789。， \n\t#*|<>\u3000xY"
    cases = ["", "\n\n\n", "###", "## ", "第一章", "第 一 章", "第一节", "知识点一",
             "#### \n\n#### \n"]
    cases += ["".join(rng.choice(pool) for _ in range(rng.randint(50, 3000))) for _ in range(30)]
    bad = []
    for i, text in enumerate(cases):
        try:
            md1, _ = normalize_markdown_outline(text, doc_hint="fuzz")
            run_chain(text)
        except Exception as e:
            bad.append((i, f"{type(e).__name__}: {e}"))
    if bad:
        for i, e in bad[:5]:
            print(f"  FAIL case#{i}: {e}")
        return 0, 1
    print(f"  PASS 鲁棒性：{len(cases)} 组输入无异常")
    return 1, 0


def run_full_pipeline():
    """D. 一整套解析链路：server._build_trees_from_md → _fix_hierarchy_and_merge
    → _prepare_final_json（即思维导图实际消费的最终树）。全程无 LLM 调用，
    验证"输入 md → 最终显示树"的层级零错误 + 耗时。"""
    print("════ D. 一整套解析链路（server 路径，最终树 = 导图消费的 JSON）════")
    import contextlib
    import glob
    import io
    import tempfile
    import time
    import statistics
    import server

    ok = fail = 0
    chain_times = []
    front_times = []

    def run_server_chain(md_path, unit_meta=None):
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):   # 压掉 server 的进度输出，保留测试信号
                tfs = server._build_trees_from_md([md_path], td, unit_meta or {}, api_key='')
                merged = server._fix_hierarchy_and_merge(tfs, td)
                final = server._prepare_final_json(merged)
            return tfs, final

    # D1. 全语料：每份 md 独立走完整链路，最终树零错误级违规
    files = sorted(glob.glob("data/_temp_uploads/*/markdown/*.md"))
    d1_ok = d1_fail = 0
    for f in files:
        t0 = time.perf_counter()
        try:
            unit_meta = {os.path.splitext(os.path.basename(f))[0]: {
                "unit_title": os.path.basename(f), "source_title": os.path.basename(f)}}
            _tfs, final = run_server_chain(f, unit_meta)
            errs = [v for v in check_skeleton(final.get("children") or [])
                    if v.get("severity") == "error"]
            if errs:
                d1_fail += 1
                print(f"  FAIL {os.path.basename(f)[:44]}: {errs[:2]}")
            else:
                d1_ok += 1
            chain_times.append(time.perf_counter() - t0)
        except Exception as e:
            d1_fail += 1
            print(f"  FAIL {os.path.basename(f)[:44]}: 崩溃 {type(e).__name__}: {e}")
    print(f"  语料全链路: {d1_ok} 通过 / {d1_fail} 失败（共 {len(files)}）")
    ok += d1_ok
    fail += d1_fail

    # D2. 已知案例：最终树里核对父链
    f1 = "data/_temp_uploads/1f7ec904/markdown/01_000_02.333大纲知识清单中教第三-五章.md"
    if os.path.exists(f1):
        try:
            unit_meta = {os.path.splitext(os.path.basename(f1))[0]: {
                "unit_title": os.path.basename(f1), "source_title": os.path.basename(f1)}}
            _tfs, final = run_server_chain(f1, unit_meta)
            roots = final.get("children") or []

            def chain_of(kw):
                out = []
                def walk(ns, path):
                    for n in ns:
                        p = path + [n.get("title") or ""]
                        if kw in (n.get("title") or ""):
                            out.append(p)
                        walk(n.get("children") or [], p)
                walk(roots, [])
                return out

            yz, hn = chain_of("颜之推的教育思想"), chain_of("韩愈的教育思想")
            dz, wc = chain_of("董仲舒的教育实践"), chain_of("王充的教育思想")
            good = (yz and yz[0][-2] == "第四节 魏晋南北朝与隋唐时期的教育思想"
                    and hn and hn[0][-2] == "第四节 魏晋南北朝与隋唐时期的教育思想"
                    and dz and dz[0][-2] == "第四节 汉代的教育思想"
                    and wc and wc[0][-2] == "第四节 汉代的教育思想")
            errs = [v for v in check_skeleton(roots) if v.get("severity") == "error"]
            if good and not errs:
                ok += 1
                print("  PASS 已知案例（最终树）：颜之推/韩愈/董仲舒/王充 父链")
            else:
                fail += 1
                print(f"  FAIL 已知案例（最终树）: {yz} {hn} {dz} {wc} errs={errs[:2]}")
        except Exception as e:
            fail += 1
            print(f"  FAIL 已知案例（最终树）: 崩溃 {type(e).__name__}: {e}")

    # D3. 合成破坏书走完整链路（含包装/合并/最终 JSON）
    d3_ok = d3_fail = 0
    picks = [("lost_section_marks", corr_lost_section_marks),
             ("delete_middle", corr_delete_middle_section),
             ("delete_first", corr_delete_first_section),
             ("mixed_big", corr_mixed_big)]
    with tempfile.TemporaryDirectory() as td:
        for name, fn in picks:
            for seed in range(6):
                rng = random.Random(seed * 104729 + sum(ord(c) for c in name) % 100003)
                book = make_book(rng)
                md = render(book)
                corrupted, spec = fn(md, rng, book)
                path = os.path.join(td, f"{name}_{seed}.md")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(corrupted)
                try:
                    unit_meta = {f"{name}_{seed}": {"unit_title": f"{name}_{seed}", "source_title": f"{name}_{seed}"}}
                    _tfs, final = run_server_chain(path, unit_meta)
                    roots = final.get("children") or []
                    errs = []
                    find_ch = lambda t: _find(roots, t)
                    if spec.get("check") == "deleted":
                        verify_deleted(roots, book, spec, errs, find_ch=find_ch)
                    else:
                        verify_full(roots, book, spec, errs, find_ch=find_ch)
                    errs = [e for e in errs]
                    sk_errs = [v for v in check_skeleton(roots) if v.get("severity") == "error"]
                    if sk_errs:
                        errs.append(f"错误级违规: {sk_errs[:2]}")
                    _check_markers(roots, book, errs)
                    if errs:
                        d3_fail += 1
                        print(f"  FAIL {name}#{seed}: {errs[:2]}")
                    else:
                        d3_ok += 1
                except Exception as e:
                    d3_fail += 1
                    print(f"  FAIL {name}#{seed}: 崩溃 {type(e).__name__}: {e}")
    print(f"  合成书全链路: {d3_ok} 通过 / {d3_fail} 失败（{len(picks) * 6} 本）")
    ok += d3_ok
    fail += d3_fail

    # D4. 耗时（前置层单独计量 + 全链路总耗时；无 AI，纯解析）
    for f in files[:min(len(files), 12)]:
        try:
            text = open(f, encoding="utf-8").read()
            t0 = time.perf_counter()
            md1, _rep = normalize_markdown_outline(text, doc_hint=os.path.basename(f))
            nodes = parse_md_to_nodes(md1)
            nodes = adjust_standalone_levels(nodes)
            nodes = apply_hierarchy_repair(nodes)
            roots = build_tree(nodes)
            audit_tree_skeleton(roots)
            front_times.append(time.perf_counter() - t0)
        except Exception:
            pass
    if chain_times and front_times:
        med_chain = statistics.median(chain_times) * 1000
        max_chain = max(chain_times) * 1000
        med_front = statistics.median(front_times) * 1000
        print(f"  耗时：全链路（纯解析，无 AI）中位 {med_chain:.0f}ms / 最大 {max_chain:.0f}ms"
              f"（{len(chain_times)} 份）；其中前置层中位 {med_front:.0f}ms")
        if med_front > 150:
            print(f"  FAIL 前置层耗时超预算: 中位 {med_front:.0f}ms > 150ms")
            fail += 1
        else:
            ok += 1
    return ok, fail


if __name__ == "__main__":
    ok_a, fail_a = run_matrix()
    ok_b, fail_b = run_real_corpus()
    ok_c, fail_c = run_fuzz()
    ok_d, fail_d = run_full_pipeline()
    total_fail = fail_a + fail_b + fail_c + fail_d
    print(f"\n{'=' * 52}\n模式通过: {ok_a}  失败项: {total_fail}")
    sys.exit(1 if total_fail else 0)
