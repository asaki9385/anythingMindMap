#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构前置层：文档骨架的恢复与校验（outline_skeleton）。

解决什么问题
------------
解析阶段最昂贵的一类错误是**层级错位**（某主题内容挂到另一个主题下）。
根源发生在"节点出生之前"：OCR/抽取输出里，大纲标题可能失去 `#` 标记变成
纯文本行（例：「第四节 魏晋南北朝与隋唐时期的教育思想」后跟一张图），
`parse_md_to_nodes` 只认 `#` 行 → 该节节点从未创建 → 后续知识点/内容
全部挂进上一节。事后审校（placement_audit 等）只能操作"已存在的节点"，
无法修复一个从未被创建的边界。

本模块在**解析前置**与**增强前置**共用一套确定性逻辑（无 AI 调用）：

1. normalize_markdown_outline(md_text, doc_hint)
   · Pass 0  标题内的编号空格坍缩（"第 二 节 …" → "第二节 …"）；
   · Pass 0.5 紧邻重复的标题行只保留一条（OCR 双排/页眉残留）；
   · Pass 1  重复页眉剔除（与文件名有词面重叠、整行出现 ≥3 次的标题）；
   · Pass 2  章名补全（裸「第X章」+ 真章名；正文里带章号的章名行回填）；
   · Pass 3  大纲标题恢复：纯文本「第X节 / 知识点X / 第X章」行，满足
        序列吻合（= 上一编号 +1）、孤立（前后不是同类结构行）、短行、
        非目录（无 /页码）→ 提升为标题。允许尾部句读（短标题）；
        序列不吻合 → 判为页眉/导图文字/目录，忽略；断档 → 记 warnings。

2. check_skeleton(roots) / repair_boundaries(roots) / audit_tree_skeleton(roots)
   · 同级节点序列不变量（章/节/知识点 严格递增、不重复）；
   · 知识点序列重启（<= 上一值）= 缺失边界的确定性证据：
     - 重启发生在「节」里 → 补出的占位提升为该节的**兄弟**（缺的是节）；
     - 重启发生在「章」下（首节标题丢失）→ 章内直挂知识点按重启切分，
       逐段包进占位；占位与节同级。
   · 同号同名（重复行）只记 duplicates，不产生占位。

所有恢复/移动/告警都写进返回报告，无静默修改；对无问题的文档零改动。

