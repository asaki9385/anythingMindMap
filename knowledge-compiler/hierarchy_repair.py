"""
Multi-Number Hierarchy Repair Engine (多编号体系层级修复引擎)

Automatically identifies and repairs document knowledge structure by:
1. Detecting various numbering systems (Chinese chapters, Arabic, circle, etc.)
2. Inferring unified semantic levels
3. Fixing orphan nodes, sibling errors, and mixed numbering hierarchies
4. Running before build_tree() to correct node levels

Does NOT modify content, summary, or keywords.
"""

import re
from collections import OrderedDict

# =============================================================================
# CHINESE NUMBER CONVERSION
# =============================================================================

_CN_NUM_MAP = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '百': 100, '千': 1000, '万': 10000,
}


def chinese_to_int(cn: str) -> int:
    """Convert Chinese number string to integer.
    Examples: 一→1, 十二→12, 二十→20, 一百二十三→123
    """
    if not cn:
        return 0
    result = 0
    current = 0
    for ch in cn:
        if ch in ('零', '〇'):
            continue
        val = _CN_NUM_MAP.get(ch)
        if val is None:
            return 0
        if val >= 10:
            if current == 0:
                current = 1
            result += current * val
            current = 0
        else:
            current = val
    result += current
    return result


_CIRCLE_MAP = {
    '①': 1, '②': 2, '③': 3, '④': 4, '⑤': 5,
    '⑥': 6, '⑦': 7, '⑧': 8, '⑨': 9, '⑩': 10,
    '⑪': 11, '⑫': 12, '⑬': 13, '⑭': 14, '⑮': 15,
    '⑯': 16, '⑰': 17, '⑱': 18, '⑲': 19, '⑳': 20,
}


def circle_to_int(circle: str) -> int:
    return _CIRCLE_MAP.get(circle, 0)


# =============================================================================
# NUMBER PATTERNS - Unified regex registry
# =============================================================================

NUMBER_PATTERNS = OrderedDict({
    # ── Chinese Chapter/Section/Part ──
    # Supports: 中文数字、阿拉伯数字、圆圈数字、特殊符号
    'chapter_cn': re.compile(
        r'^第([一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+)章'
    ),
    'section_cn': re.compile(
        r'^第([一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+)节'
    ),
    'part_cn': re.compile(
        r'^第([一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+)部分'
    ),

    # ── English Chapter/Section/Part ──
    'chapter_en': re.compile(
        r'^(?:Chapter|CHAPTER)\s+(\d+)', re.IGNORECASE
    ),
    'section_en': re.compile(
        r'^(?:Section|SECTION)\s+(\d+)', re.IGNORECASE
    ),
    'part_en': re.compile(
        r'^(?:Part|PART)\s+(\d+)', re.IGNORECASE
    ),

    # ── Chinese ordered list (dotted) ──
    'chinese_dotted': re.compile(
        r'^([一二三四五六七八九十]+)[、，,]\s*'
    ),

    # ── Chinese ordered list (bracketed) ──
    'chinese_bracket': re.compile(
        r'^[（(【〔]\s*([一二三四五六七八九十]+)\s*[）)】〕]'
    ),

    # ── Arabic dotted (including multi-level like 1.2.3) ──
    # Supports: 1. / 1.1 / 1.1.1 / 1.2.3.4 / 1  / 1、 / 1，
    'arabic_dotted': re.compile(
        r'^(\d+(?:\.\d+)*)(?:[\.、，,]|\s+)'
    ),

    # ── Arabic bracketed ──
    'arabic_bracket': re.compile(
        r'^[（(【〔]\s*(\d+)\s*[）)】〕]'
    ),

    # ── Arabic right-bracket (1) ──
    'arabic_right_bracket': re.compile(
        r'^(\d+)\s*[）)]'
    ),

    # ── Circle numbers ──
    'circle_number': re.compile(
        r'^([①-⑳])'
    ),

    # ── Knowledge point (Chinese) ──
    'knowledge_point': re.compile(
        r'^知识点([一二三四五六七八九十\d]+)'
    ),
})

# Composite pattern for initial detection
_ANY_NUMBERING = re.compile(
    r'^('
    r'第[一二三四五六七八九十百千\d]+[章节部分]|'
    r'(?:Chapter|CHAPTER|Section|SECTION|Part|PART)\s+\d+|'
    r'[一二三四五六七八九十]+[、，,]|'
    r'[（(【\[〔][一二三四五六七八九十\d]+[）)】\]〕]|'
    r'\d+(?:\.\d+)*[\.、，,)]|'
    r'[①-⑳]|'
    r'知识点[一二三四五六七八九十\d]+'
    r')'
)


