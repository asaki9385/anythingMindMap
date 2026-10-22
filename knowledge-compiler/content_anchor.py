#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原文锚定层 —— 把原文段落按骨架的区间声明挂进节点，**原文一字不改**。

设计约定
--------
1. **保真是硬性不变式**：无论骨架怎么生成、校验是否通过，任何一段原文
   都不得丢失。`assert_no_content_loss()` 会核验这一点。
2. 段落是**稳定且可逆**的划分：`split_paragraphs(text)` 切出的段落按序
   join 回去即原文（只忽略纯空白行的呈现差异），因此可以靠序号索引精确对齐。
3. 骨架节点用 `src_range = [start, end)` 声明它占哪些段落；锚定时把这些段落
   原样拼进节点 `content`。**不用 AI 改写 content**——这正是"只出骨架、原文
   锚定"的关键。

为什么这么做
------------
原 llm_structurer 是一步到位：让模型同时产出标题与 content。实测后果：
每片只喂 6000/8000 字（丢字）、max_tokens 2000 易截断成非法 JSON、
一旦截断就退化成"仅留前 10 段×500 字"的兜底树（再次丢字），且模型改写过的
content 不再是原文。对要拿来背诵的备考资料，改写是硬伤。
"""
from __future__ import annotations

import re
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# ────────────────────────────────────────────
# 段落模型
# ────────────────────────────────────────────

_BLANK_LINE = re.compile(r'\n\s*\n+')


def split_paragraphs(text: str) -> list[str]:
    """按空行切段，返回非空段落列表（顺序即原文顺序）。"""
    parts = _BLANK_LINE.split((text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def paragraphs_text(paras: list[str]) -> str:
    return "\n\n".join(paras)


# ────────────────────────────────────────────
# 区间校验
# ────────────────────────────────────────────

def collect_all_ranges(skeleton: dict) -> dict:
    """收集**每个**节点声明的 [start, end)，键为节点 id。

    不限叶子：父节点可能自带引言段（如"章节总起"），此时它的区间不与子节点
    重叠。是否重复取决于分配策略，见 assign_paragraphs。
    """
    bag = {}

    def _walk(node):
        r = node.get("src_range")
        if isinstance(r, (list, tuple)) and len(r) == 2:
            try:
                bag[id(node)] = (int(r[0]), int(r[1]))
            except (TypeError, ValueError, OverflowError):
                pass
        for c in node.get("children") or []:
            _walk(c)

    _walk(skeleton)
    return bag


def assign_paragraphs(skeleton: dict, ranges: dict,
                      n_paras: int) -> dict:
    """每个段落归给「声明了它、且层级最深」的节点。

    这样一来：
      · 父节点若声明了整段子树的区间（结构壳），会被更深的子节点自然覆盖，
        正文不会在父子间重复；
      · 父节点若只声明自己的引言段（与子区间不重叠），则引言归父节点。
    同深度冲突（两个兄弟同时声明同一段）按 DFS 先到先得，并记入 conflicts。
    """
    claims = {}          # para_idx -> (depth, dfs_order, node)
    order_counter = [0]
    conflicts = [0]

    def _walk(node, depth):
        r = ranges.get(id(node))
        dfs = order_counter[0]
        order_counter[0] += 1
        if r is not None:
            a, b = r
            a = max(0, min(n_paras, a))
            b = max(0, min(n_paras, b))
            for p in range(a, b):
                prev = claims.get(p)
                if prev is None:
                    claims[p] = (depth, dfs, node)
                else:
                    # 更深者胜；同深则保留先到者
                    if depth > prev[0]:
                        claims[p] = (depth, dfs, node)
                    else:
                        conflicts[0] += 1
        for c in node.get("children") or []:
            _walk(c, depth + 1)

    _walk(skeleton, 0)
    return {"claims": claims, "conflicts": conflicts[0]}


def validate_ranges(ranges: list[tuple[int, int]], n_paras: int) -> tuple[bool, str]:
    """校验区间是否合法（允许父子重叠，故只查越界与整体覆盖）。

    完全不覆盖/大量空洞会退回按序分配；是否"划分"不作硬性要求，
    因为父子重叠是常见写法，由 assign_paragraphs 的"最深者得"来消解。
    """
    if not ranges:
        return False, "无区间声明"
    for a, b in ranges:
        if not (0 <= a < b <= n_paras):
            return False, f"区间越界 [{a},{b})（段落共 {n_paras}）"
    covered = set()
    for a, b in ranges:
        covered.update(range(a, b))
    if not covered:
        return False, "区间未覆盖任何段落"
    hole = n_paras - len(covered)
    if hole > n_paras * 0.35:
        return False, f"空洞过多：{hole}/{n_paras} 段无人认领"
    return True, "ok"


# ────────────────────────────────────────────
# 锚定
# ────────────────────────────────────────────

def _new_node(title: str, content: str, level: int, src_index: int) -> dict:
    """与 tree_builder.parse_md_to_nodes 的节点形态保持一致，便于下游复用。"""
    return {
        "title": title,
        "level": level,
        "content": content,
        "children": [],
        "captions": [],
        "src_index": src_index,
    }


def _count_leaves(skeleton: dict) -> int:
    n = 0

    def _walk(node):
        nonlocal n
        kids = node.get("children") or []
        if not kids:
            n += 1
        for c in kids:
            _walk(c)

    _walk(skeleton)
    return n


def anchor_skeleton(skeleton: dict, paragraphs: list[str],
                    allow_positional_fallback: bool = True) -> tuple[dict, dict]:
    """把原文段落挂进骨架。→ (tree_root, report)

    tree_root 形态与 tree_builder 产出一致：{title, level, content, children, ...}。

    分配策略：每段归给声明了它且层级最深的节点（见 assign_paragraphs）。
    区间整体不可信时 → 按叶子占比顺序分配（确定性、不丢段落），
    绝不因为骨架可疑就把原文丢掉。
    """
    n = len(paragraphs)
    report = {"anchored_by": "range", "nodes": 0, "paragraphs": n,
              "lost_paragraphs": 0, "conflicts": 0, "detail": ""}

    root = _new_node(skeleton.get("title") or "未命名", "", 1, 0)
    report["nodes"] = 1 + _count_leaves(skeleton)

    if n == 0:
        root["children"] = _skeleton_to_nodes(skeleton, {}, 2, None)
        return root, report

    ranges = collect_all_ranges(skeleton)
    ok, detail = validate_ranges(list(ranges.values()), n)

    if not ok:
        if not allow_positional_fallback:
            raise ValueError(f"骨架区间不可信：{detail}")
        report["anchored_by"] = "positional"
        report["detail"] = detail
        ranges = _positional_ranges(skeleton, n)

    assigned = assign_paragraphs(skeleton, ranges, n)
    report["conflicts"] = assigned["conflicts"]

    # 按节点聚合段落，再写进 content
    paras_by_node = {}
    for p, (_d, _o, node) in assigned["claims"].items():
        paras_by_node.setdefault(id(node), []).append(p)
    content_by_node = {}
    for nid, idxs in paras_by_node.items():
        idxs.sort()
        content_by_node[nid] = paragraphs_text([paragraphs[i] for i in idxs])

    root["children"] = _skeleton_to_nodes(skeleton, {}, 2, None,
                                         content_by_node=content_by_node)
    report["lost_paragraphs"] = n - len(assigned["claims"])
    return root, report


def _positional_ranges(skeleton: dict, n_paras: int) -> dict:
    """骨架区间不可信时的兜底：按叶子节点数等比切分段落序列。

    确定性、无损、绝不抛异常——宁可挂得不够准，也不丢原文。
    """
    bag = {}
    leaves = []

    def _collect(node):
        if not (node.get("children") or []):
            leaves.append(node)
        for c in node.get("children") or []:
            _collect(c)

    _collect(skeleton)
    if not leaves:
        return bag
    per = n_paras // len(leaves)
    extra = n_paras % len(leaves)
    pos = 0
    for i, leaf in enumerate(leaves):
        take = per + (1 if i < extra else 0)
        bag[id(leaf)] = (pos, pos + take)
        pos += take
    return bag


def _count_anchored(range_by_node: dict, n_paras: int) -> int:
    covered = set()
    for a, b in range_by_node.values():
        covered.update(range(a, b))
    return len(covered)


def _skeleton_to_nodes(skeleton: dict, range_by_node: dict, level: int,
                      paragraphs: list[str] | None = None,
                      content_by_node: dict | None = None) -> list[dict]:
    """骨架 → 节点列表；正文由 content_by_node 提供（每节点聚合好的段落文本）。"""
    out = []
    for i, child in enumerate(skeleton.get("children") or []):
        if content_by_node is not None:
            content = content_by_node.get(id(child), "")
        elif paragraphs is not None:
            a, b = range_by_node.get(id(child), (None, None))
            content = paragraphs_text(paragraphs[a:b]) if a is not None else ""
        else:
            content = ""
        node = _new_node(child.get("title") or f"小节 {i + 1}", content, level, i)
        node["children"] = _skeleton_to_nodes(child, range_by_node, level + 1,
                                              paragraphs, content_by_node)
        out.append(node)
    return out


# ────────────────────────────────────────────
# 不变式
# ────────────────────────────────────────────

def iter_nodes(node: dict):
    yield node
    for c in node.get("children") or []:
        yield from iter_nodes(c)


def assert_no_content_loss(root: dict, paragraphs: list[str]) -> dict:
    """核验：每个原文段落都出现在某个节点的 content 里。

    → {"lost": [...未出现的段落下标], "kept": 已保留段落数}
    """
    blob = "\n".join((n.get("content") or "") for n in iter_nodes(root))
    lost = []
    kept = 0
    for i, p in enumerate(paragraphs):
        probe = p[:40] if len(p) > 40 else p
        if probe and probe in blob:
            kept += 1
        else:
            lost.append(i)
    return {"lost": lost, "kept": kept, "total": len(paragraphs)}


# ────────────────────────────────────────────
# 兜底：无论如何都不许丢正文
# ────────────────────────────────────────────

def paragraphs_to_tree(paragraphs: list[str], title: str = "未命名",
                       chunk_size: int = 6) -> dict:
    """无骨架时的保底产物：把段落按固定块大小分组，原文全保留。

    用于 AI 不可用/骨架被拒时，保证"无标题全文丢弃"不再发生。
    """
    root = _new_node(title, "", 1, 0)
    if not paragraphs:
        return root
    children = []
    for start in range(0, len(paragraphs), chunk_size):
        block = paragraphs[start:start + chunk_size]
        head = block[0].replace('\n', ' ').strip()[:24]
        if len(block[0].strip()) > 24:
            head += "…"
        node = _new_node(head or f"段落 {start + 1}", paragraphs_text(block), 2, len(children))
        children.append(node)
    root["children"] = children
    return root