设计文档：docs/结构前置层-骨架恢复与校验.md
测试：test_outline_skeleton.py / test_hierarchy_recovery.py
"""

from __future__ import annotations

import re
from collections import Counter

from hierarchy_repair import chinese_to_int, has_any_numbering, is_box_title

# 无标题文本可恢复时，缺失边界用占位节点承载后续条目
PLACEHOLDER_TITLE = "（未识别小节）"

# ── 通用标题族（与 hierarchy_repair 的编号口径一致）──
_CHAPTER_ANY_RE = re.compile(r'^第([一二三四五六七八九十百\d]+)章')
_SECTION_ANY_RE = re.compile(r'^第([一二三四五六七八九十百\d]+)节')
_KP_ANY_RE = re.compile(r'^知识点([一二三四五六七八九十\d]+)')

# ── 纯文本候选行（无 # 前缀）──
_PLAIN_SECTION_RE = re.compile(r'^第([一二三四五六七八九十]+)节[\s\u3000]+(\S.*)$')
_PLAIN_KP_RE = re.compile(r'^知识点([一二三四五六七八九十\d]+)\s*[、.：:]?\s*(\S.*)?$')
_PLAIN_CHAPTER_RE = re.compile(r'^第([一二三四五六七八九十]+)章[\s\u3000]+(\S.*)$')

_BARE_CHAPTER_HEADING_RE = re.compile(r'^(#{1,6})\s*第([一二三四五六七八九十]+)章\s*$')
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$')

_PUNCT_END = '。；！？，'
_PAGE_SUFFIX_RE = re.compile(r'/\s*\d+\s*$')

# 章名补全时最多接受的长度（超过视为正文句子）
_SUBTITLE_MAX = 30
# 提升行的长度上限（防止把正文句子当标题）
_PROMOTE_LINE_MAX = 40
_PROMOTE_TAIL_MAX = 34
# 带句读的标题（提升时去句读）允许的正文长度上限
_PROMOTE_PUNCT_TAIL_MAX = 20
# 紧邻重复标题行判定的空行上限
_DUP_GAP_MAX = 2


def _num_from_raw(raw: str) -> int:
    raw = (raw or '').strip()
    if raw.isdigit():
        return int(raw)
    return chinese_to_int(raw)


def _v(vtype: str, severity: str, path: list, title: str, detail: str) -> dict:
    return {
        "type": vtype,
        "severity": severity,
        "path": " > ".join([p for p in path if p]),
        "title": title,
        "detail": detail,
    }


def _collapse_num_spaces(text: str) -> str:
    """标题内的编号空格坍缩："第 二 节" → "第二节"，"知识点 一" → "知识点一"。"""
    t = re.sub(r'第\s*([一二三四五六七八九十]+)\s*章', r'第\1章', text or '')
    t = re.sub(r'第\s*([一二三四五六七八九十]+)\s*节', r'第\1节', t)
    t = re.sub(r'知识点\s*([一二三四五六七八九十\d]+)', r'知识点\1', t)
    return t


def _strip_marks_prefix(s: str) -> str:
    return re.sub(r'^#+\s*', '', s or '').strip()


def _kp_text_after_num(title: str) -> str:
    return re.sub(r'^知识点[一二三四五六七八九十\d]+\s*[、.：:]?\s*', '', (title or '').strip()).strip()


def _is_title_line(s: str) -> bool:
    """标题行：markdown 标题，或三种纯文本结构行。"""
    if not s:
        return False
    if re.match(r'^#+\s', s):
        return True
    return bool(_PLAIN_SECTION_RE.match(s) or _PLAIN_CHAPTER_RE.match(s)
                or _PLAIN_KP_RE.match(s))


def _same_title_text(a: str, b: str) -> bool:
    ta = _collapse_num_spaces(_strip_marks_prefix(a))
    tb = _collapse_num_spaces(_strip_marks_prefix(b))
    return bool(ta) and ta == tb


def new_report() -> dict:
    return {
        "recovered": [],        # [{"line": n, "title": t, "kind": "chapter|section|kp"}]
        "dropped_headers": [],  # [{"text": t, "count": n}]
        "chapter_titles": [],   # [{"line": n, "to": t}]
        "deduped_titles": [],   # [{"line": n, "text": t}]
        "duplicates": [],       # [{"parent": t, "title": t}]（同号同名，未产生占位）
        "placeholders": [],
        "warnings": [],         # [{"type","detail","line"}]
        "violations_before": 0,
        "violations_after": 0,
        "errors_before": 0,
        "errors_after": 0,
        "samples": [],
    }


# =============================================================================
# 解析前置：markdown 规范化（骨架恢复）
# =============================================================================

def _hint_tokens(doc_hint: str) -> set:
    """从文件名/文档标题里抽取"页眉可能呼应"的词面 token。"""
    if not doc_hint:
        return set()
    toks = set()
    for run in re.findall(r'[A-Za-z0-9]{3,}', doc_hint):
        toks.add(run.lower())
    for run in re.findall(r'[\u4e00-\u9fff]{4,}', doc_hint):
        toks.add(run)
    return toks


def _overlaps_hint(text: str, tokens: set) -> bool:
    if not tokens:
        return False
    t = re.sub(r'\s+', '', text).lower()
    return any(tok.lower().replace(' ', '') in t for tok in tokens)


def _next_nonempty(lines: list, i: int) -> str:
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    return lines[j].strip() if j < len(lines) else ''


def _prev_nonempty(lines: list, i: int) -> str:
    j = i - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    return lines[j].strip() if j >= 0 else ''


def _is_plain_struct_line(s: str) -> bool:
    """不含 # 的结构型行（大纲编号开头）——用于目录/连续块识别。"""
    if not s or s.startswith('#') or s.startswith('!['):
        return False
    if _PLAIN_SECTION_RE.match(s) or _PLAIN_CHAPTER_RE.match(s):
        return True
    m = _PLAIN_KP_RE.match(s)
    return bool(m)


