#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片段结构质量评分 —— 取代 has_heading_structure 的"标题数≥2"布尔判定。

为什么要分级
------------
原来的 `has_heading_structure` 只看「`#` 标题行数 ≥ 2」，实测暴露两类误判：
  · 无标题纯散文 → 0 标题 → 判"无结构"，但随后 parse_md_to_nodes 会把正文
    全部静默丢弃，产出空树（泛用性的最大硬伤）。
  · 扁平编号清单（"1. 要点1"…）→ txt_to_markdown 把每条都合成 `#` 标题 →
    判"有结构"，但没有层级，产出 11 个平级根节点。

实测标定（data/_temp_uploads 共 37 个真实片段）
----------------------------------------------
  标题占非空行：中位 23.2%，最低 18.8%，最高 33.7%
  带编号的标题：占标题的 85%–95%
  正文行数：全部片段 > 0
  建树节点数：全部片段 > 0
即真实教材是「**编号丰富、层级隐含**」型——真正的层级藏在编号文本里
（第X章 > 一、 > （一） > 1. > （1） > ①），而不是 markdown 的 `#` 深度。
所以判分看**编号体系能否还原出层级**，而非标题多寡。

三条路由
--------
  regex     结构可还原 → 走 tree_builder（快、忠实、不花钱）
  skeleton  结构不可还原但有正文 → AI 出骨架 + 原文锚定（保真，不改写）
  prose     连正文都稀薄 → 也要保住原文，绝不丢字
