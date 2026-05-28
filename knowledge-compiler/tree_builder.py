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

def get_level(title, hashes):
    t = title.strip()
    if re.match(r'^第[一二三四五六七八九十百\d]+章', t): return 1
    if re.match(r'^第[一二三四五六七八九十百\d]+节', t): return 2
    if re.match(r'^知识点[一二三四五六七八九十\d]+', t):  return 3
    if re.match(r'^[（(]\d+[）)]', t): return 5
    if re.match(r'^[（(][一二三四五六七八九十]+[）)]', t): return 4
    if re.match(r'^\d+[\.、]', t): return 5
    return min(hashes, 5)

_EXPLICIT_LEVEL_RE = re.compile(
    r'^(第[一二三四五六七八九十百\d]+[章节]|知识点[一二三四五六七八九十\d]+|[（(][\d一二三四五六七八九十]+[）)]|\d+[\.、])'
)
_CHAPTER_RE = re.compile(r'^第[一二三四五六七八九十百\d]+章')
_SECTION_RE = re.compile(r'^第[一二三四五六七八九十百\d]+节')

def adjust_standalone_levels(nodes):
    """Demote headers without explicit level patterns that appear inside
    a chapter or section, so they nest correctly instead of capturing
    sibling sections.
    
    E.g. "教师的角色" (L1 by fallback) inside "第一节 教 师" (L2)
    is demoted to L3, allowing "第二节 学生" (L2) to pop it off the
    stack and become a sibling of "第一节".
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
        if _CHAPTER_RE.match(t):
            break
        if _SECTION_RE.match(t):
            first_section = node
            break
    if first_section is not None:
        active_section_level = 2

    for node in nodes:
        t = node['title'].strip()
        if _CHAPTER_RE.match(t):
            active_chapter_level = 1
            active_section_level = 0
        elif _SECTION_RE.match(t):
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

def parse_md_to_nodes(md_text):
    lines = md_text.split('\n')
    nodes = []
    current = None
    buf = []
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            if current is not None:
                current['content'] = '\n'.join(buf).strip()
                nodes.append(current)
                buf = []
            hashes = len(m.group(1))
            title = m.group(2).strip()
            if is_noise(title):
                current = None
                continue
            current = {'title': title, 'level': get_level(title, hashes), 'content': '', 'children': []}
        else:
            s = line.strip()
            if s and not s.startswith('![') and not s.startswith('<') and not s.startswith('```') and current is not None:
                buf.append(line)
    if current is not None:
        current['content'] = '\n'.join(buf).strip()
        nodes.append(current)
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