def _plain_entry(s: str):
    """纯结构行的 (族, 编号)；不是纯结构行返回 None。"""
    s = _collapse_num_spaces((s or '').strip())
    m = _PLAIN_CHAPTER_RE.match(s)
    if m:
        return ("chapter", _num_from_raw(m.group(1)))
    m = _PLAIN_SECTION_RE.match(s)
    if m:
        return ("section", _num_from_raw(m.group(1)))
    m = _PLAIN_KP_RE.match(s)
    if m:
        return ("kp", _num_from_raw(m.group(1)))
    return None


def _list_block_blocked(lines: list, i: int) -> bool:
    """候选是否身处"目录型清单块"。

    目录/清单的特征不是"旁边有结构行"，而是**连续段（仅空行分隔）本身就是
    一条合法清单**：章 +1，节在新章后从 1 起、同章 +1，知识点在新节后从 1 起、
    同节 +1。残留块（导图文字/页眉复制）序列不合法，不会被误判为目录。
    连续段 ≥3 条且序列合法 → 拒绝提升。
    """
    self_entry = _plain_entry(lines[i])
    if self_entry is None:
        return False
    run = [self_entry]
    # 向左收集
    j = i - 1
    blanks = 0
    while j >= 0:
        s = lines[j].strip()
        if not s:
            blanks += 1
            if blanks > 1:
                break
            j -= 1
            continue
        e = _plain_entry(s) if _is_plain_struct_line(s) else None
        if e is None:
            break
        run.insert(0, e)
        blanks = 0
        j -= 1
    # 向右收集
    j = i + 1
    blanks = 0
    while j < len(lines):
        s = lines[j].strip()
        if not s:
            blanks += 1
            if blanks > 1:
                break
            j += 1
            continue
        e = _plain_entry(s) if _is_plain_struct_line(s) else None
        if e is None:
            break
        run.append(e)
        blanks = 0
        j += 1
    if len(run) < 3:
        return False
    last_ch = last_sec = last_kp = 0
    for fam, num in run:
        if fam == "chapter":
            if last_ch and num != last_ch + 1:
                return False
            last_ch, last_sec, last_kp = num, 0, 0
        elif fam == "section":
            if num != last_sec + 1:
                return False
            last_sec, last_kp = num, 0
        else:
            if num != last_kp + 1:
                return False
            last_kp = num
    return True


def _promote_clean(s: str, tail: str):
    """候选项的"可提升"判定 + 清洗。返回干净文本或 None。

    · 目录页码行（… / 15）不可提升；
    · 短标题允许尾部句读（OCR 残留）：去句读后正文 ≤20 字才接受；
    · 剩余句读/超长 → 视为正文句子，拒绝。
    """
    if _PAGE_SUFFIX_RE.search(s):
        return None
    clean = s
    eff_tail = tail or ''
    if eff_tail and eff_tail[-1] in _PUNCT_END:
        eff_tail = eff_tail.rstrip(_PUNCT_END)
        if len(eff_tail) > _PROMOTE_PUNCT_TAIL_MAX:
            return None
        clean = s[:len(s) - len(tail)] + eff_tail
    if len(clean) > _PROMOTE_LINE_MAX:
        return None
    if eff_tail and len(eff_tail) > _PROMOTE_TAIL_MAX:
        return None
    if any(ch in eff_tail for ch in '。；！？'):
        return None
    return clean


