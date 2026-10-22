#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识树"结构审校"层 —— 在 AI 增强环节内解决解析遗留的顺序/命名缺陷。

设计原则（与用户约定一致）：

1. **LLM 直接输出新结构**，但只输出"顺序"和"要改的标题"两类决定；
   LLM 不允许增删节点、不允许改动正常标题。
2. **代码执行 + 严格校验**：LLM 用**索引**而非标题回传（标题可能重复、也可能被
   LLM 顺手改写导致失配），返回的 order 必须是同一批索引的一个合法排列，
   否则整组丢弃、当组不发生任何变化。
3. **重命名只针对"坏标题"**：markdown 残留、纯编号、OCR 噪声、异常超长。
   语义正常的标题一律不送 LLM，也一律不改——这是为了防止模型曲解原文。
4. **绝不增删节点**：审校改变的是"呈现顺序"和"坏标题的文字"，
   树的信息量与原文的一致性由调用方保证。

本模块只依赖标准库 + httpx，可在无 API key 时纯确定性降级运行。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app_settings import chat_body_extras, chat_headers   # noqa: E402  （必须在 sys.path 处理之后）

# ────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────

# 一次送 LLM 的兄弟节点组上限，防止 prompt 过长
MAX_SIBLINGS_PER_CALL = 40
# 一次送 LLM 的父节点组数（并发单位）
CONCURRENCY = 3
# LLM 调用预算：单棵树最多审校多少个父节点组，防止失控
MAX_GROUPS_PER_TREE = 400
# 判定"坏标题"的最大长度
TITLE_MAX_LEN = 60
# 组内节点数低于该值时不做 LLM 审校（小组排序确定性规则已经够用）
MIN_GROUP_FOR_LLM = 2

REQUEST_TIMEOUT = 30
MAX_RETRY = 2

RESERVED_FIELDS = ("group_id", "member_index", "_audit_hint")


# _src_index 兜底哨兵：缺失时排最后，且不影响其他节点的相对顺序
_SRC_INDEX_MISSING = 1 << 30


# ────────────────────────────────────────────
# 编号识别（与 benchmark_structure / hierarchy_repair 同一套口径）
# ────────────────────────────────────────────

_CIRCLE = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'


def _numbering_info(title: str):
    """→ (numbering_type, number) 或 (None, None)。"""
    from hierarchy_repair import (NUMBER_PATTERNS, normalize_numbering,
                                  chinese_to_int, circle_to_int)
    t = normalize_numbering((title or "").strip())
    for name, pat in NUMBER_PATTERNS.items():
        m = pat.match(t)
        if m:
            raw = (m.group(1) or "").strip()
            if "." in raw:
                tail = raw.rsplit(".", 1)[-1]
                n = int(tail) if tail.isdigit() else 0
            elif len(raw) == 1 and raw in _CIRCLE:
                n = circle_to_int(raw)
            elif raw.isdigit():
                n = int(raw)
            else:
                n = chinese_to_int(raw)
            return name, n
    return None, None


def _src_index(node: dict) -> int:
    try:
        return int(node.get("src_index"))
    except (TypeError, ValueError):
        return _SRC_INDEX_MISSING


# ────────────────────────────────────────────
# 坏标题判定（判定为真才允许重命名）
# ────────────────────────────────────────────

_MD_RESIDUE = re.compile(r'(\*\*|__|`{1,3}|^#{1,6}\s|\]\(|<[a-zA-Z/])')
_OCR_NOISE = re.compile(r'[�]|[\x00-\x08\x0b\x0c\x0e-\x1f]')


def bad_title_reason(title: str) -> str:
    """非空 = "这个标题坏到需要修"。正常标题返回空串，绝不送 LLM 改名。"""
    t = (title or "").strip()
    if not t:
        return "空标题"
    if _MD_RESIDUE.search(t):
        return "markdown残留"
    ntype, _ = _numbering_info(t)
    if ntype:
        from hierarchy_repair import NUMBER_PATTERNS, normalize_numbering
        pat = NUMBER_PATTERNS[ntype]
        m = pat.match(normalize_numbering(t))
        tail = t[m.end():].strip() if m else t
        if len(tail) < 2:
            return "纯编号无标题"
    if _OCR_NOISE.search(t):
        return "OCR噪声字符"
    if len(t) > TITLE_MAX_LEN:
        return "标题过长"
    return ""


