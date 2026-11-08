#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 骨架质量闭环：严格契约、长文窗口、确定性降级、质量分。

与 skeleton_builder 的分工：
  · skeleton_builder 负责“问模型要骨架”；
  · 本模块负责“判断骨架是否合格、怎么拼、降级成什么”。

硬约束（任何路径都不许破）：
  · 所有段落必须恰好被覆盖一次；
  · 叶区间不重叠、按 DFS 顺序单调；
  · 深度 ≤ 6；
  · 标题尽量接地（标题二元组能在所属原文里找到，或带原文编号）。
"""
from __future__ import annotations

import re
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

MAX_DEPTH = 6
MAX_PARAS_PER_CALL = 120
WINDOW_OVERLAP = 20
QUALITY_FLOOR = 0.6


# ────────────────────────────────────────────
# 归一化 / 工具
# ────────────────────────────────────────────

def _norm(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^0-9a-z一-鿿]+", "", s)


def _shingles(norm: str, n: int = 2) -> set[str]:
    if len(norm) <= n:
        return {norm} if norm else set()
    return {norm[i:i + n] for i in range(len(norm) - n + 1)}


def _is_heading_like(text: str) -> bool:
    s = (text or "").strip()
    if not s or "\n" in s or len(s) > 60:
        return False
    if s.startswith(("|", ">", "```", "![", "<")):
        return False
    return not re.search(r'[。；！？]\s*$', s)


def _walk(node: dict):
    yield node
    for ch in node.get("children") or []:
        yield from _walk(ch)


def _count_nodes(node: dict) -> int:
    return sum(1 for _ in _walk(node))


# ────────────────────────────────────────────
# 严格契约
# ────────────────────────────────────────────

def validate_skeleton(skeleton: dict, n_paras: int,
                      profile: dict | None = None) -> dict:
    """校验骨架是否满足契约。返回 errors/coverage/order_ok/ungrounded。"""
    errors = []
    leaf_ranges = []       # (start, end, title) —— 只用于重叠/顺序检查
    all_ranges = []        # 父区间也参与覆盖集合
    depth_max = [0]

    def walk(node: dict, depth: int, parent_range):
        depth_max[0] = max(depth_max[0], depth)
        if depth > MAX_DEPTH:
            errors.append({"code": "depth", "detail": f"深度 {depth} > {MAX_DEPTH}",
                           "title": node.get("title")})
        r = node.get("src_range")
        rng = None
        if r is not None:
            if not (isinstance(r, (list, tuple)) and len(r) == 2):
                errors.append({"code": "bad_range", "detail": f"区间格式 {r!r}",
                               "title": node.get("title")})
            else:
                try:
                    a, b = int(r[0]), int(r[1])
                except (TypeError, ValueError, OverflowError):
                    a, b = -1, -1
                if not (0 <= a < b <= n_paras):
                    errors.append({"code": "bad_range",
                                   "detail": f"区间越界 [{a},{b}) / {n_paras}",
                                   "title": node.get("title")})
                else:
                    rng = (a, b)
                    all_ranges.append(rng)
                    if parent_range and not (parent_range[0] <= a and b <= parent_range[1]):
                        errors.append({"code": "parent_range",
                                       "detail": f"子区间 {rng} 不在父区间 {parent_range} 内",
                                       "title": node.get("title")})
        children = node.get("children") or []
        if not children and rng is not None:
            leaf_ranges.append((rng[0], rng[1], node.get("title") or ""))
        for ch in children:
            walk(ch, depth + 1, rng or parent_range)

    walk(skeleton, 0, None)

    leaf_ranges.sort(key=lambda x: (x[0], x[1]))
    covered = set()
    last_end = 0
    for a, b, title in leaf_ranges:
        if a < last_end:
            errors.append({"code": "overlap", "detail": f"{a}-{b} 与前区间重叠",
                           "title": title})
        last_end = max(last_end, b)
    # 覆盖集合包含父区间（父节点可持有标题段/引言段），
    # 重叠/顺序只由叶区间决定，避免父子重叠被误报。
    for a, b in all_ranges:
        covered.update(range(a, b))
    uncovered = [i for i in range(n_paras) if i not in covered]
    if uncovered:
        errors.append({"code": "coverage_gap",
                       "detail": f"段落 {uncovered[0]}-{uncovered[-1] + 1} 无人认领"
                                 f"（共 {len(uncovered)} 段）"})

    order_ok = all(leaf_ranges[i][0] <= leaf_ranges[i + 1][0]
                   for i in range(len(leaf_ranges) - 1))
    if not order_ok:
        errors.append({"code": "out_of_order", "detail": "叶区间顺序与原文不一致"})

    coverage = round(len(covered) / n_paras, 4) if n_paras else 1.0
    return {
        "ok": not errors and coverage == 1.0,
        "errors": errors[:20],
        "coverage": coverage,
        "order_ok": order_ok,
        "leaf_count": len(leaf_ranges),
        "depth": depth_max[0],
        "overlaps": sum(1 for e in errors if e["code"] == "overlap"),
    }


# ────────────────────────────────────────────
# 标题接地
# ────────────────────────────────────────────

_NUM_PREFIX_RE = re.compile(
    r'^(第\s*[一二三四五六七八九十百千\d]+\s*[章节讲单元课专题篇编]|'
    r'[一二三四五六七八九十]+[、，,]|[（(【〔\[]\s*[一二三四五六七八九十\dIVXLCDMⅠ-Ⅻ]+\s*[）)】〕\]]|'
    r'\d+(?:[.．]\d+)*[.、．，,)]|[①-⑳㉑-㊿]|[A-Za-z][.、．)]|知识点|考点)')


def title_grounding(skeleton: dict, paragraphs: list[str]) -> dict:
    """标题是否能在所属区间原文中找到依据。返回 rate + 未接地标题。"""
    total = ok = 0
    ungrounded = []

    def walk(node: dict):
        nonlocal total, ok
        title = (node.get("title") or "").strip()
        r = node.get("src_range")
        children = node.get("children") or []
        if title and isinstance(r, (list, tuple)) and len(r) == 2 and not children:
            try:
                a, b = int(r[0]), int(r[1])
            except (TypeError, ValueError):
                a, b = 0, 0
            source = "\n".join(paragraphs[a:b])
            total += 1
            if _NUM_PREFIX_RE.match(title) or _grounded(title, source):
                ok += 1
            else:
                ungrounded.append(title[:60])
        for ch in children:
            walk(ch)

    walk(skeleton)
    return {
        "rate": round(ok / total, 4) if total else None,
        "grounded": ok,
        "total": total,
        "ungrounded": ungrounded[:20],
    }


def _grounded(title: str, source: str) -> bool:
    nt, ns = _norm(title), _norm(source)
    if not nt or not ns:
        return False
    if nt in ns:
        return True
    grams = _shingles(nt, 2)
    return bool(grams) and len(grams & _shingles(ns, 2)) / len(grams) >= 0.3


# ────────────────────────────────────────────
# 质量分
# ────────────────────────────────────────────

def quality_score(skeleton: dict, validation: dict,
                  grounding: dict | None = None) -> float:
    coverage = validation.get("coverage", 0.0)
    order = 1.0 if validation.get("order_ok") else 0.0
    grounded = (grounding or {}).get("rate")
    grounded = 0.5 if grounded is None else grounded
    depth = validation.get("depth", 0)
    depth_score = 1.0 if 2 <= depth <= 4 else (0.6 if depth in (1, 5) else 0.3)
    schema = 1.0 if validation.get("ok") else max(0.0, 1.0 - len(validation.get("errors") or []) / 5.0)
    return round(0.4 * coverage + 0.2 * order + 0.2 * grounded +
                 0.1 * depth_score + 0.1 * schema, 4)


# ────────────────────────────────────────────
# 长文窗口
# ────────────────────────────────────────────

def plan_windows(paragraphs: list[str], *, max_paras: int = MAX_PARAS_PER_CALL,
                 overlap: int = WINDOW_OVERLAP) -> list[dict]:
    n = len(paragraphs)
    if n <= max_paras:
        return [{"index": 0, "start": 0, "end": n, "own_start": 0, "own_end": n}]
    step = max(1, max_paras - overlap)
    windows = []
    start = 0
    idx = 0
    while start < n:
        end = min(n, start + max_paras)
        own_start = 0 if idx == 0 else start + overlap
        windows.append({"index": idx, "start": start, "end": end,
                        "own_start": own_start, "own_end": end})
        if end >= n:
            break
        start += step
        idx += 1
    return windows


def _shift_ranges(node: dict, offset: int, own_start: int, own_end: int) -> dict:
    """把窗口内相对区间转成全局区间，并裁掉左窗口已覆盖的重叠部分。"""
    out = {"title": node.get("title") or "", "children": []}
    r = node.get("src_range")
    if isinstance(r, (list, tuple)) and len(r) == 2:
        try:
            a, b = int(r[0]) + offset, int(r[1]) + offset
        except (TypeError, ValueError, OverflowError):
            a = b = None
        if a is not None:
            a = max(a, own_start)
            b = min(b, own_end)
            if a < b:
                out["src_range"] = [a, b]
    for ch in node.get("children") or []:
        c = _shift_ranges(ch, offset, own_start, own_end)
        if c.get("children") or c.get("src_range"):
            out["children"].append(c)
    return out


def stitch_skeletons(parts: list[dict | None], windows: list[dict]) -> dict | None:
    """多窗口骨架 → 全局骨架。重叠归左窗口；拼接后必须通过严格契约。"""
    if not parts or len(parts) != len(windows):
        return None
    children = []
    for part, win in zip(parts, windows):
        if not part:
            return None
        shifted = _shift_ranges(part, win["start"], win["own_start"], win["own_end"])
        if shifted.get("children"):
            # 把每个窗口的根作为一个结构节点，保留其标题
            node = {"title": shifted.get("title") or f"第 {win['index'] + 1} 部分",
                    "children": shifted["children"]}
            if shifted.get("src_range"):
                node["src_range"] = shifted["src_range"]
            children.append(node)
        elif shifted.get("src_range"):
            children.append(shifted)
    root = {"title": parts[0].get("title") or "未命名", "children": children}
    validation = validate_skeleton(root, _n_paras(windows))
    if not validation["ok"]:
        return None
    return root


def _n_paras(windows: list[dict]) -> int:
    return max((w.get("own_end", 0) for w in windows), default=0)


# ────────────────────────────────────────────
# 无模型确定性骨架
# ────────────────────────────────────────────

def _heading_info(text: str, profile: dict | None):
    try:
        from numbering_profile import classify_numbering, profile_level
    except Exception:
        return None
    first = (text or "").split("\n")[0].strip()
    first = re.sub(r'^\s*#{1,6}\s+', '', first).strip()
    if not _is_heading_like(first):
        return None
    info = classify_numbering(first, profile, allow_generic=profile is not None)
    if not info:
        return None
    lvl = profile_level(profile, info.get("type"), info.get("path"))
    if lvl is None:
        lvl = info.get("semantic_level")
    if lvl is None:
        path = info.get("path") or []
        lvl = len(path) + 1 if path else 3
    return {"title": first, "level": max(1, min(MAX_DEPTH, lvl)),
            "type": info.get("type"), "path": info.get("path") or []}


def deterministic_skeleton(paragraphs: list[str], profile: dict | None = None) -> dict | None:
    """无模型/骨架被拒时的确定性骨架：编号切边界，否则按块分组。"""
    n = len(paragraphs)
    if not n:
        return None
    headings = []
    for i, p in enumerate(paragraphs):
        info = _heading_info(p, profile)
        if info:
            headings.append({**info, "index": i})
    if len(headings) < 2:
        return _chunk_skeleton(paragraphs)

    root = {"title": "未命名", "children": []}
    stack = []

    def close(node: dict, end: int):
        if node.get("_start") is not None and end > node["_start"]:
            node["src_range"] = [node.pop("_start"), end]
        else:
            node.pop("_start", None)

    for hi, h in enumerate(headings):
        i = h["index"]
        next_i = headings[hi + 1]["index"] if hi + 1 < len(headings) else n
        # 关闭所有 level >= 当前 level 的节点
        while stack and stack[-1]["level"] >= h["level"]:
            close(stack.pop(), i)
        node = {"title": h["title"], "children": [], "_start": i, "level": h["level"]}
        if stack:
            stack[-1]["children"].append(node)
        else:
            root["children"].append(node)
        stack.append(node)
        # 叶节点的范围到下一个标题；父节点只保留标题段（子节点会覆盖后续）
        if hi + 1 >= len(headings) or headings[hi + 1]["level"] <= h["level"]:
            close(node, next_i)
    while stack:
        close(stack.pop(), n)

    # 首个标题前的导言
    if headings and headings[0]["index"] > 0:
        root["children"].insert(0, {
            "title": "文档导言",
            "src_range": [0, headings[0]["index"]],
            "children": [],
        })
    _drop_empty(root)
    # 去掉内部字段
    for node in _walk(root):
        node.pop("level", None)
    validation = validate_skeleton(root, n, profile)
    if not validation["ok"]:
        return _chunk_skeleton(paragraphs)
    return root


def _chunk_skeleton(paragraphs: list[str], chunk_size: int = 6,
                    group_size: int = 3) -> dict:
    n = len(paragraphs)
    chunks = []
    for start in range(0, n, chunk_size):
        end = min(n, start + chunk_size)
        head = paragraphs[start].replace("\n", " ").strip()
        head = re.sub(r'^\s*#{1,6}\s+', '', head).strip()
        title = head[:24] + ("…" if len(head) > 24 else "")
        chunks.append({
            "title": title or f"段落 {start + 1}",
            "src_range": [start, end],
            "children": [],
        })
    if len(chunks) <= 1:
        return {"title": "未命名", "children": chunks}
    # 没有编号也没有模型时，至少给一个两层结构：每 3 个块归一个主题段。
    children = []
    for gi, i in enumerate(range(0, len(chunks), group_size), start=1):
        group = chunks[i:i + group_size]
        children.append({
            "title": f"主题 {gi}",
            "src_range": [group[0]["src_range"][0], group[-1]["src_range"][1]],
            "children": group,
        })
    return {"title": "未命名", "children": children}


def _drop_empty(node: dict) -> None:
    kids = []
    for ch in node.get("children") or []:
        _drop_empty(ch)
        if ch.get("children") or ch.get("src_range"):
            kids.append(ch)
    node["children"] = kids


def validate_and_score(skeleton: dict, paragraphs: list[str],
                       profile: dict | None = None) -> dict:
    v = validate_skeleton(skeleton, len(paragraphs), profile)
    g = title_grounding(skeleton, paragraphs)
    return {
        "validation": v,
        "grounding": g,
        "score": quality_score(skeleton, v, g),
    }