def _promote_titles(lines: list, report: dict) -> int:
    """扫描一遍并按序列连续性提升纯文本标题；返回本遍提升数量。

    迭代调用直到 0（不动点），保证清理与提升的相互影响收敛、normalize 幂等。
    """
    ch_num = 0
    last_sec = 0
    last_kp = 0
    in_fence = False
    in_table = False
    promoted = 0
    warned_lines = {w.get("line") for w in report.get("warnings", [])}

    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        low = s.lower()
        if low.startswith('<table'):
            in_table = True
        if in_table:
            if low.endswith('</table>'):
                in_table = False
            continue
        if not s:
            continue

        # 标题行：推进序列计数，不做提升
        hm = _HEADING_RE.match(s)
        if hm:
            text = hm.group(2).strip()
            cm = _CHAPTER_ANY_RE.match(text)
            if cm:
                ch_num = _num_from_raw(cm.group(1))
                last_sec = 0
                last_kp = 0
                continue
            sm = _SECTION_ANY_RE.match(text)
            if sm:
                last_sec = max(last_sec, _num_from_raw(sm.group(1)))
                last_kp = 0
                continue
            km = _KP_ANY_RE.match(text)
            if km:
                last_kp = max(last_kp, _num_from_raw(km.group(1)))
            continue

        # 纯文本候选（先做编号空格坍缩）
        probe = _collapse_num_spaces(s)
        kind = None
        num = 0
        tail = ''
        m = _PLAIN_SECTION_RE.match(probe)
        if m:
            kind, num, tail = "section", _num_from_raw(m.group(1)), m.group(2)
        else:
            m = _PLAIN_KP_RE.match(probe)
            if m:
                kind, num, tail = "kp", _num_from_raw(m.group(1)), (m.group(2) or '')
            else:
                m = _PLAIN_CHAPTER_RE.match(probe)
                if m:
                    kind, num, tail = "chapter", _num_from_raw(m.group(1)), m.group(2)
        if kind is None:
            continue

        # 目录/清单守卫：连续段构成合法清单（≥3 条）→ 目录/导图清单，不提升；
        # 残留块序列不合法、真实标题旁边零星跟的残留行都不会被误伤
        if _list_block_blocked(lines, i):
            continue
        clean = _promote_clean(probe, tail)
        if clean is None:
            continue

        if kind == "chapter":
            last = ch_num
        elif kind == "section":
            last = last_sec
        else:
            last = last_kp

        if num <= last and last > 0:
            continue  # 重复行：页眉/导图文字/目录，忽略
        if num > last + 2:
            # 大断档：序号离得太远，多半是页眉/导图文字，拒绝提升
            if (i + 1) not in warned_lines:
                report["warnings"].append({
                    "type": f"{kind}_gap",
                    "detail": f"编号断档（上一值 {last} → 本行 {num}）：{clean[:40]}",
                    "line": i + 1,
                })
                warned_lines.add(i + 1)
            continue
        if num > last + 1:
            # 断一档：前面恰好丢过一个标题（被整行删除），本行仍可按序列恢复。
            # 记录告警便于回溯；后面缺失的边界由占位逻辑补齐。
            if (i + 1) not in warned_lines:
                report["warnings"].append({
                    "type": f"{kind}_gap",
                    "detail": f"编号断档（上一值 {last} → 本行 {num}），按序列恢复：{clean[:40]}",
                    "line": i + 1,
                })
                warned_lines.add(i + 1)

        # 序列吻合（或断一档可恢复）→ 提升为标题
        lines[i] = '## ' + clean
        report["recovered"].append({"line": i + 1, "title": clean, "kind": kind})
        promoted += 1
        if kind == "chapter":
            ch_num = num
            last_sec = 0
            last_kp = 0
        elif kind == "section":
            last_sec = num
            last_kp = 0
        else:
            last_kp = num

    return promoted


