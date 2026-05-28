"""
333教育综合 - MD → 知识树 Builder (并行版)
每个MD文件独立处理，产出单独的tree JSON
"""

import re
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

NOISE_EXACT = {
    "333", "教育综合", "考研大纲知识清单", "前言",
    "参考答案", "【参考答案】", "精华提要", "知识导图",
}
NOISE_CONTAINS = [
    "丹丹", "攻略", "导学", "自测表", "考情分析",
    "考点延伸", "知识导图", "公众号", "微信",
    "热点话题", "参考答案",
]

def is_noise(title):
    t = title.strip()
    if t in NOISE_EXACT:
        return True
    return any(kw in t for kw in NOISE_CONTAINS)


# =============================================================================
# 目录页检测和章节标题合并
# =============================================================================

# 目录页特征：连续的章节标题 + 页码格式
# 支持带 # 前缀和不带 # 前缀的格式
_TOC_CHAPTER_RE = re.compile(r'^(?:#\s+)?第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+章\s+.+/\s*\d+$')
_TOC_SECTION_RE = re.compile(r'^(?:#\s+)?第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+节\s+.+/\s*\d+$')
_TOC_ITEM_RE = re.compile(r'^(?:本章自测表|参考文献|思考题|习题)\s*/\s*\d+$')

# 章节标题模式（不含页码）
_CHAPTER_TITLE_RE = re.compile(r'^第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+章$')


def detect_toc_section(lines, start_idx):
    """检测从 start_idx 开始是否是目录页内容。

    目录页特征：
    1. 以 # 第X章 标题 / 页码 开头
    2. 后面跟着 第X节 标题 / 页码 格式的子章节
    3. 可能包含 "本章自测表 / 页码" 等

    返回：(is_toc, end_idx)
      - is_toc: 是否是目录页
      - end_idx: 目录页结束的索引（下一行）
    """
    if start_idx >= len(lines):
        return False, start_idx

    line = lines[start_idx].strip()

    # 检查是否是目录页的章节标题（带页码）
    if not _TOC_CHAPTER_RE.match(line):
        return False, start_idx

    # 向后扫描，寻找目录页的结束
    idx = start_idx + 1
    while idx < len(lines):
        line = lines[idx].strip()

        # 空行跳过
        if not line:
            idx += 1
            continue

        # 检查是否是目录页的子章节（带页码）
        if _TOC_SECTION_RE.match(line) or _TOC_ITEM_RE.match(line):
            idx += 1
            continue

        # 检查是否是下一个目录页的章节标题（带页码）
        if _TOC_CHAPTER_RE.match(line):
            # 继续扫描下一个章节
            idx += 1
            continue

        # 遇到其他内容，目录页结束
        break

    return True, idx


def merge_split_chapter_titles(nodes):
    """合并被拆分的章节标题。

    例如：
      "# 第一章"
      "# 心理发展与教育"

    应该合并为：
      "# 第一章 心理发展与教育"
    """
    if not nodes:
        return nodes

    merged = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        title = node.get('title', '')

        # 检查是否是单独的章节标题（如 "第一章"）
        if _CHAPTER_TITLE_RE.match(title) and i + 1 < len(nodes):
            next_node = nodes[i + 1]
            next_title = next_node.get('title', '')

            # 检查下一个节点是否是章节标题的一部分
            # 且下一个节点没有自己的编号体系
            if (next_title and
                not re.match(r'^第[一二三四五六七八九十百千\d]+[章节部分]', next_title) and
                not re.match(r'^[一二三四五六七八九十]+[、，,]', next_title) and
                not re.match(r'^[（(【〔][一二三四五六七八九十\d]+[）)】〕]', next_title) and
                not re.match(r'^\d+(?:\.\d+)*', next_title) and
                not re.match(r'^[①-⑳]', next_title) and
                len(next_title) < 20):  # 标题长度限制

                # 合并标题
                merged_title = f"{title} {next_title}"
                merged_node = dict(node)
                merged_node['title'] = merged_title
                merged.append(merged_node)
                i += 2  # 跳过下一个节点
                continue

        merged.append(node)
        i += 1

    return merged