# =============================================================================
# Unified Semantic Level Mapping
# =============================================================================

# Base semantic level for each numbering type (used as relative ordering)
TYPE_TO_SEMANTIC_LEVEL = {
    'chapter_cn': 1,
    'chapter_en': 1,
    'part_cn': 1,
    'part_en': 1,
    'section_cn': 2,
    'section_en': 2,
    'chinese_dotted': 3,
    'knowledge_point': 3,
    'chinese_bracket': 4,
    'arabic_dotted': None,       # dynamic: computed per-node
    'arabic_bracket': 6,
    'arabic_right_bracket': 6,
    'circle_number': 7,
}

# Semantic level names for debugging
SEMANTIC_NAMES = {
    1: 'chapter/part',
    2: 'section',
    3: 'chinese-list',
    4: 'chinese-sublist',
    5: 'number-list',
    6: 'bracket-number',
    7: 'circle-number',
}

# Category ordering for hierarchy comparison.
# Lower number = higher in hierarchy (more of an ancestor).
# Categories group numbering types by their role in document structure.
_TYPE_CATEGORY = {
    'chapter_cn': 1, 'chapter_en': 1, 'part_cn': 1, 'part_en': 1,
    'section_cn': 2, 'section_en': 2,
    'chinese_dotted': 3, 'knowledge_point': 3,
    'chinese_bracket': 4,
    'arabic_dotted': 5,
    'arabic_bracket': 6, 'arabic_right_bracket': 6,
    'circle_number': 7,
}

# Chinese-system numbering types
_CHINESE_TYPES = {'chapter_cn', 'section_cn', 'part_cn',
                  'chinese_dotted', 'chinese_bracket', 'knowledge_point'}

# Numbering systems that indicate "high-level" structure (chapters, parts)
_HIGH_LEVEL_TYPES = {'chapter_cn', 'chapter_en', 'part_cn', 'part_en',
                     'section_cn', 'section_en', 'chinese_dotted',
                     'chinese_bracket', 'knowledge_point'}


# =============================================================================
# Detection Functions
# =============================================================================

def _extract_num(val_str: str) -> int:
    """Convert a captured number string to int (handles Arabic, Chinese, and circle numbers)."""
    # Check for circle numbers first
    if val_str in _CIRCLE_MAP:
        return _CIRCLE_MAP[val_str]
    # Check for Arabic digits
    if val_str.isdigit():
        return int(val_str)
    # Check for single special characters (●, ○, etc.) - treat as placeholder
    if len(val_str) == 1 and not val_str.isalnum():
        # Special characters like ● are treated as unknown, return 0
        return 0
    return chinese_to_int(val_str)


def has_any_numbering(title: str) -> bool:
    """Quick check if title starts with any recognized numbering pattern."""
    return bool(_ANY_NUMBERING.match(title.strip()))


def detect_numbering_type(title: str) -> dict | None:
    """Detect the numbering type of a title.

    Returns dict with keys: type, path, semantic_level, raw_prefix
    Returns None if no numbering detected.
    """
    t = title.strip()
    if not t:
        return None

    for ntype, pattern in NUMBER_PATTERNS.items():
        m = pattern.match(t)
        if not m:
            continue

        raw_prefix = m.group(0).rstrip()
        val_str = m.group(1)

        if ntype == 'arabic_dotted':
            path = [int(x) for x in val_str.split('.')]
            semantic_level = len(path)
        elif ntype == 'circle_number':
            path = [circle_to_int(val_str)]
            semantic_level = TYPE_TO_SEMANTIC_LEVEL[ntype]
        else:
            path = [_extract_num(val_str)]
            semantic_level = TYPE_TO_SEMANTIC_LEVEL[ntype]

        return {
            'type': ntype,
            'path': path,
            'semantic_level': semantic_level,
            'raw_prefix': raw_prefix,
        }

    return None


def extract_number_path(title: str) -> list[int] | None:
    """Extract the number path from a title."""
    info = detect_numbering_type(title)
    if info:
        return info['path']
    return None


def infer_semantic_level(title: str) -> int | None:
    """Infer the unified semantic level from a title's numbering system."""
    info = detect_numbering_type(title)
    if info:
        return info['semantic_level']
    return None