def normalize_markdown_outline(md_text: str, doc_hint: str = "") -> tuple:
    """解析前置规范化：返回 (新 md_text, 报告)。对结构完好的文档零改动（幂等）。"""
    report = new_report()
    lines = md_text.split('\n')

    # ── Pass 0: 标题行编号空格坍缩 ──
    for i, raw in enumerate(lines):
        m = _HEADING_RE.match(raw.strip())
        if not m:
            continue
        new_text = _collapse_num_spaces(m.group(2).strip())
        if new_text != m.group(2).strip():
            lines[i] = f"{m.group(1)} {new_text}"

    # ── Pass 0.5: 紧邻重复的标题行只保留一条 ──
    # （OCR 双排/页眉残留常产生 "## 第二节 X" 与 "## 第二节 X"（或纯文本变体）相邻，
    #  有时三连甚至更多；一次扫干净，保证幂等）
    for i in range(len(lines)):
        s = lines[i].strip()
        if not s or not _is_title_line(s):
            continue
        keep_heading = s.startswith('#')
        j = i + 1
        blanks = 0
        while j < len(lines):
            t2 = lines[j].strip()
            if not t2:
                blanks += 1
                if blanks > _DUP_GAP_MAX:
                    break
                j += 1
                continue
            if not (_same_title_text(s, t2) and _is_title_line(t2)):
                break
            blanks = 0
            if (not keep_heading) and t2.startswith('#'):
                # 保留标题行：丢靠前的纯文本行
                report["deduped_titles"].append(
                    {"line": i + 1, "text": _strip_marks_prefix(lines[i])})
                lines[i] = ''
                keep_heading = True
            else:
                report["deduped_titles"].append(
                    {"line": j + 1, "text": _strip_marks_prefix(lines[j])})
                lines[j] = ''
            j += 1

    # ── Pass 1: 重复页眉剔除 ──
    # 判定：整行文本作为标题出现 ≥3 次，且与文件名/文档标题有词面重叠
    # （页眉通常呼应书名，如「333 教育综合考研大纲知识清单」「考研政治 核心考案」）。
    # 内容框标题（丹丹攻略/知识导图/课后作业…）与编号标题不进候选。
    heading_counts = Counter()
    for raw in lines:
        m = _HEADING_RE.match(raw.strip())
        if m:
            heading_counts[m.group(2).strip()] += 1
    hint_tokens = _hint_tokens(doc_hint)
    drop_texts = set()
    for text, cnt in heading_counts.items():
        if cnt < 3 or len(text) < 4:
            continue
        if is_box_title(text) or has_any_numbering(text):
            continue
        if not _overlaps_hint(text, hint_tokens):
            continue
        drop_texts.add(text)
    if drop_texts:
        for i, raw in enumerate(lines):
            m = _HEADING_RE.match(raw.strip())
            if m and m.group(2).strip() in drop_texts:
                lines[i] = ''
        for text in sorted(drop_texts):
            report["dropped_headers"].append({"text": text, "count": heading_counts[text]})

    # ── Pass 2a: 裸「## 第X章」+ 下一非空纯文本行 → 章名补全 ──
    for i, raw in enumerate(lines):
        s = raw.strip()
        m = _BARE_CHAPTER_HEADING_RE.match(s)
        if not m:
            continue
        nxt_raw = _next_nonempty(lines, i)
        if not nxt_raw or _HEADING_RE.match(nxt_raw):
            continue  # 下一行是标题：交给建树期的标题合并处理
        if nxt_raw.startswith('![') or nxt_raw.startswith('[') or _is_plain_struct_line(nxt_raw):
            continue
        if is_box_title(nxt_raw):
            continue
        if len(nxt_raw) > _SUBTITLE_MAX or nxt_raw.endswith(tuple(_PUNCT_END)):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        lines[i] = s + ' ' + nxt_raw
        lines[j] = ''
        report["chapter_titles"].append({"line": i + 1, "to": lines[i]})

    # ── Pass 2b: 正文里带章号的章名行回填（如「## 第五章」…「第五章 理学教育…」）──
    bare_idx = None
    bare_prefix = ''
    cur_ch = 0
    for i, raw in enumerate(lines):
        s = raw.strip()
        m = _BARE_CHAPTER_HEADING_RE.match(s)
        if m:
            bare_idx, bare_prefix = i, m.group(1)
            cur_ch = _num_from_raw(m.group(2))
            continue
        hm = _HEADING_RE.match(s)
        if hm:
            cm = _CHAPTER_ANY_RE.match(hm.group(2).strip())
            if cm:
                bare_idx = None
                cur_ch = _num_from_raw(cm.group(1))
            continue
        if bare_idx is None:
            continue
        pm = _PLAIN_CHAPTER_RE.match(_collapse_num_spaces(s))
        if pm and _num_from_raw(pm.group(1)) == cur_ch:
            clean = _promote_clean(_collapse_num_spaces(s), pm.group(2))
            if clean:
                lines[bare_idx] = f"{bare_prefix} {clean}"
                lines[i] = ''
                report["chapter_titles"].append({"line": bare_idx + 1, "to": lines[bare_idx]})
                bare_idx = None

    # ── Pass 3: 大纲标题恢复（序列连续性驱动，迭代到不动点）──
    # 提升会改变邻域（残留行被清掉、标题行不再计入密度），少数情况下
    # 首轮被密度守卫拦下的真标题在清理后应当恢复 —— 迭代到没有新提升为止，
    # 保证 normalize 幂等（第二次运行不再产生任何恢复）。
    for _ in range(6):
        if _promote_titles(lines, report) == 0:
            break

    return '\n'.join(lines), report