# ────────────────────────────────────────────
# 确定性审校：判断哪些兄弟组"确实有问题"
# ────────────────────────────────────────────

def deterministic_fix_group(members: list) -> bool:
    """就地按编号顺序修正一组兄弟节点（仅重排中间列表）。

    注意：只重排传入的列表，**不写回父节点的 children**——那一步交给
    deterministic_audit/_fix_index_group 按索引完成，否则父节点顺序不变、
    等于没改。保留此函数是为了单独调试某个兄弟组。
    """
    fam, _ = _numbering_info(members[0].get("title", "")) if members else (None, None)
    if fam is None:
        keys = [_src_index(c) for c in members]
        if keys == sorted(keys):
            return False
        members.sort(key=_src_index)
        return True
    keys = [_numbering_info(c.get("title", ""))[1] for c in members]
    if keys == sorted(keys):
        return False
    order = sorted(range(len(members)), key=lambda i: keys[i])
    members[:] = [members[i] for i in order]
    return True


def collect_audit_groups(tree: dict, limit: int = MAX_GROUPS_PER_TREE):
    """收集需要 LLM 审校的父节点组。

    有疑点才交给 LLM：编号重复、编号断档、或组内存在坏标题。
    "单纯顺序乱"不送——确定性排序已经能修，交给模型反而可能自作主张改错。

    注意：必须在确定性修正**之前**调用。修正会就地重排 children，
    之后再来收集，看到的就只剩"已经排好的组"，模型再也看不到原本错的样子，
    编号重复/断档这类"拍了平"的结构线索也会一并消失。
    """
    groups = []

    def _walk(parent: dict):
        children = parent.get("children") or []
        if len(children) >= MIN_GROUP_FOR_LLM:
            for fam, idxs in _group_indices(children):
                members = [children[i] for i in idxs]
                if fam is not None:
                    nums = [_numbering_info(c.get("title", ""))[1] for c in members]
                    uniq = sorted(set(nums))
                    duplicated = len(set(nums)) != len(nums)
                    gapped = bool(uniq) and uniq[0] == 1 and uniq[-1] > 1 \
                        and len(uniq) != uniq[-1]
                else:
                    duplicated = gapped = False
                has_bad = any(bad_title_reason(c.get("title", "")) for c in members)
                if duplicated or gapped or has_bad:
                    groups.append(_Group(
                        parent, children, idxs, fam, members,
                        reason=("duplicate" if duplicated else "")
                               + (" gap" if gapped else "")
                               + (" bad_title" if has_bad else "")))
        for child in children:
            _walk(child)

    # 从根节点本身开始：根节点的兄弟组（多个来源 PDF 的并列）同样要审
    _walk({"title": "", "children": tree.get("children") or []})
    return groups[:limit]


def _sibling_title(node: dict) -> str:
    return node.get("title", "")


class _Group:
    """一个"待审校的兄弟组"：知道自己在父节点 children 里的哪些槽位。

    apply_order 必须写回这些槽位——只重排临时列表的话，LLM 的重排结论会
    无声无息丢掉，而改名会落在已经移动过的位置上，改到别的节点上。
    """

    __slots__ = ("parent", "children", "slots", "family", "members", "_reason")

    def __init__(self, parent, children, slots, family, members, reason=""):
        self.parent = parent
        self.children = children
        self.slots = list(slots)
        self.family = family
        self.members = list(members)
        self._reason = reason

    @property
    def parent_title(self):
        return (self.parent.get("title") or "")[:60]

    @property
    def reason(self):
        return self._reason

    def apply_order(self, order):
        """把 order（成员下标的一个排列）写回父节点的 children。"""
        picked = [self.members[i] for i in order]
        for slot, node_obj in zip(self.slots, picked):
            self.children[slot] = node_obj
        # 同步 members，让后续"改名"用的下标与 prompt 里展示的一致
        self.members[:] = picked