def get_level(title, hashes):
    t = title.strip()
    if re.match(r'^第[一二三四五六七八九十百\d]+章', t): return 1
    if re.match(r'^第[一二三四五六七八九十百\d]+节', t): return 2
    if re.match(r'^知识点[一二三四五六七八九十\d]+', t):  return 3
    if re.match(r'^[（(]\d+[）)]', t): return 5
    if re.match(r'^[（(][一二三四五六七八九十]+[）)]', t): return 4
    # English numbered sections: depth by number-segment count
    if re.match(r'^\d+\.\d+\.\d+\s', t): return 3
    if re.match(r'^\d+\.\d+\s', t): return 2
    # 单独的 "数字. " 格式（如 "1. 弗洛伊德关于自我发展的理论"）
    # 这种格式通常是知识点的子节点，应该使用更高的层级
    if re.match(r'^\d+\.\s', t): return 5
    # Chinese comma-separated sub-points (e.g. "1、要点")
    if re.match(r'^\d+[、]', t): return 5
    # Other dotted numbered items without trailing space (e.g. "1.text")
    if re.match(r'^\d+\.\s*\S', t): return 5
    return min(hashes, 5)

_EXPLICIT_LEVEL_RE = re.compile(
    r'^(第[一二三四五六七八九十百\d]+[章节]|知识点[一二三四五六七八九十\d]+|[（(][\d一二三四五六七八九十]+[）)]|\d+[\.、])'
)
_CHAPTER_RE = re.compile(r'^第[一二三四五六七八九十百\d]+章')
_SECTION_RE = re.compile(r'^第[一二三四五六七八九十百\d]+节')
# English numbered section patterns
_EN_CHAPTER_RE = re.compile(r'^\d+\.\s')
_EN_SECTION_RE = re.compile(r'^\d+\.\d+\s')


def _is_chapter(t):
    return bool(_CHAPTER_RE.match(t) or _EN_CHAPTER_RE.match(t))


def _is_section(t):
    return bool(_SECTION_RE.match(t) or _EN_SECTION_RE.match(t))


def adjust_standalone_levels(nodes):
    """Demote headers without explicit level patterns that appear inside
    a chapter or section, so they nest correctly instead of capturing
    sibling sections.

    E.g. "Background" (L1 by fallback) inside "2. Methods" (L1)
    is demoted to L2, allowing "2.1 Overview" (L2) to pop it off the
    stack and become a sibling.
    """
    if not nodes:
        return nodes

    # Pre-scan: if the file starts inside a section (no chapter header
    # before the first section), seed the section context so continuation
    # files (e.g. Part_11 continuing Part_10) don't lose hierarchy.
    active_chapter_level = 0
    active_section_level = 0
    first_section = None
    for node in nodes:
        t = node['title'].strip()
        if _is_chapter(t):
            break
        if _is_section(t):
            first_section = node
            break
    if first_section is not None:
        active_section_level = 2

    for node in nodes:
        t = node['title'].strip()
        if _is_chapter(t):
            active_chapter_level = 1
            active_section_level = 0
        elif _is_section(t):
            active_section_level = 2
        elif not _EXPLICIT_LEVEL_RE.match(t):
            if active_section_level > 0 and node['level'] <= active_section_level:
                node['level'] = active_section_level + 1
            elif active_chapter_level > 0 and node['level'] <= active_chapter_level:
                node['level'] = active_chapter_level + 1
    return nodes

def merge_chapter_titles(nodes):
    result = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        t = node['title'].strip()
        is_ch = re.match(r'^第[一二三四五六七八九十百\d]+章$', t)
        is_sec = re.match(r'^第[一二三四五六七八九十百\d]+节$', t)
        if (is_ch or is_sec) and i + 1 < len(nodes):
            nxt = nodes[i+1]
            nxt_t = nxt['title'].strip()
            if not re.match(r'^第[一二三四五六七八九十百\d]+[章节]', nxt_t) and nxt['level'] <= 2:
                merged = dict(node)
                merged['title'] = t + ' ' + nxt_t
                merged['content'] = nxt.get('content','') or node.get('content','')
                merged['children'] = []
                result.append(merged)
                i += 2
                continue
        result.append(node)
        i += 1
    return result

CAPTION_RE = re.compile(
    r'^(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|图\s*\d+|表\s*\d+)[：:．.]?\s*(.+)',
    re.IGNORECASE,
)