# =============================================================================
# 骨架不变式：同级序列校验
# =============================================================================

def _check_family(children: list, family: str, path: list, violations: list):
    """同一父节点下，某编号族的严格递增检查。"""
    prev = None
    prev_title = None
    for c in children:
        title = (c.get("title") or "").strip()
        if family == "chapter":
            m = _CHAPTER_ANY_RE.match(title)
        elif family == "section":
            m = _SECTION_ANY_RE.match(title)
        else:
            m = _KP_ANY_RE.match(title)
        if not m:
            continue
        num = _num_from_raw(m.group(1))
        if prev is not None:
            if family == "kp" and num <= prev:
                same = (num == prev and prev_title is not None
                        and _kp_text_after_num(prev_title) == _kp_text_after_num(title))
                if same:
                    violations.append(_v("kp_duplicate", "warn", path, title,
                                         f"同号同名知识点重复：{num}（重复行，无需分隔）"))
                else:
                    # 知识点序列重启 = 缺失小节边界的确定性证据（可自动分隔）
                    violations.append(_v("kp_restart", "error", path, title,
                                         f"知识点编号 {num} <= 上一值 {prev}，疑似缺失小节边界"))
            elif num == prev:
                violations.append(_v(f"{family}_duplicate", "warn", path, title,
                                     f"同级编号重复：{num}（可能出现两处同名结构）"))
            elif num < prev:
                violations.append(_v(f"{family}_regression", "warn", path, title,
                                     f"同级编号回退：{prev} → {num}"))
            elif num > prev + 1:
                violations.append(_v(f"{family}_gap", "warn", path, title,
                                     f"同级编号断档：{prev} → {num}（可能丢了一个标题）"))
        prev = num
        prev_title = title