# =============================================================================
# Numbering Normalization
# =============================================================================

_BRACKET_NORMALIZE = [
    (re.compile(r'^\((\d+)\)\s*'), r'（\1）'),
    (re.compile(r'^\(([一二三四五六七八九十]+)\)\s*'), r'（\1）'),
    (re.compile(r'^【(\d+)】\s*'), r'（\1）'),
    (re.compile(r'^【([一二三四五六七八九十]+)】\s*'), r'（\1）'),
    (re.compile(r'^〔(\d+)〕\s*'), r'（\1）'),
    (re.compile(r'^〔([一二三四五六七八九十]+)〕\s*'), r'（\1）'),
    (re.compile(r'^([一二三四五六七八九十]+)[,，]\s*'), r'\1、'),
    (re.compile(r'^(\d+)[）)]\s*'), r'\1. '),
    (re.compile(r'^(\d+)[、，]\s*'), r'\1. '),
]


def normalize_numbering(text: str) -> str:
    """Normalize numbering styles to a unified format for consistent detection.
    Does NOT change the semantic content, only the bracket/separator style.
    """
    result = text
    for pattern, replacement in _BRACKET_NORMALIZE:
        result = pattern.sub(replacement, result)
    return result


def normalize_node_numbering(node: dict) -> dict:
    """Normalize the numbering in a node's title (returns modified copy)."""
    result = dict(node)
    if 'title' in result:
        result['title'] = normalize_numbering(result['title'])
    return result


def normalize_nodes_numbering(nodes: list[dict]) -> list[dict]:
    """Normalize numbering across all nodes."""
    return [normalize_node_numbering(n) for n in nodes]


# =============================================================================
# Hierarchy Repair Engine
# =============================================================================

def repair_hierarchy(nodes: list[dict]) -> list[dict]:
    """Repair the hierarchy of a flat node list.

    Strategy:
      1. Detect numbering types for all nodes.
      2. Assign each node a (category, path_depth) rank.
         - category groups numbering types by structural role
         - path_depth handles nesting within the same numbering system
      3. Use a semantic stack: pop when stack-top rank >= current rank
         (stack top is at same or deeper level → sibling or closer),
         then nest the node one level deeper.
      4. Arabic-dotted levels are elevated when under a Chinese ancestor
         to avoid competing at the same level as chapter/section.
      5. Standalone nodes (no numbering) inherit the current context.
    """
    if not nodes:
        return nodes

    # ── Phase 1: Detect numbering for all nodes ──

    for node in nodes:
        title = node.get('title', '')
        info = detect_numbering_type(normalize_numbering(title))

        if info:
            node['semantic_level'] = info['semantic_level']
            node['numbering_type'] = info['type']
            node['number_path'] = info['path']
            node['raw_prefix'] = info['raw_prefix']
        else:
            node['semantic_level'] = None
            node['numbering_type'] = None
            node['number_path'] = None
            node['raw_prefix'] = None

    # ── Phase 2: Compute rank for each node ──
    # Rank = (category, path_depth) where:
    #   category: structural role (1=chapter, 2=section, 3=list, ...)
    #   path_depth: nesting depth within the numbering system
    # Popping: stack-top rank >= node rank means pop.
    # So (category=5, depth=2) >= (category=5, depth=2) pops (sibling).
    # But (category=2, depth=1) NOT >= (category=5, depth=1) (section stays).

    for node in nodes:
        ntype = node.get('numbering_type')
        if ntype is None:
            node['_rank_category'] = None
            node['_rank_depth'] = 0
            continue

        cat = _TYPE_CATEGORY.get(ntype, 99)
        path = node.get('number_path', [])
        depth = len(path)

        node['_rank_category'] = cat
        node['_rank_depth'] = depth

    # ── Phase 3: Semantic stack for building hierarchy ──
    # Stack entries: (effective_level, category, depth, index)
    semantic_stack = []

    for i, node in enumerate(nodes):
        cat = node.get('_rank_category')
        depth = node.get('_rank_depth', 0)
        ntype = node.get('numbering_type')

        if cat is not None:
            # ── Numbered node ──

            # Pop nodes where stack-top (cat, depth) >= current (cat, depth)
            # Higher category number = lower in hierarchy → should be nested
            # Same category + same-or-deeper depth → sibling relationship
            while semantic_stack:
                s_cat, s_depth = semantic_stack[-1][1], semantic_stack[-1][2]
                if s_cat > cat:
                    # Higher category number = lower in hierarchy → pop
                    semantic_stack.pop()
                elif s_cat == cat and s_depth >= depth:
                    # Same category, stack node is at same or deeper level → pop
                    semantic_stack.pop()
                else:
                    break

            effective_level = max(1, len(semantic_stack) + 1)
            node['level'] = effective_level
            semantic_stack.append((effective_level, cat, depth, i))

        else:
            # ── Standalone node (no numbering) ──
            if semantic_stack:
                parent_eff, _, _, _ = semantic_stack[-1]
                effective_level = parent_eff + 1
            else:
                effective_level = max(1, node.get('level', 1))

            node['level'] = effective_level
            # Standalone nodes get sentinel category/depth that won't
            # accidentally pop anything (highest category = lowest priority)
            semantic_stack.append((effective_level, 99, 0, i))

    # ── Phase 4: Fix gaps in level continuity ──
    _fix_level_gaps(nodes)

    # ── Clean up internal fields ──
    for node in nodes:
        node.pop('_rank_category', None)
        node.pop('_rank_depth', None)

    return nodes