class _SubGroup(_Group):
    """组过大时只审前 MAX 个成员：**禁止重排**，只允许改名。

    原因：局部重排只动前 MAX 个槽位，后面的兄弟相对位置变得不可预期，
    风险大于收益。因此 apply_order 置空，改写逻辑会跳过重排这一步。
    """

    def __init__(self, src, members):
        super().__init__(src.parent, src.children, src.slots[:len(members)],
                         src.family, members, src.reason)
        self.apply_order = None


def _group_indices(children: list):
    """→ [(family, [index, ...]), ...]，按首次出现顺序分组。"""
    buckets = defaultdict(list)
    for idx, child in enumerate(children):
        fam, _num = _numbering_info(child.get("title", ""))
        buckets[fam].append(idx)
    return list(buckets.items())


# ────────────────────────────────────────────
# LLM 审校：LLM 输出新结构，代码校验后执行
# ────────────────────────────────────────────

def build_group_prompt(group: dict) -> str:
    """给 LLM 的提示：只让它排顺序 + 改坏标题。"""
    members = group["members"]
    lines = []
    for i, c in enumerate(members):
        title = (c.get("title") or "").strip()
        reason = bad_title_reason(title)
        hint = ""
        if reason == "纯编号无标题":
            # 这类标题的正文被 OCR 拆到了下一行，成了它的子节点。
            # 把第一个子节点的标题交给模型，它才有材料把名字补全。
            kids = [k for k in (c.get("children") or [])
                    if (k.get("title") or "").strip()]
            if kids:
                hint = f"   [第一个子标题：{kids[0]['title'].strip()[:30]}；"
                hint += "可用它补全本节点名称]"
        if reason and not hint:
            hint = f"   [标题需修正：{reason}]"
        lines.append(f"{i}. {title}{hint}")

    numbered = group["family"] is not None
    if numbered:
        order_rule = ("这些节点使用同一套编号。请按编号的先后顺序给出正确顺序，"
                      "编号重复或断档时按内容语义判断它们本来的先后；"
                      "若某几项明显是上一项展开的细目（不是并列项），"
                      "请把顺序调整为细目紧跟其父项。")
    else:
        order_rule = ("这些节点没有编号。请按内容逻辑顺序排列"
                      "（例如：概述→分点→对比→总结；或时间先后；或由浅入深）。"
                      "若当前顺序已经合理，就原样返回，不要为了变化而变化。")

    return f"""你是教育类教材的知识结构审校员。下面是一组**兄弟节点**（同属一个父节点）。

父节点：{group['parent_title']}
编号体系：{group['family'] or '无编号'}
疑似问题：{group['reason'] or '待审'}

节点列表：
{chr(10).join(lines)}

请完成两件事：
1. **顺序**：{order_rule}
2. **改名**：只对标注了"标题需修正"或"第一个子标题"的节点给出更准确的标题。
   没有标注的节点**不要改**。
   改名要求：保留原编号前缀和原意，只清理残留、补全被截断的表述；不许改变知识含义。
   对"纯编号无标题"的节点，请用给出的第一个子标题把名称补全，
   例如「知识点四」+ 子标题「希腊化时期的教育」→「知识点四 希腊化时期的教育」。

只输出 JSON，格式：
{{"order": [0,1,2,...], "renames": {{"0": "改后的标题"}}}}
- order 是上述全部索引的一个排列（每个索引用且只用一次）。
- renames 的键是索引字符串，值是新标题；无需改名的节点不要出现在 renames 里。
- 编号节点的新标题**必须保留原有编号前缀**。"""