def check_skeleton(roots: list) -> list:
    """扫描整棵树，返回同级序列违规列表（不修改树）。

    严重级：
      error —— 知识点序列重启（<= 上一值）：缺失边界的确定性证据，可自动分隔；
      warn  —— 断档 / 重复 / 回退 / 同号同名重复，只报告不修（不猜标题）。
    """
    violations = []

    def _walk(node, path):
        children = node.get("children") or []
        here = path + [node.get("title") or ""]
        if children:
            _check_family(children, "chapter", here, violations)
            _check_family(children, "section", here, violations)
            _check_family(children, "kp", here, violations)
            # 章下知识点与节节点平级共存 → 疑似缺节边界
            if _CHAPTER_ANY_RE.match((node.get("title") or "").strip()):
                has_kp = any(_KP_ANY_RE.match((c.get("title") or "").strip()) for c in children)
                has_sec = any(_SECTION_ANY_RE.match((c.get("title") or "").strip()) for c in children)
                if has_kp and has_sec:
                    violations.append(_v(
                        "kp_without_section", "warn", here, node.get("title") or "",
                        "知识点与节节点平级，可能存在缺失的节边界"))
        for c in children:
            _walk(c, here)

    for r in roots or []:
        _walk(r, [])
    return violations


# =============================================================================
# 边界修复：知识点序列重启 → 占位节点（并提升到正确层级）
# =============================================================================

def repair_boundaries(roots: list) -> dict:
    """修复"缺失边界"导致的错挂（就地修改）。

    规则：
      · 节内知识点序列重启 → 补占位，并把占位**提升为该节的兄弟**
        （缺的是"节"，占位不能深埋在上一节里）；
      · 章下直挂知识点且该章也有节（首节标题丢失）→ 章内知识点按重启
        切分，逐段包进占位；占位与节同级；
      · 同号同名知识点（重复行）→ 只记 duplicates，不产生占位。
    """
    report = {"placeholders": [], "moved": [], "duplicates": []}
    created_ids = {}

    def make_ph(node) -> dict:
        ph = {"title": PLACEHOLDER_TITLE, "content": "", "children": []}
        lvl = node.get("level")
        if isinstance(lvl, int):
            ph["level"] = lvl + 1
        created_ids[id(ph)] = True
        return ph

    def _wrap_chapter_kp_runs(node: dict, children: list) -> list:
        """章下直挂知识点（且该章有节）→ 按序列重启切分，包进占位。"""
        new = []
        ph = None
        last = None
        for c in children:
            title = (c.get("title") or "").strip()
            m = _KP_ANY_RE.match(title)
            if not m:
                new.append(c)
                ph = None
                last = None
                continue
            num = _num_from_raw(m.group(1))
            if ph is None or (last is not None and num <= last):
                ph = make_ph(node)
                new.append(ph)
                report["placeholders"].append({
                    "parent": node.get("title") or "",
                    "at": title,
                    "path": " > ".join([p for p in path_stack + [node.get("title") or ""] if p]),
                })
                last = None
            last = num
            ph["children"].append(c)
            report["moved"].append({"title": title, "into": PLACEHOLDER_TITLE})
        return new

    path_stack = []

    def _walk(node: dict):
        path_stack.append(node.get("title") or "")
        children = node.get("children") or []
        title = (node.get("title") or "").strip()

        # 章下直挂知识点：按重启切分包占位。
        # 触发条件（二者之一）：
        #   · 该章还有节节点（首节标题丢失，知识点直接挂到章下）；
        #   · 直挂知识点序列出现重启（即使全章节标题都丢了，
        #     重启本身也足以证明这里缺至少一个节边界）。
        if children and _CHAPTER_ANY_RE.match(title):
            has_sec = any(_SECTION_ANY_RE.match((c.get("title") or "").strip()) for c in children)
            kp_nums = []
            for c in children:
                m = _KP_ANY_RE.match((c.get("title") or "").strip())
                if m:
                    kp_nums.append(_num_from_raw(m.group(1)))
            restart = any(b <= a for a, b in zip(kp_nums, kp_nums[1:]))
            if kp_nums and (has_sec or restart):
                node["children"] = children = _wrap_chapter_kp_runs(node, children)

        # 通用扫描：知识点序列重启 → 就地建占位（稍后按需提升）
        new_children = []
        container = None
        last_kp = None
        last_kp_text = None
        changed = False
        for c in children:
            ctitle = (c.get("title") or "").strip()
            m = _KP_ANY_RE.match(ctitle)
            num = _num_from_raw(m.group(1)) if m else None
            kptext = _kp_text_after_num(ctitle) if m else None
            if num is not None and last_kp is not None and num <= last_kp:
                if num == last_kp and kptext is not None and last_kp_text == kptext:
                    report["duplicates"].append({"parent": node.get("title") or "", "title": ctitle})
                else:
                    container = make_ph(node)
                    new_children.append(container)
                    report["placeholders"].append({
                        "parent": node.get("title") or "",
                        "at": ctitle,
                        "path": " > ".join([p for p in path_stack if p]),
                    })
                    last_kp = None
                    last_kp_text = None
                    changed = True
            if num is not None:
                last_kp = num
                last_kp_text = kptext
            if container is not None:
                container["children"].append(c)
                report["moved"].append({"title": ctitle, "into": PLACEHOLDER_TITLE})
            else:
                new_children.append(c)
        if changed:
            node["children"] = new_children

        for c in node.get("children") or []:
            _walk(c)
        path_stack.pop()

    for r in roots or []:
        _walk(r)

    # ── 提升：节内部创建的占位 → 提升为该节的兄弟（缺的是"节"层级）──
    def _hoist(nodes: list):
        for x in list(nodes):
            kids = x.get("children") or []
            if kids:
                _hoist(kids)
        i = 0
        while i < len(nodes):
            x = nodes[i]
            kids = x.get("children") or []
            xt = (x.get("title") or "").strip()
            if (kids and _SECTION_ANY_RE.match(xt) and not _CHAPTER_ANY_RE.match(xt)):
                phs = [c for c in kids if id(c) in created_ids]
                if phs:
                    x["children"] = [c for c in kids if id(c) not in created_ids]
                    for k, ph in enumerate(phs):
                        nodes.insert(i + 1 + k, ph)
                    i += len(phs)
            i += 1

    _hoist(roots)
    return report


