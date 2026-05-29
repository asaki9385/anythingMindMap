"""
Complete test suite for Multi-Number Hierarchy Repair Engine.

Tests all supported numbering systems and their combinations.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from hierarchy_repair import (
    apply_hierarchy_repair, describe_node, get_hierarchy_stats,
    detect_numbering_type, normalize_numbering, repair_hierarchy,
    chinese_to_int, circle_to_int, NUMBER_PATTERNS,
)
from tree_builder import build_tree


def print_tree(roots, indent=0):
    """Print tree structure."""
    for node in roots:
        prefix = "  " * indent
        title = node.get('title', '')[:50]
        level = node.get('level', '?')
        ntype = node.get('numbering_type', '-')
        children = node.get('children', [])
        print(f"{prefix}├─ [L{level}] {ntype}: {title}")
        if children:
            print_tree(children, indent + 1)


def test_chinese_to_int():
    """Test Chinese number conversion."""
    print("=" * 70)
    print("TEST: Chinese Number Conversion")
    print("=" * 70)

    test_cases = [
        ('一', 1), ('二', 2), ('三', 3), ('四', 4), ('五', 5),
        ('六', 6), ('七', 7), ('八', 8), ('九', 9), ('十', 10),
        ('十一', 11), ('十二', 12), ('二十', 20), ('二十一', 21),
        ('一百', 100), ('一百二十三', 123),
    ]

    all_ok = True
    for cn, expected in test_cases:
        result = chinese_to_int(cn)
        status = "[OK]" if result == expected else "[FAIL]"
        if result != expected:
            all_ok = False
        print(f"  {status} chinese_to_int('{cn}') = {result} (expected {expected})")

    return all_ok


def test_circle_to_int():
    """Test circle number conversion."""
    print("\n" + "=" * 70)
    print("TEST: Circle Number Conversion")
    print("=" * 70)

    test_cases = [
        ('①', 1), ('②', 2), ('③', 3), ('④', 4), ('⑤', 5),
        ('⑥', 6), ('⑦', 7), ('⑧', 8), ('⑨', 9), ('⑩', 10),
    ]

    all_ok = True
    for circle, expected in test_cases:
        result = circle_to_int(circle)
        status = "[OK]" if result == expected else "[FAIL]"
        if result != expected:
            all_ok = False
        print(f"  {status} circle_to_int('{circle}') = {result} (expected {expected})")

    return all_ok


def test_normalize_numbering():
    """Test numbering normalization."""
    print("\n" + "=" * 70)
    print("TEST: Numbering Normalization")
    print("=" * 70)

    test_cases = [
        ('(1) 测试', '（1）测试'),
        ('(一) 测试', '（一）测试'),
        ('【1】测试', '（1）测试'),
        ('【一】测试', '（一）测试'),
        ('〔1〕测试', '（1）测试'),
        ('〔一〕测试', '（一）测试'),
        ('一，测试', '一、测试'),
        ('1）测试', '1. 测试'),
        ('1、测试', '1. 测试'),
    ]

    all_ok = True
    for input_text, expected in test_cases:
        result = normalize_numbering(input_text)
        status = "[OK]" if result == expected else "[FAIL]"
        if result != expected:
            all_ok = False
        print(f"  {status} '{input_text}' -> '{result}'")
        if result != expected:
            print(f"       expected: '{expected}'")

    return all_ok


def test_detect_numbering_type():
    """Test numbering type detection."""
    print("\n" + "=" * 70)
    print("TEST: Numbering Type Detection")
    print("=" * 70)

    test_cases = [
        ('第一章 教育学', 'chapter_cn', [1]),
        ('第一节 教育概念', 'section_cn', [1]),
        ('第一部分 引言', 'part_cn', [1]),
        ('Chapter 1 Introduction', 'chapter_en', [1]),
        ('Section 1 Background', 'section_en', [1]),
        ('Part 1 Overview', 'part_en', [1]),
        ('一、概述', 'chinese_dotted', [1]),
        ('（一）背景', 'chinese_bracket', [1]),
        ('1. 方法', 'arabic_dotted', [1]),
        ('1.1 数据', 'arabic_dotted', [1, 1]),
        ('1.1.1 历史', 'arabic_dotted', [1, 1, 1]),
        ('（1）结果', 'arabic_bracket', [1]),
        ('1）结论', 'arabic_right_bracket', [1]),
        ('① 教育目的', 'circle_number', [1]),
        ('知识点1 重点', 'knowledge_point', [1]),
    ]

    all_ok = True
    for title, expected_type, expected_path in test_cases:
        info = detect_numbering_type(title)
        if info:
            actual_type = info['type']
            actual_path = info['path']
            status = "[OK]" if actual_type == expected_type and actual_path == expected_path else "[FAIL]"
            if actual_type != expected_type or actual_path != expected_path:
                all_ok = False
            print(f"  {status} '{title}'")
            print(f"       type: {actual_type} (expected {expected_type})")
            print(f"       path: {actual_path} (expected {expected_path})")
        else:
            all_ok = False
            print(f"  [FAIL] '{title}' -> None (expected {expected_type})")

    return all_ok


def test_arabic_dotted_hierarchy():
    """Test pure Arabic dotted numbering hierarchy."""
    print("\n" + "=" * 70)
    print("TEST: Arabic Dotted Hierarchy (1. / 1.1 / 1.1.1)")
    print("=" * 70)

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

    nodes = [{'title': t, 'level': 1, 'content': '', 'children': []} for t in titles]
    repaired = repair_hierarchy(nodes)
    roots = build_tree(repaired)

    print("\n--- TREE STRUCTURE ---")
    print_tree(roots)

    # Verify hierarchy
    all_ok = True

    # "1. 概述" should be root at L1
    r1 = roots[0] if roots else None
    if r1 and '1.' in r1['title'] and r1['level'] == 1:
        print("\n  [OK] 1. 概述 is root at L1")
    else:
        print("\n  [FAIL] 1. 概述 not found or wrong level")
        all_ok = False

    # "1.1 背景" should be child of "1." at L2
    bg = r1['children'][0] if r1 and r1.get('children') else None
    if bg and '1.1' in bg['title'] and bg['level'] == 2:
        print("  [OK] 1.1 背景 is child at L2")
    else:
        print("  [FAIL] 1.1 背景 not found or wrong level")
        all_ok = False

    # "1.1.1 历史" should be child of "1.1" at L3
    hist = bg['children'][0] if bg and bg.get('children') else None
    if hist and '1.1.1' in hist['title'] and hist['level'] == 3:
        print("  [OK] 1.1.1 历史 is child at L3")
    else:
        print("  [FAIL] 1.1.1 历史 not found or wrong level")
        all_ok = False

    return all_ok


def test_chinese_chapter_hierarchy():
    """Test Chinese chapter/section hierarchy."""
    print("\n" + "=" * 70)
    print("TEST: Chinese Chapter Hierarchy (第一章/第一节)")
    print("=" * 70)

    titles = [
        "第一章 教育学",
        "第一节 教育概念",
        "第二节 教育发展",
        "第二章 心理学",
        "第一节 认知发展",
    ]

    nodes = [{'title': t, 'level': 1, 'content': '', 'children': []} for t in titles]
    repaired = repair_hierarchy(nodes)
    roots = build_tree(repaired)

    print("\n--- TREE STRUCTURE ---")
    print_tree(roots)

    # Verify hierarchy
    all_ok = True

    # "第一章" should be root at L1
    ch1 = roots[0] if roots else None
    if ch1 and '第一章' in ch1['title'] and ch1['level'] == 1:
        print("\n  [OK] 第一章 is root at L1")
    else:
        print("\n  [FAIL] 第一章 not found or wrong level")
        all_ok = False

    # "第一节" should be child of "第一章" at L2
    sec1 = ch1['children'][0] if ch1 and ch1.get('children') else None
    if sec1 and '第一节' in sec1['title'] and sec1['level'] == 2:
        print("  [OK] 第一节 is child at L2")
    else:
        print("  [FAIL] 第一节 not found or wrong level")
        all_ok = False

    return all_ok


def test_mixed_numbering_complete():
    """Test complete mixed numbering system."""
    print("\n" + "=" * 70)
    print("TEST: Complete Mixed Numbering (Chapter/Section/一、/（一）/1./（1）/①)")
    print("=" * 70)

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
    ]

    nodes = [{'title': t, 'level': 1, 'content': '', 'children': []} for t in titles]
    repaired = repair_hierarchy(nodes)
    roots = build_tree(repaired)

    print("\n--- TREE STRUCTURE ---")
    print_tree(roots)

    # Verify hierarchy
    all_ok = True

    # "第一章" should be root at L1
    ch1 = roots[0] if roots else None
    if ch1 and '第一章' in ch1['title'] and ch1['level'] == 1:
        print("\n  [OK] 第一章 is root at L1")
    else:
        print("\n  [FAIL] 第一章 not found or wrong level")
        all_ok = False

    # "第一节" should be child at L2
    sec1 = ch1['children'][0] if ch1 and ch1.get('children') else None
    if sec1 and '第一节' in sec1['title'] and sec1['level'] == 2:
        print("  [OK] 第一节 is child at L2")
    else:
        print("  [FAIL] 第一节 not found or wrong level")
        all_ok = False

    # "一、" should be child at L3
    list1 = sec1['children'][0] if sec1 and sec1.get('children') else None
    if list1 and '一、' in list1['title'] and list1['level'] == 3:
        print("  [OK] 一、 is child at L3")
    else:
        print("  [FAIL] 一、 not found or wrong level")
        all_ok = False

    # "（一）" should be child at L4
    sublist1 = list1['children'][0] if list1 and list1.get('children') else None
    if sublist1 and '（一）' in sublist1['title'] and sublist1['level'] == 4:
        print("  [OK] （一） is child at L4")
    else:
        print("  [FAIL] （一） not found or wrong level")
        all_ok = False

    # "1." should be child at L5
    num1 = sublist1['children'][0] if sublist1 and sublist1.get('children') else None
    if num1 and '1.' in num1['title'] and num1['level'] == 5:
        print("  [OK] 1. is child at L5")
    else:
        print("  [FAIL] 1. not found or wrong level")
        all_ok = False

    # "（1）" should be child at L6
    bracket1 = num1['children'][0] if num1 and num1.get('children') else None
    if bracket1 and '（1）' in bracket1['title'] and bracket1['level'] == 6:
        print("  [OK] （1） is child at L6")
    else:
        print("  [FAIL] （1） not found or wrong level")
        all_ok = False

    # "①" should be child at L7
    circle1 = bracket1['children'][0] if bracket1 and bracket1.get('children') else None
    if circle1 and '①' in circle1['title'] and circle1['level'] == 7:
        print("  [OK] ① is child at L7")
    else:
        print("  [FAIL] ① not found or wrong level")
        all_ok = False

    return all_ok


def test_standalone_nodes():
    """Test standalone nodes (no numbering) within numbered context."""
    print("\n" + "=" * 70)
    print("TEST: Standalone Nodes in Numbered Context")
    print("=" * 70)

    titles = [
        "1. 概述",
        "这是概述的内容",
        "1.1 背景",
        "这是背景的内容",
        "2. 方法",
        "这是方法的内容",
    ]

    nodes = [{'title': t, 'level': 1, 'content': '', 'children': []} for t in titles]
    repaired = repair_hierarchy(nodes)
    roots = build_tree(repaired)

    print("\n--- TREE STRUCTURE ---")
    print_tree(roots)

    # Verify hierarchy
    all_ok = True

    # "1. 概述" should be root at L1
    r1 = roots[0] if roots else None
    if r1 and '1.' in r1['title'] and r1['level'] == 1:
        print("\n  [OK] 1. 概述 is root at L1")
    else:
        print("\n  [FAIL] 1. 概述 not found or wrong level")
        all_ok = False

    # Standalone content should be nested under "1."
    content = r1['children'][0] if r1 and r1.get('children') else None
    if content and '概述的内容' in content['title']:
        print("  [OK] Standalone content is nested under 1.")
    else:
        print("  [FAIL] Standalone content not nested correctly")
        all_ok = False

    return all_ok


def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "=" * 70)
    print("MULTI-NUMBER HIERARCHY REPAIR ENGINE - COMPLETE TEST SUITE")
    print("=" * 70)

    results = {}
    results['chinese_to_int'] = test_chinese_to_int()
    results['circle_to_int'] = test_circle_to_int()
    results['normalize_numbering'] = test_normalize_numbering()
    results['detect_numbering_type'] = test_detect_numbering_type()
    results['arabic_dotted'] = test_arabic_dotted_hierarchy()
    results['chinese_chapter'] = test_chinese_chapter_hierarchy()
    results['mixed_complete'] = test_mixed_numbering_complete()
    results['standalone'] = test_standalone_nodes()

    print("\n" + "=" * 70)
    print("TEST RESULTS SUMMARY")
    print("=" * 70)

    all_passed = True
    for test_name, passed in results.items():
        status = "[OK] PASSED" if passed else "[FAIL] FAILED"
        if not passed:
            all_passed = False
        print(f"  {status} - {test_name}")

    print("\n" + "=" * 70)
    if all_passed:
        print("ALL TESTS PASSED [OK]")
    else:
        print("SOME TESTS FAILED [FAIL]")
    print("=" * 70)

    return all_passed


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
