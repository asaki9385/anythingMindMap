#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主题归属审校 —— 解析阶段的「内容错位」防护（确定性，无 AI）。

解决什么问题
------------
备考资料里有一类反复出现的结构性错误：**某个主题的内容被合成到另一个
主题的节点下面**。典型形态：

· 提示框跨主题收纳内容 —— 韩愈章节末尾的「丹丹攻略」框里第一条其实是
  「颜之推家庭教育思想的启示」，随编号续排挂进了韩愈的「（三）论尊师
  重道」下；
· OCR 分页/版面错乱时，整段小节挂到了相邻主题下。

这类错误的根源在建树：只要排版上"跟着"某个主题，就会被挂进去，增强阶段
的审校管的是乱序/重复编号，不判断"内容属于谁"。本模块在**建树完成、增强
之前**做一次归属审校，把明确错位的节点移回它自己的主题下。

做法（保守，宁漏勿错）
----------------------
1. **实体注册表**：扫描全部节点，从"整个标题就是『实体 + 主题后缀』"的
   主题节点里抽取实体（如「知识点一 颜之推的教育思想」→ 颜之推）；
2. **错位检测**：节点标题里出现一个注册实体、其所有祖先标题里都没有它、
   而祖先里存在**另一个**注册实体 → 疑似错位；
3. **安全门**（全部满足才移动）：
   · 标题里恰好一个注册实体，且注册表中该实体唯一对应一个主题节点；
   · 移动方不是结构节点（不是 知识点/第X章/第X节 本身）；
   · 与目标主题在同一「章节」范围内（最近的 第X章/第X节 相同）；
   · 当前父节点不是习题容器（自测/练习/请将下列… 之类的分组要按题走）；
   · 目标下没有同名节点（防重复）。
   不满足安全门 → 只记录不移动（留给后续人工/增强期审校）。

所有移动都会返回并打印明细，便于回溯。

Usage:
    from placement_audit import fix_topic_placement
    report = fix_topic_placement(tree_children)   # 就地修正
    # report = {"registry": n, "moved": [...], "skipped": [...]}