def parse_md_to_nodes(md_text):
    lines = md_text.split('\n')
    nodes = []
    current = None
    buf = []
    in_mermaid = False
    in_table = False
    in_html_table = False
    in_formula = False

    # 跳过目录页内容
    i = 0
    while i < len(lines):
        line = lines[i]
        is_toc, end_idx = detect_toc_section(lines, i)
        if is_toc:
            # 跳过目录页内容
            i = end_idx
            continue

        # 处理正常内容
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            if current is not None:
                current['content'] = '\n'.join(buf).strip()
                nodes.append(current)
                buf = []
                in_mermaid = False
                in_table = False
                in_html_table = False
                in_formula = False
            hashes = len(m.group(1))
            title = m.group(2).strip()
            if is_noise(title):
                current = None
                i += 1
                continue
            current = {'title': title, 'level': get_level(title, hashes), 'content': '', 'children': [], 'captions': []}
        else:
            s = line.strip()
            if current is None:
                i += 1
                continue
            # Preserve mermaid blocks
            if s.startswith('```mermaid'):
                in_mermaid = True
                buf.append(line)
                i += 1
                continue
            if in_mermaid:
                buf.append(line)
                if s == '```':
                    in_mermaid = False
                i += 1
                continue
            # Preserve HTML <table> blocks (MinerU table output)
            if s.lower().startswith('<table'):
                in_html_table = True
                buf.append(line)
                if s.lower().endswith('</table>'):
                    in_html_table = False
                i += 1
                continue
            if in_html_table:
                buf.append(line)
                if s.lower().endswith('</table>'):
                    in_html_table = False
                i += 1
                continue
            # Preserve pipe tables
            if re.match(r'^\|', s):
                in_table = True
                buf.append(line)
                i += 1
                continue
            elif in_table and s == '':
                in_table = False
                i += 1
                continue
            else:
                in_table = False
            # Preserve LaTeX formula blocks ($$...$$)
            if s.startswith('$$'):
                in_formula = not in_formula
                buf.append(line)
                i += 1
                continue
            if in_formula:
                buf.append(line)
                i += 1
                continue
            # Caption detection (Figure/Table/chart labels)
            cap_m = CAPTION_RE.match(s)
            if cap_m:
                label = cap_m.group(1).strip()
                caption_text = cap_m.group(2).strip()
                buf.append(f"[图表] {label}: {caption_text}")
                current.setdefault('captions', []).append({"label": label, "text": caption_text})
                i += 1
                continue
            # Standard filter: skip images, non-table HTML, non-mermaid code fences
            if s and not s.startswith('![') and not s.startswith('<') and not s.startswith('```'):
                buf.append(line)
        i += 1

    if current is not None:
        current['content'] = '\n'.join(buf).strip()
        nodes.append(current)

    # 合并被拆分的章节标题
    nodes = merge_split_chapter_titles(nodes)

    return nodes

def build_tree(nodes):
    if not nodes: return []
    nodes = merge_chapter_titles(nodes)
    roots = []
    stack = []
    for node in nodes:
        level = node['level']
        node = dict(node)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]['children'].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots

def _detect_hierarchy_anomalies(nodes, parent_level=0):
    """Scan tree for hierarchy anomalies. Returns list of {node, path, reason}."""
    anomalies = []
    for i, node in enumerate(nodes):
        level = node.get('level', 0)
        title = node.get('title', '')

        # Level gap > 1
        if parent_level > 0 and level > parent_level + 1:
            anomalies.append({
                "node": node, "title": title, "level": level,
                "expected_max": parent_level + 1,
                "reason": f"level jump {parent_level} -> {level}"
            })

        # 5+ consecutive same-level siblings
        if i >= 4:
            prev_levels = [nodes[j].get('level', 0) for j in range(i-4, i)]
            if all(l == level for l in prev_levels) and level == nodes[i-1].get('level', 0):
                anomalies.append({
                    "node": node, "title": title, "level": level,
                    "reason": f"5+ consecutive L{level} siblings"
                })

        # Recurse into children
        children = node.get('children', [])
        if children:
            anomalies.extend(_detect_hierarchy_anomalies(children, level))

    return anomalies


