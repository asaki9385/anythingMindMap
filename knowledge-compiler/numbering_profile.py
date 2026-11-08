#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编号画像 —— 从文档样本学习「族 → 层级 → 重启作用域」。

现有 hierarchy_repair 是一张静态规则表：15 个族、固定层级、不认识就继承父级。
本模块补的是「先看几页，再排全篇」：

  1. probe_numbering_profile(text)：前 N 行快探 + 全文投票；
  2. 未知族泛化：第X讲/单元/课/专题/篇/编、罗马数字、字母、㉑-㊿、多级点号；
  3. 族间偏序：用「B 是否出现在两个 A 之间」推断父子，拓扑排序得层级；
  4. 重启作用域：节在章后重启、知识点在节后重启，分别记录；
  5. 低置信度不生效：profile 默认 None，旧逻辑完全不变。

profile 是纯 dict，可随任务落盘/进报告；所有调用点都接受 profile=None。
"""
from __future__ import annotations

import re
import sys
import os
import unicodedata
from collections import OrderedDict, Counter, defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hierarchy_repair import (  # noqa: E402
    NUMBER_PATTERNS, TYPE_TO_SEMANTIC_LEVEL, detect_numbering_type,
    normalize_numbering, chinese_to_int, circle_to_int,
)

PROFILE_VERSION = 1
MIN_CONFIDENCE = 0.60


# ────────────────────────────────────────────
# 未知族泛化
# ────────────────────────────────────────────

_CN_NUM = r'[一二三四五六七八九十百千\d]+'
_ROMAN = r'[IVXLCDMⅠ-Ⅻⅰ-ⅻ]+'

# name → (regex, default_level, kind)
GENERIC_PATTERNS = OrderedDict({
    'volume_cn': (re.compile(rf'^第\s*({_CN_NUM})\s*(篇|编|辑)'), 1, 'cn'),
    'unit_cn': (re.compile(rf'^第\s*({_CN_NUM})\s*(讲|单元|课|专题|模块|项目|任务)'), 1, 'cn'),
    'topic_cn': (re.compile(rf'^(?:专题|模块|项目|任务)\s*({_CN_NUM})'), 1, 'cn'),
    'roman_dotted': (re.compile(rf'^({_ROMAN})\s*[.、．)]', re.I), 3, 'roman'),
    'roman_bracket': (re.compile(rf'^[（(【〔\[]\s*({_ROMAN})\s*[）)】〕\]]', re.I), 4, 'roman'),
    'alpha_dotted': (re.compile(r'^([A-Za-z])\s*[.、．)]'), 5, 'alpha'),
    'alpha_bracket': (re.compile(r'^[（(【〔\[]\s*([A-Za-z])\s*[）)】〕\]]'), 6, 'alpha'),
    'circle_ext': (re.compile(r'^([㉑-㊿])'), 7, 'circle_ext'),
    'multi_dotted_ext': (re.compile(r'^(\d+(?:[.．]\d+)+)(?:[.、．，,)]|\s+)'), None, 'multi'),
})

GENERIC_DEFAULT_LEVEL = {
    'volume_cn': 1, 'unit_cn': 1, 'topic_cn': 1,
    'roman_dotted': 3, 'roman_bracket': 4,
    'alpha_dotted': 5, 'alpha_bracket': 6,
    'circle_ext': 7,
}

_ROMAN_MAP = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}


def roman_to_int(s: str) -> int:
    s = unicodedata.normalize("NFKC", (s or "")).upper()
    total = 0
    prev = 0
    for ch in reversed(s):
        v = _ROMAN_MAP.get(ch, 0)
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return max(0, total)


def _cn_to_int(raw: str) -> int:
    raw = (raw or '').strip()
    if raw.isdigit():
        return int(raw)
    try:
        return chinese_to_int(raw)
    except Exception:
        return 0


def _generic_value(kind: str, raw: str) -> int:
    if kind == 'cn':
        return _cn_to_int(raw)
    if kind == 'roman':
        return roman_to_int(raw)
    if kind == 'alpha':
        return ord(raw.upper()) - 64
    if kind == 'circle_ext':
        code = ord(raw)
        if 0x3251 <= code <= 0x32BF:
            return code - 0x3251 + 21
        return circle_to_int(raw) or 0
    return 0


def classify_generic(title: str) -> dict | None:
    """已知 15 族之外的编号识别。返回与 detect_numbering_type 同形。"""
    t = normalize_numbering((title or '').strip())
    for name, (pat, level, kind) in GENERIC_PATTERNS.items():
        m = pat.match(t)
        if not m:
            continue
        raw = m.group(1)
        if kind == 'multi':
            path = [int(x) for x in re.split(r'[.．]', raw) if x.isdigit()]
            if not path:
                continue
            return {'type': 'arabic_dotted', 'semantic_level': None,
                    'path': path, 'raw_prefix': t[:m.end()].strip()}
        val = _generic_value(kind, raw)
        if val <= 0:
            continue
        return {'type': name, 'semantic_level': level,
                'path': [val], 'raw_prefix': t[:m.end()].strip()}
    return None


def classify_numbering(title: str, profile: dict | None = None,
                       *, allow_generic: bool = True) -> dict | None:
    """已知族 → 泛化族 → profile 层级覆盖。

    allow_generic=False 时只认旧 15 族；调用方在 profile=None 时用它保持旧行为。
    """
    t = normalize_numbering((title or '').strip())
    info = detect_numbering_type(t)
    if info is None and allow_generic:
        info = classify_generic(t)
    if info is None:
        return None
    lvl = profile_level(profile, info.get('type'), info.get('path'))
    if lvl is not None:
        info = {**info, 'semantic_level': lvl}
    return info


# ────────────────────────────────────────────
# 候选行
# ────────────────────────────────────────────

_HEADING_RE = re.compile(r'^#{1,6}\s+(.+?)\s*$')
_PAGE_SUFFIX_RE = re.compile(r'(?:/|\.{2,}|…{2,}|·{2,})\s*\d{1,4}\s*$')
_SENT_END_RE = re.compile(r'[。；！？]\s*$')


def _looks_like_plain_heading(s: str) -> bool:
    if not s or len(s) > 60:
        return False
    if s.startswith(('|', '>', '```', '![', '<')):
        return False
    if _PAGE_SUFFIX_RE.search(s) or _SENT_END_RE.search(s):
        return False
    return True


def _toc_line_ranges(lines: list[str]) -> list[tuple[int, int]]:
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


def _in_ranges(i: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= i < b for a, b in ranges)


def collect_candidates(lines: list[str], *, max_lines: int | None = None) -> list[dict]:
    """收集可能承载编号的行：Markdown 标题优先；纯文本候选仅在标题不足时使用。"""
    end = len(lines) if max_lines is None else min(len(lines), max_lines)
    toc = _toc_line_ranges(lines[:end])
    headings = []
    plains = []
    unknown_plains = []
    heading_count_all = 0
    in_fence = False
    for i in range(end):
        raw = lines[i]
        s = raw.strip()
        if s.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence or not s or _in_ranges(i, toc):
            continue
        m = _HEADING_RE.match(s)
        if m:
            heading_count_all += 1
            title = m.group(1).strip()
            info = classify_numbering(title)
            if info:
                headings.append({'line': i, 'title': title, 'kind': 'heading',
                                 'info': info})
            continue
        if _looks_like_plain_heading(s):
            info = classify_numbering(s)
            item = {'line': i, 'title': s, 'kind': 'plain', 'info': info}
            if info:
                plains.append(item)
            elif len(s) <= 48:
                unknown_plains.append(item)
    # 真实教材 OCR 往往每行都是 #；只有标题稀少时才采纳纯文本候选，
    # 避免正文里的 "1. 含义。" 被当成大纲。
    use_plain = heading_count_all < 10
    return headings + ((plains + unknown_plains) if use_plain else [])


# ────────────────────────────────────────────
# 族统计 / 族间偏序
# ────────────────────────────────────────────

def _value(info: dict) -> int:
    path = info.get('path') or []
    return path[0] if path else 0


def _family_stats(cands: list[dict]) -> dict:
    fams = OrderedDict()
    for c in cands:
        info = c.get('info')
        if not info:
            continue
        name = info['type']
        entry = fams.setdefault(name, {
            'count': 0, 'first': c['line'], 'last': 0, 'values': [],
            'depths': set(), 'restarts': [], 'gaps': [], 'sample': c['title'][:60],
        })
        entry['count'] += 1
        entry['last'] = c['line']
        entry['depths'].add(len(info.get('path') or []))
        entry['values'].append((c['line'], _value(info)))
    for name, e in fams.items():
        vals = e.pop('values')
        multi = max(e['depths'] or {1}) > 1
        prev = 0
        for line, val in vals:
            if prev and not multi:
                if val <= prev:
                    e['restarts'].append({'line': line, 'prev': prev, 'value': val})
                elif val > prev + 2:
                    e['gaps'].append({'line': line, 'prev': prev, 'value': val})
            prev = val
        e['depths'] = sorted(e['depths'])
    return fams


def _containment_edges(cands: list[dict]) -> dict[tuple[str, str], int]:
    """B 出现在两个相邻 A 之间 → B 很可能是 A 的子级。"""
    edges = defaultdict(int)
    names = [c['info']['type'] if c.get('info') else None for c in cands]
    positions = defaultdict(list)
    for i, name in enumerate(names):
        if name:
            positions[name].append(i)
    for a, pos in positions.items():
        for left, right in zip(pos, pos[1:]):
            between = Counter(n for n in names[left + 1:right] if n)
            for b, count in between.items():
                if b != a:
                    edges[(a, b)] += count
    return edges


def _default_level(name: str) -> int | None:
    lvl = TYPE_TO_SEMANTIC_LEVEL.get(name)
    if lvl is not None:
        return lvl
    return GENERIC_DEFAULT_LEVEL.get(name)


def _infer_order(fams: dict, cands: list[dict]) -> list[str]:
    """默认层级优先；未知族按“最强父级 + 1”推断；同级用包含关系破平。

    已知 15 族的语义层级是经过标定的，不能被一次包含统计轻易推翻；
    包含关系只用于：① 给没有默认层级的族（多级点号/新族）定位；
    ② 同层族之间谁包谁。
    """
    names = list(fams.keys())
    if not names:
        return []
    edges = _containment_edges(cands)

    first_pos = {}
    for i, c in enumerate(cands):
        info = c.get('info')
        if info and info['type'] not in first_pos:
            first_pos[info['type']] = i

    levels = {}
    for n in names:
        lvl = _default_level(n)
        if lvl is None:
            # 未知层级：看它第一次出现前最近的一个已知层级族，
            # 通常就是它的直接父级（比全局包含统计更稳）。
            idx = first_pos.get(n, len(cands))
            for prev in reversed(cands[:idx]):
                info = prev.get('info')
                if info and _default_level(info['type']) is not None:
                    lvl = _default_level(info['type']) + 1
                    break
            if lvl is None:
                parents = [(a, edges[(a, n)]) for (a, b) in edges
                           if b == n and _default_level(a) is not None]
                if parents:
                    a, _ = max(parents, key=lambda x: x[1])
                    lvl = _default_level(a) + 1
                else:
                    lvl = 5
        levels[n] = lvl

    order = []
    for lvl in sorted(set(levels.values())):
        group = [n for n in names if levels[n] == lvl]
        # 同层：A 包住 B 的次数明显更多 → A 在前
        graph = defaultdict(set)
        indeg = {n: 0 for n in group}
        for a in group:
            for b in group:
                if a == b:
                    continue
                ab, ba = edges.get((a, b), 0), edges.get((b, a), 0)
                if ab >= 2 and ab > ba * 2:
                    graph[a].add(b)
        for a, bs in graph.items():
            for b in bs:
                indeg[b] += 1
        ready = sorted([n for n in group if indeg[n] == 0],
                       key=lambda n: fams[n]['first'])
        seq = []
        while ready:
            n = ready.pop(0)
            seq.append(n)
            for b in sorted(graph.get(n, ()), key=lambda x: fams[x]['first']):
                indeg[b] -= 1
                if indeg[b] == 0:
                    ready.append(b)
            ready.sort(key=lambda x: fams[x]['first'])
        if len(seq) != len(group):
            seq = sorted(group, key=lambda n: fams[n]['first'])
        order.extend(seq)
    return order


# ────────────────────────────────────────────
# 画像
# ────────────────────────────────────────────

def _profile_from_candidates(cands: list[dict], *, source: str,
                             sample_lines: int, total_lines: int) -> dict:
    fams = _family_stats(cands)
    order = _infer_order(fams, cands)

    # 重启作用域：最近的前置高层族
    rank = {n: i for i, n in enumerate(order)}
    for name, e in fams.items():
        scope = None
        if e['restarts']:
            for r in e['restarts']:
                before = [c for c in cands if c['line'] < r['line']]
                higher = [c['info']['type'] for c in before
                          if rank.get(c['info']['type'], -1) < rank.get(name, 99)]
                scope = higher[-1] if higher else 'document'
                break
        e['restart_scope'] = scope

    unknown = []
    for c in cands:
        if not c.get('info'):
            unknown.append({'line': c['line'], 'title': c['title'][:60]})

    transitions = max(1, len(cands) - 1)
    unexplained = sum(len(e['restarts']) for e in fams.values()) + \
        sum(len(e['gaps']) for e in fams.values())
    consistency = max(0.0, 1.0 - unexplained / transitions)
    classified = sum(1 for c in cands if c.get('info'))
    coverage = (classified / len(cands)) if cands else 1.0
    sample_factor = min(1.0, classified / 12.0)
    confidence = round(0.4 * coverage + 0.4 * consistency + 0.2 * sample_factor, 3)
    if not cands:
        confidence = 0.0

    families = {}
    for name, e in fams.items():
        families[name] = {
            'level': _default_level(name),
            'rank': rank.get(name),
            'count': e['count'],
            'first': e['first'],
            'depths': e['depths'],
            'restart_scope': e['restart_scope'],
            'restarts': e['restarts'][:10],
            'gaps': e['gaps'][:10],
            'sample': e['sample'],
        }

    return {
        'version': PROFILE_VERSION,
        'families': families,
        'order': order,
        'restart_scope': {n: e.get('restart_scope') for n, e in families.items()
                          if e.get('restart_scope')},
        'cross_file': 'restart',
        'confidence': confidence,
        'effective': confidence >= MIN_CONFIDENCE,
        'unknown': unknown[:20],
        'source': source,
        'sample': {'lines': sample_lines, 'total_lines': total_lines,
                   'candidates': len(cands)},
    }


def probe_numbering_profile(text: str, *, sample_lines: int = 200,
                            doc_hint: str = "") -> dict:
    """前 N 行快探 + 全文投票。低置信度时 effective=False。"""
    lines = (text or '').split('\n')
    sample_cands = collect_candidates(lines, max_lines=sample_lines) if lines else []
    full_cands = collect_candidates(lines) if lines else []

    # 全文候选不足时，退回样本候选（短文档/标题都在前几页）
    if len(full_cands) >= 3:
        cands = full_cands
        source = 'full' if not sample_cands else 'sample+full'
    else:
        cands = sample_cands
        source = 'sample'

    profile = _profile_from_candidates(cands, source=source,
                                       sample_lines=min(sample_lines, len(lines)),
                                       total_lines=len(lines))
    # 样本里有、全文里被漏掉的族，补进画像（例如后文换族/截断）
    sample_fams = _family_stats(sample_cands)
    for name, e in sample_fams.items():
        if name not in profile['families']:
            profile['families'][name] = {
                'level': _default_level(name), 'rank': None, 'count': e['count'],
                'first': e['first'], 'depths': e['depths'],
                'restart_scope': e.get('restart_scope'), 'restarts': [],
                'gaps': [], 'sample': e['sample'],
            }
    if profile['source'] == 'sample+full':
        profile['source'] = 'sample+full'
    return profile


def profile_level(profile: dict | None, family: str,
                  path: list[int] | None = None) -> int | None:
    """profile 中的族层级；profile 不生效时返回 None（调用方走旧逻辑）。"""
    if not profile or not profile.get('effective', True):
        return None
    fams = profile.get('families') or {}
    entry = fams.get(family)
    if family in ('arabic_dotted', 'multi_dotted_ext'):
        # 多级点号的层级由 path 深度决定，不用一个固定值覆盖
        if not entry or entry.get('level') is None:
            return None
    if entry and entry.get('level') is not None:
        return entry['level']
    order = profile.get('order') or []
    if family in order:
        return order.index(family) + 1
    return _default_level(family)


def profile_rank(profile: dict | None, family: str) -> int | None:
    """层次比较用的 rank（越小越高）。"""
    if not profile or not profile.get('effective', True):
        return None
    order = profile.get('order') or []
    if family in order:
        return order.index(family)
    lvl = _default_level(family)
    return None if lvl is None else lvl


def merge_profiles(profiles: list[dict] | None) -> dict | None:
    """多文件/多片段的编号画像合并：取最高置信度为主，计数与未知项累加。"""
    profiles = [p for p in (profiles or []) if p]
    if not profiles:
        return None
    base = max(profiles, key=lambda p: p.get('confidence', 0))
    merged = {
        'version': PROFILE_VERSION,
        'families': {},
        'order': list(base.get('order') or []),
        'restart_scope': dict(base.get('restart_scope') or {}),
        'cross_file': 'restart',
        'confidence': round(sum(p.get('confidence', 0) for p in profiles) / len(profiles), 3),
        'effective': any(p.get('effective', True) for p in profiles),
        'unknown': [],
        'source': 'merged',
        'files': len(profiles),
        'sample': {
            'lines': sum((p.get('sample') or {}).get('total_lines', 0) for p in profiles),
            'candidates': sum((p.get('sample') or {}).get('candidates', 0) for p in profiles),
        },
    }
    for p in profiles:
        for name, e in (p.get('families') or {}).items():
            cur = merged['families'].setdefault(name, {
                'level': e.get('level'), 'rank': e.get('rank'),
                'count': 0, 'first': e.get('first', 1 << 30),
                'depths': e.get('depths') or [1], 'restart_scope': e.get('restart_scope'),
                'restarts': [], 'gaps': [], 'sample': e.get('sample', ''),
            })
            cur['count'] += int(e.get('count') or 0)
            cur['first'] = min(cur['first'], e.get('first', 1 << 30))
            if cur.get('level') is None:
                cur['level'] = e.get('level')
            if cur.get('rank') is None:
                cur['rank'] = e.get('rank')
            cur['restarts'].extend((e.get('restarts') or [])[:3])
            cur['gaps'].extend((e.get('gaps') or [])[:3])
        merged['unknown'].extend(p.get('unknown') or [])
        merged['restart_scope'].update(p.get('restart_scope') or {})
    # 补上主画像没有、但其他文件出现的族
    for name, e in sorted(merged['families'].items(),
                          key=lambda kv: (kv[1].get('level') or 99, kv[1]['first'])):
        if name not in merged['order']:
            merged['order'].append(name)
    merged['unknown'] = merged['unknown'][:20]
    merged['restarts'] = sum(len(e.get('restarts') or []) for e in merged['families'].values())
    merged['effective'] = merged['effective'] and merged['confidence'] >= MIN_CONFIDENCE
    return merged


def profile_summary(profile: dict | None) -> str:
    if not profile:
        return "编号体系：未识别"
    order = profile.get('order') or []
    fams = profile.get('families') or {}
    parts = [f"{n}×{fams.get(n, {}).get('count', 0)}" for n in order[:6]]
    return (f"编号体系：{' > '.join(order[:6]) or '无'}；"
            f"置信度 {profile.get('confidence', 0):.0%}；"
            f"样本 {profile.get('sample', {}).get('candidates', 0)} 行"
            + (f"（{' / '.join(parts)}）" if parts else ""))