def _fix_level_gaps(nodes: list[dict]):
    """Ensure no level gaps > 1 between consecutive numbered nodes.
    If level jumps from 2 to 5 without intermediate levels, it indicates
    a numbering system mismatch. Cap to prev_level + 1.
    """
    prev_numbered_level = 0
    for node in nodes:
        if node.get('numbering_type') is None:
            continue
        cur = node['level']
        if prev_numbered_level > 0 and cur - prev_numbered_level > 2:
            node['level'] = prev_numbered_level + 1
        prev_numbered_level = cur


# =============================================================================
# Full Pipeline Convenience
# =============================================================================

def apply_hierarchy_repair(nodes: list[dict]) -> list[dict]:
    """Apply the complete hierarchy repair pipeline.

    normalize_numbering() → infer_semantic_level() → repair_hierarchy()
    """
    nodes = normalize_nodes_numbering(nodes)
    nodes = repair_hierarchy(nodes)
    return nodes


# =============================================================================
# Debug / Introspection
# =============================================================================

def get_hierarchy_stats(nodes: list[dict]) -> dict:
    """Get statistics about the hierarchy."""
    types = {}
    levels = {}
    numbered = 0
    standalone = 0

    for node in nodes:
        ntype = node.get('numbering_type')
        if ntype:
            numbered += 1
            types[ntype] = types.get(ntype, 0) + 1
        else:
            standalone += 1

        level = node.get('level', 0)
        levels[level] = levels.get(level, 0) + 1

    return {
        'total': len(nodes),
        'numbered': numbered,
        'standalone': standalone,
        'numbering_types': types,
        'level_distribution': dict(sorted(levels.items())),
    }


def describe_node(node: dict) -> str:
    """Generate a human-readable description of a node's numbering."""
    title = node.get('title', '')[:60]
    ntype = node.get('numbering_type', '-')
    path = node.get('number_path')
    sem = node.get('semantic_level', '-')
    level = node.get('level', '?')
    prefix = node.get('raw_prefix', '-')

    path_str = '.'.join(str(p) for p in path) if path else '-'
    sem_name = SEMANTIC_NAMES.get(sem, '-') if isinstance(sem, int) else '-'

    return (f"[L{level}|S{sem}={sem_name}] type={ntype} path=[{path_str}] "
            f"prefix={prefix!r} title={title!r}")


# =============================================================================
# Self-Test
# =============================================================================

def _build_test_nodes_mixed() -> list[dict]:
    """Build a complex mixed-numbering test case."""
    titles = [
        "第一章 教育学",
        "第一节 教育概念",
        "一、教育定义",
        "（一）广义教育",
        "1. 学校教育",
        "（1）现代教育",
        "① 教育目的",
        "② 教育制度",
        "（2）传统教育",
        "2. 家庭教育",
        "（二）狭义教育",
        "二、教育功能",
        "第二节 教育发展",
        "Chapter 2 心理学",
        "Section 1 认知发展",
        "2.1 感知觉",
        "2.1.1 视觉",
        "2.1.2 听觉",
        "2.2 记忆",
        "（一）短时记忆",
        "1. 编码阶段",
        "2. 存储阶段",
        "（二）长时记忆",
    ]
    nodes = []
    for title in titles:
        nodes.append({
            'title': title,
            'level': 1,
            'content': '',
            'children': [],
        })
    return nodes


