#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原文保真度审计 —— 回答「生成的树到底覆盖了多少原文」。

只读度量层，不参与建树。指标口径与 content_anchor 的段落模型同源：
原文按空行切段，逐段在树节点的 content 中查找；找不到才算丢失。

与 parse_report.tree_stats.coverage_pct 的区别：
  · coverage_pct = 有摘要的节点 / 总节点 —— 这是「AI 解读覆盖率」；
  · 本模块的 char_coverage / paragraph_recall —— 这是「原文保真度」。
两者必须分开命名、分开展示，不得互相替代。
"""
from __future__ import annotations

import bisect
import os
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from content_anchor import split_paragraphs  # noqa: E402


# ────────────────────────────────────────────
# 文本归一化
# ────────────────────────────────────────────

_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_LINK_RE = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_HTML_RE = re.compile(r'<[^>]+>')
_FENCE_LINE_RE = re.compile(r'^\s*```[^\n]*$', re.M)
_TABLE_SEP_RE = re.compile(r'^\s*\|?[\s:|\-]+\|?\s*$', re.M)
_MD_PREFIX_RE = re.compile(r'^\s{0,3}(?:#{1,6}|[-*+>])\s*', re.M)
_PAGE_LINE_RE = re.compile(r'^\s*[-—–·.…]*\s*\d{1,4}\s*[-—–·.…]*\s*$')


def normalize_text(text: str) -> str:
    """匹配用归一化：只保留 CJK/字母/数字，去掉标点与版式噪声。"""
    s = unicodedata.normalize("NFKC", text or "")
    # 代码块本身是原文内容：只去围栏标记，保留块内文字。
    s = _FENCE_LINE_RE.sub("", s)
    s = _IMAGE_RE.sub("", s)
    s = _LINK_RE.sub(r"\1", s)
    s = _HTML_RE.sub("", s)
    s = _TABLE_SEP_RE.sub("", s)
    s = _MD_PREFIX_RE.sub("", s)
    s = s.lower()
    return re.sub(r"[^0-9a-z一-鿿]+", "", s)


def _shingles(norm: str, n: int = 4) -> set[str]:
    if len(norm) <= n:
        return {norm} if norm else set()
    return {norm[i:i + n] for i in range(len(norm) - n + 1)}


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if len(a) > 4000 or len(b) > 4000:
        # 长文本用 shingle 包含率，避免 SequenceMatcher 的平方级开销
        ga, gb = _shingles(a), _shingles(b)
        if not ga:
            return 0.0
        return len(ga & gb) / len(ga)
    return SequenceMatcher(None, a, b).ratio()


# ────────────────────────────────────────────
# 原文段落模型（带行号，便于排除目录）
# ────────────────────────────────────────────

def _paragraphs_with_lines(text: str) -> list[tuple[str, int, int]]:
    """与 split_paragraphs 同口径：按空行切段，额外返回 [start, end) 行号。"""
    lines = (text or "").split("\n")
    out = []
    buf: list[str] = []
    start = None
    for i, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = i
            buf.append(line)
        elif buf:
            out.append(("\n".join(buf).strip(), start, i))
            buf = []
            start = None
    if buf:
        out.append(("\n".join(buf).strip(), start, len(lines)))
    return out


def _toc_ranges(lines: list[str]) -> list[tuple[int, int]]:
    try:
        from tree_builder import detect_toc_section
    except Exception:
        return []
    ranges = []
    i = 0
    while i < len(lines):
        try:
            is_toc, end = detect_toc_section(lines, i)
        except Exception:
            break
        if is_toc and end > i:
            ranges.append((i, end))
            i = end
        else:
            i += 1
    return ranges


def _in_ranges(a: int, b: int, ranges: list[tuple[int, int]]) -> bool:
    for x, y in ranges:
        if a < y and b > x:
            return True
    return False


def _norm_paragraph(text: str) -> str:
    """段落归一化；连续重复行（OCR 双排/页眉残留）只算一次。"""
    parts = [normalize_text(line) for line in (text or "").split("\n")]
    parts = [p for p in parts if p]
    out = []
    prev = None
    for p in parts:
        if p == prev:
            continue
        out.append(p)
        prev = p
    return "".join(out)


def _is_heading_paragraph(text: str) -> bool:
    lines = [l for l in (text or "").split("\n") if l.strip()]
    if len(lines) != 1:
        return False
    s = lines[0].strip()
    if s.startswith("#"):
        return True
    if len(s) <= 48 and not re.search(r'[。；！？]\s*$', s):
        try:
            from hierarchy_repair import has_any_numbering
            return has_any_numbering(re.sub(r'^#+\s*', '', s))
        except Exception:
            return False
    return False


def collect_source(text: str, skeleton_report: dict | None = None) -> dict:
    """原文 → 可对照的段落与标题。返回 paragraphs/headings/excluded。"""
    skeleton_report = skeleton_report or {}
    lines = (text or "").split("\n")
    toc = _toc_ranges(lines)
    dropped = {
        normalize_text(h.get("text", ""))
        for h in skeleton_report.get("dropped_headers", [])
        if h.get("text")
    }
    dropped.discard("")

    paragraphs = []
    excluded = {"toc": 0, "headers": 0, "images": 0, "page_numbers": 0,
                "empty": 0, "duplicate_heading": 0}
    prev_kept_norm = None
    for idx, (p, a, b) in enumerate(_paragraphs_with_lines(text)):
        norm = _norm_paragraph(p)
        if not norm:
            excluded["images"] += 1
            continue
        if norm in {"目录", "contents", "tableofcontents"}:
            excluded["toc"] += 1
            continue
        if _in_ranges(a, b, toc):
            excluded["toc"] += 1
            continue
        if any(d and (norm == d or (len(d) >= 8 and d in norm and len(norm) <= len(d) * 2))
               for d in dropped):
            excluded["headers"] += 1
            continue
        if all(_PAGE_LINE_RE.match(line) for line in p.split("\n") if line.strip()):
            excluded["page_numbers"] += 1
            continue
        if prev_kept_norm is not None and norm == prev_kept_norm and _is_heading_paragraph(p):
            excluded["duplicate_heading"] += 1
            continue
        paragraphs.append({"index": idx, "text": p, "norm": norm,
                           "line_start": a, "line_end": b})
        prev_kept_norm = norm

    headings = []
    for m in re.finditer(r'^\s{0,3}#{1,6}\s+(.+?)\s*$', text or "", re.M):
        title = re.sub(r'\s+', ' ', m.group(1)).strip()
        if title:
            headings.append({"title": title, "norm": normalize_text(title),
                             "source": "markdown"})
    for r in skeleton_report.get("recovered", []):
        title = (r.get("title") or "").strip()
        if title:
            headings.append({"title": title, "norm": normalize_text(title),
                             "source": "recovered"})
    for r in skeleton_report.get("chapter_titles", []):
        title = (r.get("to") or "").strip()
        title = re.sub(r'^#{1,6}\s+', '', title)
        if title:
            headings.append({"title": title, "norm": normalize_text(title),
                             "source": "chapter_restore"})
    seen = set()
    unique = []
    for h in headings:
        if h["norm"] and h["norm"] not in seen:
            seen.add(h["norm"])
            unique.append(h)

    return {
        "paragraphs": paragraphs,
        "headings": unique,
        "excluded": excluded,
    }


# ────────────────────────────────────────────
# 树侧文本
# ────────────────────────────────────────────

_SYNTHETIC_TITLES = {"（未识别小节）", "（后续内容）", "文档导言"}
_BRIDGE_PREFIX = "与《"


def _is_synthetic(node: dict, title: str, parent_title: str) -> bool:
    content = (node.get("content") or "").strip()
    if content.startswith("来源PDF："):
        return True
    if title.startswith(_BRIDGE_PREFIX):
        return True
    if title in _SYNTHETIC_TITLES:
        return True
    if parent_title and title == parent_title:
        return True
    return False


def collect_tree(tree: dict) -> dict:
    nodes = []
    order = [0]

    def walk(node: dict, path: list[str], parent_title: str):
        title = (node.get("title") or node.get("name") or "").strip()
        synthetic = _is_synthetic(node, title, parent_title)
        content = node.get("content") or ""
        nodes.append({
            "path": tuple(path + [title]),
            "title": title,
            "content": content,
            # 标题也是原文的一部分：很多 Markdown 标题会变成节点的 title，
            # 若只拿 content 对照，整行标题会被误判为丢失。
            "norm_content": normalize_text(title + "\n" + content),
            "synthetic": synthetic,
            "dfs": order[0],
        })
        order[0] += 1
        for ch in node.get("children") or []:
            walk(ch, path + [title], title)

    for ch in tree.get("children") or []:
        walk(ch, [], (tree.get("title") or "").strip())
    return {"nodes": nodes,
            "content_nodes": [n for n in nodes if n["norm_content"]],
            "title_nodes": [n for n in nodes if n["title"] and not n["synthetic"]]}


# ────────────────────────────────────────────
# 段落 / 标题匹配
# ────────────────────────────────────────────

def _choose_candidate(cands: list[tuple[int, float]], content_nodes: list[dict],
                      last_dfs: int) -> tuple[int, float] | None:
    """优先选“当前位置之后最近”的节点，避免重复标题/重复段落被匹配到后文。"""
    if not cands:
        return None
    forward = [c for c in cands if content_nodes[c[0]]["dfs"] >= last_dfs]
    pool = forward or cands
    if forward:
        return min(pool, key=lambda c: (content_nodes[c[0]]["dfs"] - last_dfs,
                                        len(content_nodes[c[0]]["norm_content"])))
    return min(pool, key=lambda c: (last_dfs - content_nodes[c[0]]["dfs"],
                                    len(content_nodes[c[0]]["norm_content"])))


def match_paragraphs(paragraphs: list[dict], tree_info: dict,
                     fuzzy: float = 0.86) -> dict:
    content_nodes = tree_info["content_nodes"]
    if not paragraphs:
        return {"matches": {}, "unmatched": [], "paragraph_recall": 1.0,
                "char_coverage": 1.0, "matched_chars": 0, "total_chars": 0}

    total_chars = sum(len(p["norm"]) for p in paragraphs)
    matched_chars = 0
    matches = {}
    unmatched = []
    last_dfs = -1
    duplicate_norms = {n for n, c in Counter(p["norm"] for p in paragraphs).items()
                       if c > 1}
    used_occurrences: dict[tuple[int, str], int] = {}

    # 4-gram 倒排：只在需要模糊匹配时查
    gram_index: dict[str, set[int]] = {}
    for ni, n in enumerate(content_nodes):
        for g in _shingles(n["norm_content"]):
            gram_index.setdefault(g, set()).add(ni)

    for p in paragraphs:
        norm = p["norm"]
        exact_cands = []
        for ni, n in enumerate(content_nodes):
            if norm not in n["norm_content"]:
                continue
            if norm in duplicate_norms:
                # 原文同一段出现多次时，节点里必须有足够多次出现，
                # 否则第二次应算丢失，不能因为第一次命中就虚高。
                if used_occurrences.get((ni, norm), 0) >= n["norm_content"].count(norm):
                    continue
            exact_cands.append((ni, 1.0))
        chosen = _choose_candidate(exact_cands, content_nodes, last_dfs)
        exact = chosen is not None

        if chosen is None:
            para_grams = _shingles(norm)
            hit_ids: set[int] = set()
            for g in para_grams:
                hit_ids.update(gram_index.get(g, ()))
            fuzzy_cands = []
            for ni in hit_ids:
                n = content_nodes[ni]
                r = _ratio(norm, n["norm_content"]) if len(norm) < 4000 else (
                    len(para_grams & _shingles(n["norm_content"])) / max(1, len(para_grams)))
                if r >= fuzzy:
                    fuzzy_cands.append((ni, r))
            if not fuzzy_cands and len(content_nodes) <= 200:
                for ni, n in enumerate(content_nodes):
                    r = _ratio(norm, n["norm_content"])
                    if r >= fuzzy:
                        fuzzy_cands.append((ni, r))
            chosen = _choose_candidate(fuzzy_cands, content_nodes, last_dfs)

        if chosen is None:
            unmatched.append({"index": p["index"], "text": p["text"][:80],
                              "line_start": p["line_start"]})
            continue
        ni, ratio = chosen
        node = content_nodes[ni]
        matches[p["index"]] = {
            "node_path": list(node["path"]),
            "dfs": node["dfs"],
            "exact": exact,
            "ratio": round(ratio, 4),
        }
        matched_chars += len(norm)
        last_dfs = node["dfs"]
        if norm in duplicate_norms:
            used_occurrences[(ni, norm)] = used_occurrences.get((ni, norm), 0) + 1

    n = len(paragraphs)
    return {
        "matches": matches,
        "unmatched": unmatched,
        "paragraph_recall": round(len(matches) / n, 4) if n else 1.0,
        "char_coverage": round(matched_chars / total_chars, 4) if total_chars else 1.0,
        "matched_chars": matched_chars,
        "total_chars": total_chars,
    }


def _title_match(source_norm: str, tree_norm: str) -> bool:
    if not source_norm or not tree_norm:
        return False
    if source_norm == tree_norm:
        return True
    if len(source_norm) >= 6 and (source_norm in tree_norm or tree_norm in source_norm):
        return True
    return _ratio(source_norm, tree_norm) >= 0.9


def match_headings(source_headings: list[dict], tree_info: dict) -> dict:
    tree_titles = [n for n in tree_info["title_nodes"] if n["title"]]
    if not source_headings:
        return {"recall": None, "precision": None, "matched": 0,
                "missing": [], "extra": []}

    matched_source = set()
    matched_tree = set()
    for si, h in enumerate(source_headings):
        for ti, t in enumerate(tree_titles):
            if _title_match(h["norm"], normalize_text(t["title"])):
                matched_source.add(si)
                matched_tree.add(ti)
                break

    # 精确率只看"像标题"的树节点：带编号、或与原文标题同族。
    structural = []
    for ti, t in enumerate(tree_titles):
        title = t["title"]
        if re.match(r'^(第\s*[一二三四五六七八九十百千\d]+\s*[章节讲单元课专题篇编]|'
                    r'[一二三四五六七八九十]+[、，,]|[（(【〔]\s*[一二三四五六七八九十\d]+|'
                    r'\d+(?:\.\d+)*[\.、，)]|[①-⑳]|知识点|考点)', title):
            structural.append(ti)
    precision_denom = len(structural)
    precision = (sum(1 for ti in structural if ti in matched_tree) / precision_denom
                 if precision_denom else None)
    missing = [source_headings[i]["title"] for i in range(len(source_headings))
               if i not in matched_source][:20]
    extra = [tree_titles[i]["title"] for i in structural if i not in matched_tree][:20]
    return {
        "recall": round(len(matched_source) / len(source_headings), 4),
        "precision": round(precision, 4) if precision is not None else None,
        "matched": len(matched_source),
        "missing": missing,
        "extra": extra,
    }


def _lis_ratio(seq: list[int]) -> float:
    if len(seq) < 2:
        return 1.0
    tails = []
    for x in seq:
        i = bisect.bisect_right(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return round(len(tails) / len(seq), 4)


def order_score(matches: dict) -> float:
    seq = [m["dfs"] for _, m in sorted(matches.items(), key=lambda kv: kv[0])]
    return _lis_ratio(seq)


# ────────────────────────────────────────────
# 结构一致性 / AI 接地（诊断）
# ────────────────────────────────────────────

def hierarchy_consistency(tree: dict) -> float:
    try:
        from outline_skeleton import check_skeleton
        violations = check_skeleton(tree.get("children") or [])
    except Exception:
        return None
    errors = sum(1 for v in violations if v.get("severity") == "error")
    edges = max(1, sum(1 for _ in _iter_nodes(tree)) - 1)
    return round(max(0.0, 1.0 - errors / edges), 4)


def _iter_nodes(node: dict):
    yield node
    for ch in node.get("children") or []:
        yield from _iter_nodes(ch)


def _grounded(text: str, source: str, threshold: float) -> bool:
    nt, ns = normalize_text(text), normalize_text(source)
    if not nt or not ns:
        return False
    if nt in ns:
        return True
    grams = _shingles(nt, 2)
    if not grams:
        return False
    return len(grams & _shingles(ns, 2)) / len(grams) >= threshold


def ai_grounding(tree: dict) -> dict:
    summary_total = summary_ok = 0
    kw_total = kw_ok = 0
    exam_total = exam_ok = 0

    def walk(node: dict):
        nonlocal summary_total, summary_ok, kw_total, kw_ok, exam_total, exam_ok
        own = [(node.get("title") or ""), (node.get("content") or "")]
        for ch in node.get("children") or []:
            own.append(ch.get("content") or "")
        source = "\n".join(x for x in own if x)
        s = (node.get("summary") or "").strip()
        if s:
            summary_total += 1
            summary_ok += int(_grounded(s, source, 0.2))
        for kw in node.get("keywords") or []:
            kw_total += 1
            kw_ok += int(_grounded(str(kw), source, 0.5))
        for ep in node.get("exam_points") or []:
            exam_total += 1
            exam_ok += int(_grounded(str(ep), source, 0.3))
        for ch in node.get("children") or []:
            walk(ch)

    walk(tree)
    def rate(ok, total):
        return round(ok / total, 4) if total else None
    return {
        "summary": {"supported": summary_ok, "total": summary_total,
                    "rate": rate(summary_ok, summary_total)},
        "keywords": {"supported": kw_ok, "total": kw_total,
                     "rate": rate(kw_ok, kw_total)},
        "exam_points": {"supported": exam_ok, "total": exam_total,
                        "rate": rate(exam_ok, exam_total)},
    }


# ────────────────────────────────────────────
# 对外入口
# ────────────────────────────────────────────

def _worse(a: str, b: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    return a if rank[a] <= rank[b] else b


def _verdict(metrics: dict) -> dict:
    reasons = []
    level = "high"
    if metrics["paragraph_recall"] < 0.995:
        level = _worse(level, "low" if metrics["paragraph_recall"] < 0.95 else "medium")
        reasons.append(f"段落召回 {metrics['paragraph_recall']:.1%}")
    if metrics["char_coverage"] < 0.99:
        level = _worse(level, "low" if metrics["char_coverage"] < 0.95 else "medium")
        reasons.append(f"字符覆盖 {metrics['char_coverage']:.1%}")
    if metrics.get("heading_recall") is not None and metrics["heading_recall"] < 0.95:
        level = _worse(level, "low" if metrics["heading_recall"] < 0.85 else "medium")
        reasons.append(f"标题召回 {metrics['heading_recall']:.1%}")
    if metrics.get("order_score") is not None and metrics["order_score"] < 0.99:
        level = _worse(level, "low" if metrics["order_score"] < 0.9 else "medium")
        reasons.append(f"顺序保真 {metrics['order_score']:.1%}")
    if metrics.get("hierarchy_consistency") is not None and metrics["hierarchy_consistency"] < 0.99:
        level = _worse(level, "low" if metrics["hierarchy_consistency"] < 0.9 else "medium")
        reasons.append(f"结构违规 {1 - metrics['hierarchy_consistency']:.1%}")
    return {"level": level, "reasons": reasons}


def _finalize(source: dict, para: dict, head: dict, order: float,
              hier: float | None, grounding: dict | None, route: str | None) -> dict:
    metrics = {
        "version": 1,
        "route": route,
        "source": {
            "paragraphs": len(source["paragraphs"]),
            "chars": para["total_chars"],
            "headings": len(source["headings"]),
            "excluded": source["excluded"],
        },
        "paragraph_recall": para["paragraph_recall"],
        "char_coverage": para["char_coverage"],
        "heading_recall": head["recall"],
        "heading_precision": head["precision"],
        "order_score": order,
        "hierarchy_consistency": hier,
        "ai_grounding": grounding,
        "lost_paragraphs": para["unmatched"][:20],
        "missing_headings": head["missing"],
        "extra_headings": head["extra"],
    }
    metrics["verdict"] = _verdict(metrics)
    return metrics


def audit_unit(text: str, tree: dict, *, route: str | None = None,
               skeleton_report: dict | None = None) -> dict:
    """单个处理单元（一份 md / 一个片段）的保真审计。"""
    source = collect_source(text, skeleton_report)
    tree_info = collect_tree(tree)
    para = match_paragraphs(source["paragraphs"], tree_info)
    head = match_headings(source["headings"], tree_info)
    return _finalize(source, para, head, order_score(para["matches"]),
                     hierarchy_consistency(tree), ai_grounding(tree), route)


def audit_final(sources: list[dict], tree: dict,
                skeleton_reports: list[dict] | None = None) -> dict:
    """合并树的整体审计。

    sources: [{"name": str, "text": str, "skeleton_report": dict|None}, ...]
    skeleton_reports: 可选，与 sources 等长的旧报告兜底。
    """
    all_paras = []
    all_headings = []
    excluded = {"toc": 0, "headers": 0, "images": 0, "page_numbers": 0,
                "empty": 0, "duplicate_heading": 0}
    by_file = []
    for idx, src in enumerate(sources):
        sk = src.get("skeleton_report")
        if sk is None and skeleton_reports and idx < len(skeleton_reports):
            sk = skeleton_reports[idx]
        collected = collect_source(src.get("text") or "", sk)
        file_start = len(all_paras)
        for p in collected["paragraphs"]:
            all_paras.append({**p, "index": len(all_paras),
                              "file": src.get("name") or ""})
        for h in collected["headings"]:
            all_headings.append({**h, "file": src.get("name") or ""})
        for k, v in collected["excluded"].items():
            excluded[k] = excluded.get(k, 0) + v
        by_file.append({
            "name": src.get("name") or "",
            "_range": (file_start, len(all_paras)),
        })

    tree_info = collect_tree(tree)
    para = match_paragraphs(all_paras, tree_info)
    head = match_headings(all_headings, tree_info)

    # 按来源文件聚合一版，便于定位问题文件
    detailed_files = []
    for f in by_file:
        a, b = f["_range"]
        subset = all_paras[a:b]
        if not subset:
            continue
        m = match_paragraphs(subset, tree_info)
        detailed_files.append({
            "name": f["name"],
            "paragraphs": len(subset),
            "paragraph_recall": m["paragraph_recall"],
            "char_coverage": m["char_coverage"],
            "lost": m["unmatched"][:5],
        })

    merged_source = {"paragraphs": all_paras, "headings": all_headings,
                     "excluded": excluded}
    result = _finalize(merged_source, para, head, order_score(para["matches"]),
                       hierarchy_consistency(tree), ai_grounding(tree), "merged")
    result["by_file"] = detailed_files
    return result


if __name__ == "__main__":
    # 最小自检：把一段原文全部挂进一个节点后，覆盖率应为 100%
    sample = "第一段原文。\n\n第二段原文，包含一些标点。\n\n第三段。"
    tree = {"title": "t", "children": [{"title": "a", "content": sample, "children": []}]}
    print(audit_unit(sample, tree, route="regex"))
