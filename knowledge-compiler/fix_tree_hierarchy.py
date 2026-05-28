"""
Fix tree_parts_enhanced hierarchy.

The raw data has flat structure: chapter headers ("第一章 ...") and their
sections ("教育概述") are siblings at L1. This script restructures them
so chapters become parents and sections become children.

Output: data/tree_parts_enhanced_fixed/
"""

import json
import re
import os
import copy

INPUT_DIR = "data/tree_parts_enhanced_v2"
OUTPUT_DIR = "data/tree_parts_enhanced_fixed"

CHAPTER_RE = re.compile(r'^第.{1,3}[章篇]')
SECTION_RE = re.compile(r'^第.{1,3}节')


def load_all_parts():
    parts = []
    for i in range(1, 12):
        path = os.path.join(INPUT_DIR, f"Part_{i}_tree_enhanced.json")
        with open(path, encoding='utf-8') as f:
            parts.append(json.load(f))
    return parts


def deep_copy(node):
    return copy.deepcopy(node)


def restructure_parts(parts):
    """
    Merge all parts into a properly nested tree:
    - Chapter headers ("第X章") become top-level containers
    - L2 section headers ("第第X节") become children of chapters
    - Content nodes nest under the active section or chapter
    - Orphan nodes at part boundaries attach to the last known context
    """
    all_nodes = []
    for part in parts:
        for child in part.get('children', []):
            all_nodes.append(deep_copy(child))

    root_children = []
    current_chapter = None
    current_section = None  # active L2 section (if any)

    for node in all_nodes:
        title = node.get('title', '')
        level = node.get('level', 0)

        if CHAPTER_RE.match(title):
            # Chapter header — new top-level container
            if 'children' not in node:
                node['children'] = []
            root_children.append(node)
            current_chapter = node
            # If the chapter already has L2 section children, track the last one
            current_section = None
            for child in node.get('children', []):
                if SECTION_RE.match(child.get('title', '')) and child.get('level', 0) <= 2:
                    current_section = child

        elif SECTION_RE.match(title) and level <= 2 and current_chapter is not None:
            # Section header ("第X节") — child of current chapter
            current_chapter['children'].append(node)
            current_section = node  # now this section is the active parent

        elif current_section is not None:
            # Content node under the active section
            current_section['children'].append(node)

        elif current_chapter is not None:
            # Content node directly under chapter (no active section)
            current_chapter['children'].append(node)

        else:
            # Truly orphan node (before any chapter)
            root_children.append(node)

    return root_children


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    parts = load_all_parts()
    print(f"Loaded {len(parts)} parts")

    # Count original top-level nodes
    total_original = sum(len(p.get('children', [])) for p in parts)
    print(f"Original top-level nodes: {total_original}")

    # Restructure
    restructured = restructure_parts(parts)

    # Count restructured nodes
    def count_nodes(nodes):
        total = 0
        for n in nodes:
            total += 1
            total += count_nodes(n.get('children', []))
        return total

    total_restructured = count_nodes(restructured)
    print(f"Restructured total nodes: {total_restructured}")

    # Print chapter summary
    print(f"\nChapters found: {len(restructured)}")
    for ch in restructured:
        title = ch.get('title', '?')[:50]
        child_count = len(ch.get('children', []))
        print(f"  [{child_count:2d} children] {title}")

    # Save as a single merged file
    merged = {
        "title": "Knowledge Tree",
        "children": restructured
    }
    merged_path = os.path.join(OUTPUT_DIR, "knowledge_tree_merged.json")
    with open(merged_path, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\nSaved merged tree: {merged_path}")

    # Also split back into part-like files for the UI (group by chapter)
    # Group chapters into ~equal parts for the UI to load
    part_size = max(1, len(restructured) // 3)
    for part_idx in range(0, len(restructured), part_size):
        part_num = part_idx // part_size + 1
        chunk = restructured[part_idx:part_idx + part_size]
        part_data = {
            "title": f"Part_{part_num}",
            "children": chunk
        }
        part_path = os.path.join(OUTPUT_DIR, f"Part_{part_num}_tree_enhanced.json")
        with open(part_path, 'w', encoding='utf-8') as f:
            json.dump(part_data, f, ensure_ascii=False, indent=2)
        node_count = count_nodes(chunk)
        print(f"  Part {part_num}: {len(chunk)} chapters, {node_count} total nodes")


if __name__ == "__main__":
    main()