"""
from __future__ import annotations

import re
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hierarchy_repair import (NUMBER_PATTERNS, normalize_numbering,  # noqa: E402
                              TYPE_TO_SEMANTIC_LEVEL)


# ────────────────────────────────────────────
# 阈值（由 calibration() 实测标定，见文件末尾）
# ────────────────────────────────────────────

# 低于此分 → 交给 AI 骨架（结构不可还原，但正文仍要原样锚定）
THRESHOLD_SKELETON = 0.60

# 标题占比的"舒适区"：太低没结构，太高是把正文误抬成标题
HEADING_RATIO_SWEET = (0.08, 0.45)

_HEADING_RE = re.compile(r'^#{1,6}\s+\S')
_NOISE_RE = re.compile(r'(公众号|微信|考研大纲知识清单|本章自测表|知识导图|精华提要|'
                       r'考情分析|丹丹|封面|版权页|目录$)')


def _split_lines(text: str):
    lines = (text or "").split('\n')
    nonblank = [l for l in lines if l.strip()]
    headings = [l for l in nonblank if _HEADING_RE.match(l)]
    body = [l for l in nonblank if not _HEADING_RE.match(l)]
    return nonblank, headings, body


def _heading_title(line: str) -> str:
    return re.sub(r'^#{1,6}\s+', '', line).strip()


def _numbering_of(title: str):
    """→ (numbering_type, semantic_level) 或 (None, 0)。

    层级用 hierarchy_repair 的语义层级（第X章=1 … ①=7），不是编号的路径深度：
    真实教材的层级藏在"编号体系"里而非 markdown `#` 深度，实测这些片段的
    `#` 深度恒为 1（OCR 把每个条目都抬成 h1）。
    """
    t = normalize_numbering((title or "").strip())
    for name, pat in NUMBER_PATTERNS.items():
        m = pat.match(t)
        if m:
            return name, TYPE_TO_SEMANTIC_LEVEL.get(name, 0) or 0
    return None, 0


def score_fragment(text: str) -> dict:
    """→ {score, route, signals, reasons}

    score ∈ [0,1]；route ∈ {"regex", "skeleton", "prose"}。
    """
    nonblank, headings, body = _split_lines(text)
    n_non = len(nonblank)
    n_head = len(headings)
    n_body = len(body)

    if n_non == 0:
        return {"score": 0.0, "route": "prose",
                "signals": {"nonblank": 0, "headings": 0, "body": 0},
                "reasons": ["空白片段"]}

    signals = {
        "nonblank": n_non,
        "headings": n_head,
        "body": n_body,
        "heading_ratio": n_head / n_non,
        "body_ratio": n_body / n_non,
        "numbered_ratio": 0.0,
        "family_count": 0,
        "max_depth": 0,
        "noise_ratio": 0.0,
        "max_depth_by_markdown": 0,
    }
    reasons = []

    # ── 编号体系：真实教材的层级在这里 ──
    numbered = 0
    families = set()
    levels_seen = set()
    noise = 0
    for h in headings:
        title = _heading_title(h)
        if _NOISE_RE.search(title):
            noise += 1
        fam, slevel = _numbering_of(title)
        if fam:
            numbered += 1
            families.add(fam)
            if slevel:
                levels_seen.add(slevel)
        # markdown `#` 深度也算一种层级线索
        m = re.match(r'^(#{1,6})', h)
        if m:
            signals["max_depth_by_markdown"] = max(
                signals["max_depth_by_markdown"], len(m.group(1)))

    signals["numbered_ratio"] = (numbered / n_head) if n_head else 0.0
    signals["family_count"] = len(families)
    # 层级广度 = 能还原出多少个不同层级（真实教材是 4–6 层）
    signals["max_depth"] = len(levels_seen) or signals["max_depth_by_markdown"]
    signals["noise_ratio"] = (noise / n_head) if n_head else 0.0

    # ── 打分 ──
    # 1) 能还原出多少层级（权重最高）
    s_hierarchy = 0.0
    if signals["max_depth"] >= 5:
        s_hierarchy = 1.0
    elif signals["max_depth"] == 4:
        s_hierarchy = 0.9
    elif signals["max_depth"] == 3:
        s_hierarchy = 0.7
    elif signals["max_depth"] == 2:
        s_hierarchy = 0.4
    elif signals["max_depth"] == 1:
        s_hierarchy = 0.15

    # 2) 编号一致性：编号标题占比越高，层级越可信
    s_numbering = signals["numbered_ratio"]

    # 3) 编号族丰富度：章/节/一、/（一）/1. 混用 → 层级更清晰
    s_family = min(1.0, signals["family_count"] / 3.0)

    # 4) 标题/正文平衡：接近舒适区给满分，两端扣分
    hr = signals["heading_ratio"]
    lo, hi = HEADING_RATIO_SWEET
    if lo <= hr <= hi:
        s_balance = 1.0
    elif hr < lo:
        s_balance = max(0.0, hr / lo)          # 标题太少 → 没结构
    else:
        s_balance = max(0.0, 1.0 - (hr - hi) / (1.0 - hi))   # 标题太多 → 正文被抬成标题

    # 5) 噪声标题（页眉页脚混进标题）扣分
    s_noise = max(0.0, 1.0 - signals["noise_ratio"] * 2.0)

    score = (0.32 * s_hierarchy + 0.28 * s_numbering +
             0.12 * s_family + 0.18 * s_balance + 0.10 * s_noise)
    score = max(0.0, min(1.0, score))

    # ── 路由：只有两条，"保住原文"是不变式而非路由 ──
    # 无论走哪条，正文都必须原样落在节点里（见 content_anchor）。
    route = "regex" if score >= THRESHOLD_SKELETON else "skeleton"

    if signals["max_depth"] <= 1:
        reasons.append("层级不可还原（只有一级）")
    if signals["numbered_ratio"] < 0.3 and n_head:
        reasons.append("标题多无编号")
    if hr > HEADING_RATIO_SWEET[1]:
        reasons.append(f"标题占比 {hr:.0%}，正文疑被抬成标题")
    if hr == 0:
        reasons.append("无标题，按段落保留正文")
    if signals["noise_ratio"] > 0.15:
        reasons.append("页眉/页脚噪声混入标题")
    if not reasons and route == "regex":
        reasons.append("结构可还原")
    if route == "skeleton":
        reasons.append("交 AI 出骨架，原文原样锚定")

    return {"score": round(score, 4), "route": route,
            "signals": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in signals.items()},
            "reasons": reasons}


# ────────────────────────────────────────────
# 标定工具：把真实片段与合成不良样本放在一起看分
# ────────────────────────────────────────────

def _synthetic_cases():
    prose = ("教育学是研究教育现象和教育问题，揭示教育规律的一门科学。"
             "教育的概念有广义与狭义之分。广义的教育泛指一切有目的地增进人的知识和技能、"
             "影响人的思想品德的活动。狭义的教育主要指学校教育。")
    return {
        "真实-散文无标题": "\n\n".join(prose[i:i + 180] for i in range(0, 1200, 180)),
        "真实-扁平编号清单": "\n".join(f"# {i}. 要点{i}：教育学基础内容" for i in range(1, 12)),
        "真实-单标题巨文本": "# 导论\n\n" + prose * 3,
        "真实-只有四级标题": ("#### 小节A\n\n" + prose[:300] +
                           "\n\n#### 小节B\n\n" + prose[300:600]),
        "真实-编号大纲": ("# 第一章 教育概述\n# 一、教育的概念\n# （一）定义\n"
                      "# 1. 广义\n# （1）社会教育\n# 一、教育的功能\n" + prose[:400]),
        "真实-过度抬升": "\n".join(f"# (i) 句子{i}。{prose[:20]}" for i in range(1, 40)),
    }


def calibration():
    import glob
    import statistics
    md_dir = os.path.join(BASE_DIR, "data", "_temp_uploads", "*", "markdown", "*.md")
    files = sorted(glob.glob(md_dir))
    print("=" * 78)
    print("结构质量评分标定")
    print("=" * 78)
    if files:
        scores = []
        print(f"\n【真实片段】共 {len(files)} 个")
        print(f"{'片段':44s} {'score':>6s} {'route':>10s} {'层级':>4s} {'编号比':>7s} {'标题比':>7s}")
        for fp in files:
            r = score_fragment(open(fp, encoding="utf-8").read())
            scores.append(r["score"])
            name = os.path.basename(fp)[:44]
            s = r["signals"]
            print(f"{name:44s} {r['score']:6.2f} {r['route']:>10s} "
                  f"{s['max_depth']:4d} {s['numbered_ratio']:7.2f} {s['heading_ratio']:7.2f}")
        print(f"\n  真实片段 score：中位 {statistics.median(scores):.2f}，"
              f"最低 {min(scores):.2f}，最高 {max(scores):.2f}")
        print(f"  判为 regex 的比例：{sum(1 for s in scores if s >= THRESHOLD_SKELETON) / len(scores):.0%}")
        print(f"  判为 skeleton 的比例：{sum(1 for s in scores if s < THRESHOLD_SKELETON) / len(scores):.0%}")

    print(f"\n【合成不良样本】阈值 skeleton={THRESHOLD_SKELETON}")
    print(f"{'样本':22s} {'score':>6s} {'route':>10s} {'层级':>4s} {'编号比':>7s} {'标题比':>7s}  期望")
    expect = {"真实-散文无标题": "skeleton", "真实-扁平编号清单": "skeleton",
              "真实-单标题巨文本": "skeleton", "真实-只有四级标题": "skeleton",
              "真实-编号大纲": "regex", "真实-过度抬升": "skeleton"}
    for name, text in _synthetic_cases().items():
        r = score_fragment(text)
        s = r["signals"]
        mark = "OK" if r["route"] == expect[name] else "!!"
        print(f"{name:22s} {r['score']:6.2f} {r['route']:>10s} "
              f"{s['max_depth']:4d} {s['numbered_ratio']:7.2f} {s['heading_ratio']:7.2f}  "
              f"{expect[name]:14s} {mark}")
        for reason in r["reasons"]:
            print(f"{'':22s}   · {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(calibration())