"""

from __future__ import annotations

import re

# 标题前缀（编号）：第X章/第X节、知识点X、一、、（一）、1.
_PREFIX_RE = re.compile(
    r'^(?:第[一二三四五六七八九十百\d]+[章节]\s*'
    r'|知识点[一二三四五六七八九十\d]+\s*[、.：:]?\s*'
    r'|[一二三四五六七八九十]+[、.]\s*'
    r'|[（(][一二三四五六七八九十\d]+[）)]\s*'
    r'|\d+[、.]\s*)'
)

# 主题后缀（长后缀优先）。标题形如「实体+后缀」才被认为是主题定义节点。
_ENTITY_SUFFIXES = [
    '的教育思想', '的教育目的', '的教育作用', '的教育内容', '的教育方法',
    '的教育主张', '的教育理念', '的教育实践', '的教育活动', '的教学思想',
    '的教师观', '的儿童教育', '的家庭教育', '的德育思想', '的教育功能',
    '的教育制度', '的课程思想', '的教育理论', '的教育观', '的教育',
]

# 实体「名称感」黑名单：任务式/通用措辞混进实体时拒绝（如"培养大儒"、"帝国时期"）
_ENTITY_BLOCKLIST = [
    '时期', '教育', '学校', '社会', '国家', '我国', '综合', '和谐',
    '基础', '思想', '理论', '制度', '为', '的', '与', '和', '及', '进行', '培养',
]

# 结构节点：不要作为"移动方"（它们本身是骨架）
_STRUCT_RE = re.compile(r'^(?:第[一二三四五六七八九十百\d]+[章节]|知识点[一二三四五六七八九十\d]+)')

# 章节边界（同章节约束、内容定位）
_CHAPTER_ANY_RE = re.compile(r'^第[一二三四五六七八九十百\d]+[章节]')

# 习题容器特征：父节点是习题时不做归属移动（题目按题分组，不按知识点拆走）
_EXERCISE_HINT = re.compile(
    r'自测|练习|习题|思考题|请将|下列|补充完整|答题|填空|简答|论述题|名词解释|请选择|判断题'
)


def _strip_prefix(title: str) -> str:
    t = (title or '').strip()
    prev = None
    while prev != t:   # 允许多层前缀（如 "知识点一 1. xxx"）
        prev = t
        t = _PREFIX_RE.sub('', t).strip()
    return t


def extract_entity(title: str):
    """标题是「实体 + 主题后缀」时返回实体名，否则 None。"""
    t = _strip_prefix(title)
    if not t:
        return None
    for suf in _ENTITY_SUFFIXES:
        if not t.endswith(suf):
            continue
        ent = t[:-len(suf)].strip()
        if not (1 < len(ent) <= 6):
            return None
        if re.search(r'[，。；：、（）()?？!！,;]', ent):
            return None
        if any(b in ent for b in _ENTITY_BLOCKLIST):
            return None
        return ent
    return None


def collect_registry(roots: list) -> dict:
    """实体注册表：实体 -> [主题定义节点]（只认整题就是『实体+后缀』的节点）。"""
    reg: dict = {}

    def walk(node):
        title = node.get('title') or ''
        ent = extract_entity(title)
        if ent:
            reg.setdefault(ent, []).append(node)
        for c in node.get('children') or []:
            walk(c)

    for r in roots or []:
        walk(r)
    return reg


def _nearest_chapter(path_titles: list):
    for t in reversed(path_titles):
        if _CHAPTER_ANY_RE.match((t or '').strip()):
            return (t or '').strip()
    return None


def fix_topic_placement(roots: list, log=print) -> dict:
    """对已建好的树做主题归属审校（就地修正）。返回明细报告。"""
    report = {"registry": 0, "moved": [], "skipped": []}
    if not roots:
        return report

    reg = collect_registry(roots)
    report["registry"] = len(reg)
    if not reg:
        return report

    reg_nodes = set()
    for nodes in reg.values():
        for n in nodes:
            reg_nodes.add(id(n))

    # 预扫：节点 id -> 路径标题（用于同章节约束与祖先判断）
    paths = {}

    def collect_paths(node, path):
        paths[id(node)] = path
        for c in node.get('children') or []:
            collect_paths(c, path + [c.get('title') or ''])

    for r in roots:
        collect_paths(r, [r.get('title') or ''])

    def walk(node, parent, path_titles):
        title = node.get('title') or ''
        if title and id(node) not in reg_nodes:
            own = [e for e in reg if e in title]
            if len(own) == 1:
                ent = own[0]
                ancestor_entities = set()
                for at in path_titles[:-1]:
                    for e in reg:
                        if e in at:
                            ancestor_entities.add(e)
                if ancestor_entities and ent not in ancestor_entities:
                    targets = reg[ent]
                    reason = None
                    if len(targets) != 1:
                        reason = f"实体「{ent}」对应多个主题节点，不自动移动"
                    else:
                        tgt = targets[0]
                        tgt_path = paths.get(id(tgt), [])
                        if id(tgt) in [id(x) for x in [node]] or (id(node) in [id(x) for x in [tgt]]):
                            reason = "目标即自身"
                        elif _STRUCT_RE.match(title.strip()):
                            reason = "移动方是结构节点（知识点/章节标题），不自动移动"
                        elif _nearest_chapter(path_titles) != _nearest_chapter(tgt_path):
                            reason = "与目标主题不在同一章节，不自动移动"
                        elif parent is not None and _EXERCISE_HINT.search(
                                (parent.get('title') or '') + ' ' + (parent.get('content') or '')[:80]):
                            reason = "父节点是习题/题组容器，不自动移动"
                        elif any((c.get('title') or '') == title for c in (tgt.get('children') or [])):
                            reason = "目标下已有同名节点，不自动移动"
                        if reason:
                            report["skipped"].append({
                                "title": title[:60], "entity": ent,
                                "from": ' / '.join(path_titles[-3:]),
                                "reason": reason,
                            })
                        else:
                            # 执行移动：从原父节点摘除，挂到主题节点末尾
                            parent_children = parent.get('children') if parent else roots
                            try:
                                parent_children.remove(node)
                            except (ValueError, AttributeError):
                                pass
                            tgt.setdefault('children', []).append(node)
                            report["moved"].append({
                                "title": title[:60], "entity": ent,
                                "from": ' / '.join(path_titles[-3:]),
                                "to": tgt.get('title', '')[:40],
                            })
                            if log:
                                log(f"  归属修正：「{title[:40]}」 → 「{tgt.get('title', '')[:30]}」")
                            return  # 子树随节点一起移动，不再深入
        for c in list(node.get('children') or []):
            walk(c, node, path_titles + [c.get('title') or ''])

    for r in roots:
        walk(r, None, [r.get('title') or ''])

    return report


if __name__ == '__main__':
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("usage: python placement_audit.py <tree.json>")
        sys.exit(1)
    with open(path, encoding='utf-8') as f:
        tree = json.load(f)
    roots = tree.get('children') or []
    rep = fix_topic_placement(roots)
    print(json.dumps({"registry": rep["registry"], "moved": rep["moved"],
                      "skipped": rep["skipped"]}, ensure_ascii=False, indent=2))