def _build_test_nodes_pure_arabic() -> list[dict]:
    """Build a pure-Arabic numbering test case."""
    titles = [
        "1. 概述",
        "1.1 背景",
        "1.1.1 历史",
        "1.1.2 现状",
        "1.2 方法",
        "2. 分析",
        "2.1 数据收集",
        "2.1.1 问卷",
        "2.1.2 访谈",
        "2.2 结果",
    ]
    nodes = []
    for title in titles:
        nodes.append({
            'title': title,
            'level': 1,
            'content': '',
            'children': [],
        })
    return nodes


def _print_tree(roots, indent=0):
    """Print a tree structure."""
    for node in roots:
        prefix = '  ' * indent
        title = node.get('title', '')[:60]
        ntype = node.get('numbering_type', '-')
        children = node.get('children', [])
        print(f'{prefix}├─ [L{node["level"]}] {ntype}: {title}')
        if children:
            _print_tree(children, indent + 1)


def run_self_test(verbose: bool = True) -> bool:
    """Run self-test with mixed numbering and pure arabic cases."""
    from tree_builder import build_tree

    if verbose:
        print("=" * 70)
        print("HIERARCHY REPAIR ENGINE - SELF TEST")
        print("=" * 70)

    all_ok = True

    # ── Test 1: Mixed numbering ──
    if verbose:
        print("\n─── Test 1: Mixed Chinese + Arabic + Circle Numbering ───")
    nodes = _build_test_nodes_mixed()
    result = apply_hierarchy_repair(nodes)
    roots = build_tree(result)

    if verbose:
        _print_tree(roots)

    # Verify structure
    def find_node(tree, title_part):
        for n in tree:
            if title_part in n.get('title', ''):
                return n
            found = find_node(n.get('children', []), title_part)
            if found:
                return found
        return None

    # 第一章 should be root at L1
    ch1 = roots[0] if roots else None
    if ch1 and '第一章' in ch1.get('title', ''):
        if verbose:
            print("  [OK] 第一章 is root (L1)")
    else:
        if verbose:
            print("  [FAIL] 第一章 is NOT root!")
        all_ok = False

    # 第一节 should be child of 第一章 at L2
    sec1 = find_node([ch1], '第一节') if ch1 else None
    if sec1 and sec1.get('level') == 2:
        if verbose:
            print("  [OK] 第一节 is child of 第一章 (L2)")
    else:
        if verbose:
            print(f"  [FAIL] 第一节 level={sec1.get('level') if sec1 else 'NOT FOUND'}")
        all_ok = False

    # ① should be under （1） at L7
    circle1 = find_node(roots, '①')
    if circle1:
        if verbose:
            print(f"  [OK] ① found at L{circle1.get('level')}")
    else:
        if verbose:
            print("  [FAIL] ① not found!")
        all_ok = False

    # ── Test 2: Pure Arabic numbering ──
    if verbose:
        print("\n─── Test 2: Pure Arabic Numbering ───")
    nodes2 = _build_test_nodes_pure_arabic()
    result2 = apply_hierarchy_repair(nodes2)
    roots2 = build_tree(result2)

    if verbose:
        _print_tree(roots2)

    # "1. 概述" should be root at L1
    r1 = roots2[0] if roots2 else None
    if r1 and '概述' in r1.get('title', ''):
        if verbose:
            print(f"  [OK] 1. 概述 is root (L{r1.get('level')})")
    else:
        if verbose:
            print("  [FAIL] 1. 概述 is NOT root!")
        all_ok = False

    # "1.1 背景" should be child of "1."
    bg = find_node(roots2, '背景')
    if bg and bg.get('level', 0) > r1.get('level', 0):
        if verbose:
            print(f"  [OK] 1.1 背景 nested under 1. (L{bg.get('level')})")
    else:
        if verbose:
            print("  [FAIL] 1.1 not nested correctly!")
        all_ok = False

    # "1.1.1 历史" should be L3 child
    hist = find_node(roots2, '历史')
    if hist:
        if verbose:
            print(f"  [OK] 1.1.1 历史 at L{hist.get('level')}")
    else:
        if verbose:
            print("  [FAIL] 1.1.1 not found!")
        all_ok = False

    if verbose:
        print("\n" + "=" * 70)
        print(f"SELF TEST {'PASSED' if all_ok else 'FAILED'}")
        print("=" * 70)

    return all_ok