def validate_and_repair_hierarchy(roots, api_key):
    """Use LLM to validate and repair hierarchy anomalies.

    Falls back to original structure on failure.
    """
    anomalies = _detect_hierarchy_anomalies(roots)
    if not anomalies:
        return roots

    # Deduplicate by title
    seen = set()
    unique = []
    for a in anomalies:
        key = (a['title'], a['level'])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    if len(unique) > 30:
        unique = unique[:30]

    # Build prompt
    items = []
    for i, a in enumerate(unique):
        items.append(f"{i+1}. \"{a['title']}\" (当前level={a['level']}, 问题: {a['reason']})")

    prompt = f"""以下是文档标题层级识别结果中存在问题的条目。请判断每个标题的正确层级（1-5）。

标题列表:
{chr(10).join(items)}

规则:
- level 1 = 章/Chapter
- level 2 = 节/Section
- level 3 = 知识点/子节
- level 4 = 子知识点
- level 5 = 细节/条目

严格输出JSON数组，每个元素包含 "idx"（序号从0开始）和 "correct_level"（1-5的整数）:
[{{"idx": 0, "correct_level": 2}}, ...]

只输出确实需要修正的条目。如果某个条目当前level已经是合理的，不要包含它。"""

    import httpx
    try:
        resp = httpx.post(
            "https://api.deepseek.com/chat/completions",
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 500,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=20
        )
        if resp.status_code != 200:
            print(f"  Hierarchy validation HTTP {resp.status_code}, keeping original")
            return roots

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)

        # Handle both direct array and {"corrections": [...]} format
        corrections = result if isinstance(result, list) else result.get("corrections", result.get("repairs", []))

        # Apply corrections to a deep copy to preserve original on failure
        import copy
        roots_copy = copy.deepcopy(roots)
        # Re-detect anomalies on the copy to get mutable references
        unique_copy = []
        seen_copy = set()
        for a in _detect_hierarchy_anomalies(roots_copy):
            key = (a['title'], a['level'])
            if key not in seen_copy:
                seen_copy.add(key)
                unique_copy.append(a)
        unique_copy = unique_copy[:30]

        fixed = 0
        for c in corrections:
            idx = c.get('idx')
            new_level = c.get('correct_level')
            if idx is not None and new_level and 1 <= new_level <= 5 and idx < len(unique_copy):
                a = unique_copy[idx]
                if new_level != a['level']:
                    a['node']['level'] = new_level
                    fixed += 1

        if fixed > 0:
            print(f"  Hierarchy: fixed {fixed}/{len(unique_copy)} anomalies")
            flat = []
            for root in roots_copy:
                flat.extend(_flatten_for_rebuild(root))
            roots = build_tree(flat)

    except Exception as e:
        print(f"  Hierarchy validation failed: {e}, keeping original")

    return roots


def _flatten_for_rebuild(node, parent_level=0):
    """Flatten tree back to node list for rebuild_tree."""
    flat_node = {k: v for k, v in node.items() if k != 'children'}
    flat_node['children'] = []
    result = [flat_node]
    for child in node.get('children', []):
        result.extend(_flatten_for_rebuild(child, node['level']))
    return result


def build_single_tree(md_path: str, output_dir: str) -> dict:
    """Build tree from a single MD file, save to output_dir, return tree dict."""
    filename = os.path.splitext(os.path.basename(md_path))[0]
    with open(md_path, encoding='utf-8') as f:
        md_text = f.read().strip()

    nodes = parse_md_to_nodes(md_text)
    nodes = adjust_standalone_levels(nodes)
    tree_children = build_tree(nodes)
    tree = {"title": filename, "children": tree_children}

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{filename}_tree.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    node_count = sum(1 for _ in _iter_nodes(tree))
    return {"filename": filename, "path": out_path, "nodes": node_count, "top": len(tree_children)}

def _iter_nodes(node):
    yield node
    for c in node.get('children', []):
        yield from _iter_nodes(c)

def count_all(node):
    return 1 + sum(count_all(c) for c in node.get('children', []))

def print_tree(node, indent=0, max_depth=3):
    if indent > max_depth * 2: return
    cc = len(node.get('children', []))
    preview = node.get('content','')[:35].replace('\n',' ')
    suffix = f" ({cc}子)" if cc else ""
    suffix += f'  <- "{preview}..."' if preview else ""
    print("  "*indent + f"{'|--' if indent else '*'} [L{node['level']}] {node['title']}{suffix}")
    for c in node.get('children', []):
        print_tree(c, indent+1, max_depth)

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(__file__)
    MD_DIR = os.path.join(BASE_DIR, "data", "教原_markdown")
    TREE_DIR = os.path.join(BASE_DIR, "data", "tree_parts")
    MERGED_OUTPUT = os.path.join(BASE_DIR, "data", "knowledge_tree.json")

    md_files = sorted([
        os.path.join(MD_DIR, f) for f in os.listdir(MD_DIR) if f.endswith(".md")
    ]) if os.path.isdir(MD_DIR) else []

    print(f"Scanning: {MD_DIR}")
    print(f"Found {len(md_files)} MD files\n")

    # Parallel build
    print("Building trees in parallel...")
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(build_single_tree, p, TREE_DIR): p for p in md_files}
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            print(f"  [OK] {r['filename']} ({r['nodes']} nodes)")

    results.sort(key=lambda x: x['filename'])

    # Merge all trees into one
    print(f"\nMerging into {MERGED_OUTPUT}...")
    merged_children = []
    for r in results:
        with open(r['path'], encoding='utf-8') as f:
            tree = json.load(f)
        merged_children.extend(tree.get('children', []))

    merged = {"title": "333教育综合", "children": merged_children}
    os.makedirs(os.path.dirname(MERGED_OUTPUT), exist_ok=True)
    with open(MERGED_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    total = sum(r['nodes'] for r in results)
    print(f"\nDone: {len(results)} files, {total} total nodes")
    print(f"  Individual trees: {TREE_DIR}/")
    print(f"  Merged tree: {MERGED_OUTPUT}")