def parse_group_response(raw: str, size: int) -> tuple:
    """解析并校验 LLM 返回。→ (order_list or None, renames dict)

    order 必须是一个合法排列；否则返回 None（交给确定性规则处理）。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    data = None
    try:
        data = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except Exception:
                data = None
    if not isinstance(data, dict):
        return None, {}

    order = data.get("order")
    if not isinstance(order, list):
        return None, {}
    try:
        # 注意：int() 对 float("inf") 抛的是 OverflowError，不是 ValueError，
        # 必须一并接住，否则一个畸形 JSON 就能把整个审校阶段打挂。
        idx = [int(x) for x in order]
    except (TypeError, ValueError, OverflowError):
        return None, {}
    if len(idx) != size or sorted(idx) != list(range(size)):
        return None, {}
    if idx == list(range(size)):
        # 明说不用改，就别再走下面的写入
        order_list = list(range(size))
    else:
        order_list = idx

    renames = {}
    raw_renames = data.get("renames")
    if isinstance(raw_renames, dict):
        for k, v in raw_renames.items():
            try:
                ki = int(k)
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 <= ki < size and isinstance(v, str) and v.strip():
                renames[ki] = v.strip()
    return order_list, renames


def _numbering_prefix(title: str) -> str:
    """标题的编号前缀（如 "（一）"、"1.2"）；无编号返回空串。"""
    t = (title or "").strip()
    fam, _ = _numbering_info(t)
    if not fam:
        return ""
    from hierarchy_repair import NUMBER_PATTERNS, normalize_numbering
    m = NUMBER_PATTERNS[fam].match(normalize_numbering(t))
    return m.group(0).strip() if m else ""


_STRUCT_TOKEN_RE = re.compile(
    r'(第[一二三四五六七八九十百\d]+[章节部分]|知识点[一二三四五六七八九十\d]+)')


def _struct_token_count(text: str) -> int:
    """标题里的结构 token 数（第X章节/知识点X）。"""
    return len(_STRUCT_TOKEN_RE.findall(text or ""))


def _rename_allowed(old: str, new: str) -> bool:
    """是否接受这次改名。

    关键约束：**有编号前缀的标题必须保留编号**。模型若把"（一）教育目的"改成
    "教育目的"，这个节点就会掉出它所属的编号族，父节点下原本 1..4 的编号序列
    当场出现断档，比原标题更糟。因此这里要求新旧前缀"编号值一致"。
    """
    # 骨架守卫：改名不得引入新的结构标题 token。
    # 防「第五章 → 第五章 第一节 科举制度的演变和文教政策的发展」这类
    # 把相邻节标题吞进章标题的"修复"。
    if _struct_token_count(new) > _struct_token_count(old):
        return False
    old_p, new_p = _numbering_prefix(old), _numbering_prefix(new)
    if not old_p:
        return True                      # 原标题无编号，随便改
    if not new_p:
        return False                     # 原标题有编号、改丢了 → 拒绝
    return _numbering_info(new_p)[1] == _numbering_info(old_p)[1] \
        and _numbering_info(new_p)[0] == _numbering_info(old_p)[0]


def _sanitize_new_title(old: str, new: str) -> str:
    """新标题兜底清理：去掉残留语法、控制字符、压缩空白，超长截断。"""
    t = (new or "").strip()
    t = re.sub(r'^\s*#{1,6}\s+', '', t)          # markdown 标题井号
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)  # [文字](链接) → 文字
    t = re.sub(r'<[^>]{1,20}>', ' ', t)          # <br> / <b> 等标签
    t = t.replace("**", "").replace("__", "")
    t = t.replace("`", "")
    t = _CTRL_CHARS.sub('', t)                   # 控制字符直接删
    t = re.sub(r'\s+', ' ', t).strip(' -—:：,，、')
    if not t:
        return old
    if len(t) > TITLE_MAX_LEN:
        t = t[:TITLE_MAX_LEN].rstrip(" ，,、;；")
    return t


_CTRL_CHARS = re.compile(r'[\x00-\x1f\x7f�]')


async def audit_group_with_llm(client, url, api_key, model, group, usage=None) -> bool:
    """对一个父节点组调用 LLM，就地应用顺序/改名。失败返回 False。

    group 是 _Group：order 会通过 apply_order 写回父节点 children 的对应槽位
    （只改临时列表的话，重排会被静默丢弃，改名也会落到错位的节点上）。
    """
    members = group.members
    if len(members) > MAX_SIBLINGS_PER_CALL:
        # 组太大时只审前 MAX 个，且**不重排**这一组：局部重排会把后面的
        # 兄弟挤到不确定的位置，风险大于收益。
        members = members[:MAX_SIBLINGS_PER_CALL]
        group = _SubGroup(group, members)
    size = len(members)

    prompt = build_group_prompt({
        "parent_title": group.parent_title,
        "family": group.family,
        "reason": group.reason,
        "members": members,
    })
    payload = {
        "model": model,
        "max_tokens": 1500,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
        **chat_body_extras(url),
    }
    last = ""
    for attempt in (1, 2):
        try:
            resp = await client.post(
                url, json=payload,
                headers=chat_headers(url, api_key),
                timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                last = f"HTTP {resp.status_code}"
                if resp.status_code == 429:
                    await asyncio.sleep(3 * attempt)
                continue
            if usage is not None:
                usage.from_response(resp, model)
            content = resp.json()["choices"][0]["message"]["content"]
            order, renames = parse_group_response(content, size)
            if order is None:
                last = "非法排列"
                continue
            reordered = order != list(range(size))
            if reordered and group.apply_order:
                group.apply_order(order)
            applied = 0
            taken = {(_sibling_title(c) or "").strip()
                     for i, c in enumerate(members) if i not in renames}
            for i, new in renames.items():
                node = members[i]
                old = (node.get("title") or "").strip()
                if not bad_title_reason(old):
                    continue
                fixed = _sanitize_new_title(old, new)
                if not fixed or fixed == old:
                    continue
                if not _rename_allowed(old, fixed):
                    # 模型弄丢了编号前缀：这次改名会让该节点掉出编号族，
                    # 造成新的断档，比原标题更糟，宁可不改。
                    print(f"  [改名拒绝] {old[:24]!r} → {fixed[:24]!r}（编号前缀不符）")
                    continue
                if fixed.strip() in taken:
                    # 避免模型把两个兄弟改成同名（会触发去重逻辑删节点）
                    print(f"  [改名拒绝] → {fixed[:24]!r}（与兄弟节点重名）")
                    continue
                node["title"] = fixed
                taken.add(fixed.strip())
                applied += 1
            return reordered or applied > 0
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            break
    if last:
        print(f"  [审校跳过] {group.parent_title} — {last}")
    return False


async def audit_groups_with_llm(groups: list, url: str, api_key: str,
                               model: str, on_progress=None, usage=None) -> dict:
    """对 collect_audit_groups 收集到的疑点组逐个调用 LLM，并就地应用结果。"""
    import httpx

    report = {"llm_groups": 0, "attempted": len(groups)}
    if not groups:
        return report

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _one(client, g):
        async with sem:
            # 用"父节点整层子标题"做前后对比：只看 g.members 会漏判
            # 重排是否真的写回了 children（曾经正是因此误报成功）。
            before = list(g.children)
            changed = await audit_group_with_llm(client, url, api_key, model, g, usage)
            if changed and g.children != before:
                report["llm_groups"] += 1

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        await asyncio.gather(*[_one(client, g) for g in groups])
    return report


async def audit_tree(tree: dict, url: str, api_key: str, model: str,
                     on_progress=None, usage=None) -> dict:
    """一次性完成"收集 → LLM 审校"（不含量确定性修正）。

    保留给想自己控制修正时机的调用方；audit_book_tree 走的是
    "先收集 → 确定性修正 → LLM 兜底"的顺序，那份才是推荐入口。
    """
    groups = collect_audit_groups(tree)
    return await audit_groups_with_llm(groups, url, api_key, model, on_progress, usage)


def deterministic_audit(tree: dict) -> int:
    """全树确定性顺序修正，返回调整过的兄弟组数。

    按"父节点内各编号族"分组，只在组内重排，并通过索引写回父节点的
    children——成员是同一批 dict 对象，只重排中间列表不会改变父节点顺序。
    """
    count = 0

    def _walk(children: list):
        nonlocal count
        if len(children) >= 2:
            for _fam, idxs in _group_indices(children):
                if len(idxs) < 2:
                    continue
                if _fix_index_group(children, idxs):
                    count += 1
        # 无论本层有几个子节点，都要继续往下走
        for child in children:
            _walk(child.get("children") or [])

    # 根那一层也要走：多个来源 PDF 并列在根下，顺序同样可能错
    _walk(tree.get("children") or [])
    return count


def _fix_index_group(children: list, idxs: list) -> bool:
    """按编号顺序重排 children 中 idxs 指定的那些位置；是否发生调整。"""
    fam, _ = _numbering_info(children[idxs[0]].get("title", ""))
    if fam is None:
        keys = [_src_index(children[i]) for i in idxs]
        if keys == sorted(keys):
            return False
    else:
        keys = []
        for i in idxs:
            _t, num = _numbering_info(children[i].get("title", ""))
            keys.append(num)
        if keys == sorted(keys):
            return False
    order = [idxs[j] for j in sorted(range(len(idxs)), key=lambda k: keys[k])]
    picked = [children[i] for i in order]
    for slot, node_obj in zip(idxs, picked):
        children[slot] = node_obj
    return True


def count_bad_titles(tree: dict) -> int:
    n = 0

    def _walk(node: dict):
        nonlocal n
        if bad_title_reason(node.get("title", "")):
            n += 1
        for c in node.get("children") or []:
            _walk(c)

    # 含根本身：根标题若也要审校，不该被漏计
    _walk(tree)
    return n


def stamp_seq(tree: dict) -> None:
    """审校之后写入 seq_index，让下游的 cleanup_tree_structure 沿用审校结论。

    必要性：node_enhancer._normalize_children_order 对"无编号"兄弟组的兜底策略是
    按索引恢复原有序。若不留下"最终序"的记录，LLM 对无编号组做的顺序调整会在
    那一轮被原样回退，等于白改。

    这里**只写 seq_index，不动 src_index**：src_index 始终是文档原始顺序，
    一旦需要对审校结论纠错，仍可依据它回到原文次序。
    """
    counter = 0

    def _walk(node: dict):
        nonlocal counter
        node["seq_index"] = counter
        counter += 1
        for c in node.get("children") or []:
            _walk(c)

    # 从根开始（根自身也编上号），保证整棵树的 seq_index 连续无洞
    _walk(tree)


async def audit_book_tree(tree: dict, url: str = "", api_key: str = "",
                           model: str = "", on_progress=None, usage=None) -> dict:
    """审校入口（异步）：确定性修正 → LLM 兜底编号重复/断档、改写"坏标题"
    → 写下最终序。返回可读的报告。

    同步调用方请用 asyncio.run(audit_book_tree(...))；
    已在事件循环内（如 server._enhance_tree）时直接 await。
    """
    report = {
        "deterministic_groups": 0, "llm_groups": 0,
        "bad_titles_before": 0, "bad_titles_after": 0,
    }
    report["bad_titles_before"] = count_bad_titles(tree)

    # 先在"原样"的树上收集疑点组：确定性修正会就地重排，
    # 之后收集就看不到编号重复/断档这些本该送去问模型的问题了。
    llm_groups = []
    if url and api_key:
        llm_groups = collect_audit_groups(tree)

    report["deterministic_groups"] = deterministic_audit(tree)

    if url and api_key and llm_groups:
        report["groups"] = len(llm_groups)
        res = None
        try:
            # _enhance_tree 本身跑在事件循环里，此时不能再 asyncio.run；
            # 因此优先 await 协程，仅在同步调用方（脚本/CLI）时才自建循环。
            res = await audit_groups_with_llm(llm_groups, url, api_key,
                                              model, on_progress, usage)
        except RuntimeError as e:
            if "no running event loop" not in str(e):
                print(f"  [审校跳过] LLM 审校失败: {e}")
            else:
                import asyncio
                try:
                    res = asyncio.run(audit_groups_with_llm(
                        llm_groups, url, api_key, model, on_progress, usage))
                except Exception as e2:
                    print(f"  [审校跳过] LLM 审校失败: {e2}")
        except Exception as e:
            print(f"  [审校跳过] LLM 审校失败: {e}")
        if res:
            report["llm_groups"] = res.get("llm_groups", 0)

    stamp_seq(tree)
    report["bad_titles_after"] = count_bad_titles(tree)
    return report


if __name__ == "__main__":
    # 无参运行：对所有已处理任务做一次"确定性审校"演练，报告可修项数
    import glob
    files = sorted(glob.glob(os.path.join(
        BASE_DIR, "data", "_temp_uploads", "*", "json", "knowledge_tree.json")))
    if not files:
        print("未找到知识树文件")
        sys.exit(1)
    total = 0
    for fp in files:
        tree = json.load(open(fp, encoding="utf-8"))
        n = deterministic_audit(tree)
        bad = count_bad_titles(tree)
        total += n
        print(f"{os.path.basename(os.path.dirname(os.path.dirname(fp)))}: "
              f"确定性修正 {n} 组 | 剩余待审坏标题 {bad} 个")
    print(f"\n合计确定性修正 {total} 组")
