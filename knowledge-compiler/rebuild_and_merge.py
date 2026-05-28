"""
Rebuild trees from MD with fixed level logic, then merge AI-enhanced data back.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from tree_builder import parse_md_to_nodes, build_tree, build_single_tree
from collections import deque

BASE_DIR = os.path.dirname(__file__)
MD_DIR = os.path.join(BASE_DIR, "data", "教原_markdown")
TREE_DIR = os.path.join(BASE_DIR, "data", "tree_parts")
OLD_ENHANCED_DIR = os.path.join(BASE_DIR, "data", "tree_parts_enhanced")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_v2")


def build_node_index(tree):
    """Build a path-indexed dict from tree: {breadcrumb_path: node}"""
    index = {}
    q = deque()
    for child in tree.get('children', []):
        q.append((child, (child.get('title', ''),)))
    while q:
        node, path = q.popleft()
        index[path] = node
        for child in node.get('children', []):
            q.append((child, path + (child.get('title', ''),)))
    return index


def find_matching_path(enhanced_index, new_path, exact_match_first=True):
    """Find the best matching enhanced node for a given new path."""
    if new_path in enhanced_index:
        return new_path

    # Try fuzzy: match by last title component
    new_title = new_path[-1]
    candidates = []
    for e_path in enhanced_index:
        if e_path[-1] == new_title:
            candidates.append(e_path)

    if len(candidates) == 1:
        return candidates[0]

    # Try matching last two components
    if len(new_path) >= 2:
        for e_path in enhanced_index:
            if len(e_path) >= 2 and e_path[-2] == new_path[-2] and e_path[-1] == new_title:
                return e_path

    return None


def merge_enhanced(new_tree, enhanced_tree):
    """Copy summary/keywords/exam_points from enhanced_tree to new_tree by path matching."""
    enhanced_index = build_node_index(enhanced_tree)
    new_index = build_node_index(new_tree)

    matched = 0
    for new_path, new_node in new_index.items():
        e_path = find_matching_path(enhanced_index, new_path)
        if e_path and enhanced_index[e_path].get('summary'):
            e_node = enhanced_index[e_path]
            new_node['summary'] = e_node.get('summary', '')
            new_node['keywords'] = e_node.get('keywords', [])
            new_node['exam_points'] = e_node.get('exam_points', [])
            matched += 1

    return matched


def count_enhanced(tree):
    e = 1 if tree.get('summary') else 0
    for c in tree.get('children', []):
        e += count_enhanced(c)
    return e


def main():
    md_files = sorted([
        os.path.join(MD_DIR, f) for f in os.listdir(MD_DIR) if f.endswith(".md")
    ])

    os.makedirs(TREE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    total_matched = 0
    total_nodes = 0

    for md_path in md_files:
        filename = os.path.splitext(os.path.basename(md_path))[0]

        r = build_single_tree(md_path, TREE_DIR)
        print(f"[{filename}] Rebuilt: {r['nodes']} nodes, {r['top']} top-level")

        new_tree_path = r['path']
        with open(new_tree_path, encoding='utf-8') as f:
            new_tree = json.load(f)

        enhanced_path = os.path.join(OLD_ENHANCED_DIR, f"{filename}_tree_enhanced.json")
        if os.path.exists(enhanced_path):
            with open(enhanced_path, encoding='utf-8') as f:
                enhanced_tree = json.load(f)

            matched = merge_enhanced(new_tree, enhanced_tree)
            total_matched += matched
            total_nodes += r['nodes']
            enhanced_count = count_enhanced(new_tree)
            print(f"  Merged: {matched} paths matched, {enhanced_count} enhanced nodes")
        else:
            print(f"  WARN: No enhanced file found for {filename}")

        out_path = os.path.join(OUTPUT_DIR, f"{filename}_tree_enhanced.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(new_tree, f, ensure_ascii=False, indent=2)

    print(f"\nTotal: {total_matched} enhancements merged across {total_nodes} nodes")


if __name__ == "__main__":
    main()