def audit_tree_skeleton(roots: list) -> dict:
    """check → repair → check。返回统一结构的报告（解析/增强前置共用）。"""
    before = check_skeleton(roots)
    rep = repair_boundaries(roots)
    after = check_skeleton(roots)
    out = new_report()
    out["placeholders"] = rep.get("placeholders", [])
    out["duplicates"] = rep.get("duplicates", [])
    out["warnings"] = [v for v in before if v.get("severity") == "warn"]
    out["violations_before"] = len(before)
    out["violations_after"] = len(after)
    out["errors_before"] = sum(1 for v in before if v.get("severity") == "error")
    out["errors_after"] = sum(1 for v in after if v.get("severity") == "error")
    out["samples"] = before[:8]
    return out


def merge_reports(reports: list) -> dict:
    """把多个阶段的骨架报告合并成一份（供解析报告面板使用）。"""
    out = new_report()
    for r in reports or []:
        if not r:
            continue
        for k in ("recovered", "dropped_headers", "chapter_titles",
                  "deduped_titles", "duplicates", "placeholders", "warnings"):
            out[k].extend(r.get(k) or [])
        out["samples"].extend(r.get("samples") or [])
        for k in ("violations_before", "violations_after", "errors_before", "errors_after"):
            out[k] += int(r.get(k) or 0)
    out["samples"] = out["samples"][:8]
    return out


if __name__ == "__main__":
    import json
    import os
    import sys

    if len(sys.argv) > 1 and sys.argv[1].endswith('.md'):
        path = sys.argv[1]
        text = open(path, encoding='utf-8').read()
        new_text, rep = normalize_markdown_outline(
            text, doc_hint=os.path.basename(path))
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("usage: python outline_skeleton.py <file.md>")
