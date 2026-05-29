"""
Debug tool for the Multi-Number Hierarchy Repair Engine.

Usage:
    python debug_hierarchy.py [md_file_path]

If no path given, runs the self-test from hierarchy_repair.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from hierarchy_repair import (
    apply_hierarchy_repair, describe_node, get_hierarchy_stats,
    SEMANTIC_NAMES,
)
from tree_builder import parse_md_to_nodes, build_tree


def show_flat_nodes(nodes, label="Nodes"):
    """Display a flat node list with numbering annotations."""
    print(f"\n{'=' * 70}")
    print(f"  {label} ({len(nodes)} nodes)")
    print(f"{'=' * 70}")
    for i, node in enumerate(nodes):
        title = node.get('title', '')[:70]
        level = node.get('level', '?')
        sem = node.get('semantic_level')
        ntype = node.get('numbering_type', '-')
        path = node.get('number_path')
        path_str = '.'.join(str(p) for p in path) if path else '-'

        sem_str = f"S{sem}" if sem is not None else "S-"
        print(f"  [{i:3d}] L{level:1d} {sem_str:3s} {ntype:20s} path=[{path_str:10s}] {title}")


def show_tree(roots, label="Tree Structure"):
    """Display a tree structure."""
    print(f"\n{'=' * 70}")
    print(f"  {label} ({len(roots)} roots)")
    print(f"{'=' * 70}")

    def _show(node, indent=0, is_last=False):
        prefix = '  ' * indent
        branch = '└─ ' if is_last else '├─ '
        title = node.get('title', '')[:60]
        level = node.get('level', '?')
        sem = node.get('semantic_level')
        ntype = node.get('numbering_type', '-')

        sem_name = SEMANTIC_NAMES.get(sem, '') if isinstance(sem, int) else ''
        type_str = f"{ntype}" if ntype else "standalone"
        print(f"{prefix}{branch}[L{level}] {type_str}: {title}")
        if sem_name:
            print(f"{prefix}    ({sem_name})")

        children = node.get('children', [])
        for i, child in enumerate(children):
            _show(child, indent + 1, i == len(children) - 1)

    for i, root in enumerate(roots):
        _show(root, 0, i == len(roots) - 1)


def show_comparison(before_nodes, after_nodes):
    """Show before/after comparison."""
    print(f"\n{'=' * 70}")
    print(f"  BEFORE REPAIR → AFTER REPAIR COMPARISON")
    print(f"{'=' * 70}")

    max_len = max(len(before_nodes), len(after_nodes))
    print(f"  {'Idx':>4s}  {'Before Level':>12s}  {'After Level':>11s}  Title")
    print(f"  {'-'*4}  {'-'*12}  {'-'*11}  {'-'*50}")

    for i in range(max_len):
        before = before_nodes[i] if i < len(before_nodes) else None
        after = after_nodes[i] if i < len(after_nodes) else None

        b_level = before['level'] if before else '-'
        a_level = after['level'] if after else '-'
        title = before['title'][:50] if before else (after['title'][:50] if after else '-')

        arrow = "→"
        print(f"  {i:4d}  {str(b_level):>12s}  {arrow} {str(a_level):>8s}  {title}")


def debug_file(md_path: str):
    """Debug a markdown file through the full repair pipeline."""
    print(f"\n{'#' * 70}")
    print(f"# DEBUG: {os.path.basename(md_path)}")
    print(f"{'#' * 70}")

    if not os.path.exists(md_path):
        print(f"ERROR: File not found: {md_path}")
        return

    with open(md_path, encoding='utf-8') as f:
        md_text = f.read()

    # Step 1: Parse
    nodes = parse_md_to_nodes(md_text)
    show_flat_nodes(nodes, "AFTER PARSE (before repair)")

    # Step 2: Repair
    repaired = apply_hierarchy_repair(nodes)
    show_flat_nodes(repaired, "AFTER HIERARCHY REPAIR")

    # Step 3: Build tree
    roots = build_tree(repaired)
    show_tree(roots, "FINAL TREE")

    # Stats
    stats = get_hierarchy_stats(repaired)
    print(f"\n{'=' * 70}")
    print(f"  HIERARCHY STATISTICS")
    print(f"{'=' * 70}")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Comparison
    show_comparison(nodes, repaired)


def debug_self_test():
    """Run the built-in self test."""
    from hierarchy_repair import run_self_test
    run_self_test()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        md_path = sys.argv[1]
        debug_file(md_path)
    else:
        print("No file specified. Running built-in self test...\n")
        debug_self_test()

        # Also try to find test MD files
        base = os.path.dirname(__file__)
        test_dir = os.path.join(base, "data", "教原_markdown")
        if os.path.isdir(test_dir):
            md_files = sorted(f for f in os.listdir(test_dir) if f.endswith('.md'))
            if md_files:
                print(f"\nFound {len(md_files)} MD files in {test_dir}")
                print(f"Usage: python debug_hierarchy.py <path_to_md>")
                print(f"Example: python debug_hierarchy.py \"{os.path.join(test_dir, md_files[0])}\"")
