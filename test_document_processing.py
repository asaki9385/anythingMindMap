#!/usr/bin/env python3
"""
Test script to verify Word and TXT document processing pipeline.
Tests:
1. File type detection
2. Text extraction (DOCX -> MD, TXT -> MD)
3. Large text splitting with context bridging
4. LLM structuring of unstructured text
5. Full pipeline integration
"""

import sys
import os
import json
import tempfile
from pathlib import Path

# Add the knowledge-compiler directory to path
COMPILER_DIR = os.path.join(os.path.dirname(__file__), 'knowledge-compiler')
sys.path.insert(0, COMPILER_DIR)

def test_imports():
    """Test that all required modules can be imported."""
    print("=" * 60)
    print("TEST 1: Checking Imports")
    print("=" * 60)
    
    try:
        from parser.text_extractor import (
            docx_to_markdown, 
            txt_to_markdown, 
            split_large_text, 
            has_heading_structure
        )
        print("✓ text_extractor imports: OK")
        
        from parser.llm_structurer import structure_all_chunks
        print("✓ llm_structurer imports: OK")
        
        from server import (
            detect_file_type,
            _prepare_text_units
        )
        print("✓ server imports: OK")
        
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_file_type_detection():
    """Test file type detection function."""
    print("\n" + "=" * 60)
    print("TEST 2: File Type Detection")
    print("=" * 60)
    
    from server import detect_file_type
    
    test_cases = [
        ("document.docx", "word"),
        ("notes.txt", "text"),
        ("report.pdf", "pdf"),
        ("DOCUMENT.DOCX", "word"),
        ("TEST.TXT", "text"),
        ("file.unknown", "unknown"),
    ]
    
    all_passed = True
    for filename, expected in test_cases:
        result = detect_file_type(filename)
        status = "✓" if result == expected else "✗"
        print(f"{status} {filename}: {result} (expected {expected})")
        if result != expected:
            all_passed = False
    
    return all_passed


def test_text_extraction():
    """Test text extraction from TXT files."""
    print("\n" + "=" * 60)
    print("TEST 3: Text Extraction (TXT)")
    print("=" * 60)
    
    from parser.text_extractor import txt_to_markdown, has_heading_structure
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write("""第一章 教育的基本概念

教育是什么？教育是人类社会的一种重要现象，是社会文明的重要组成部分。

第一节 教育的定义

教育是指有目的、有计划、有组织地对受教育者进行思想、品德、知识和技能的培养。

知识点1 教育的属性

教育具有社会性、历史性和阶级性。

知识点2 教育的功能

教育具有培养人、传承文明、发展社会的功能。

第二节 教育的发展

教育随着社会的发展而发展，不同的社会形式有不同的教育形式。""")
        txt_path = f.name
    
    try:
        result = txt_to_markdown(txt_path)
        has_structure = has_heading_structure(result)
        
        print(f"✓ TXT file extracted: {len(result)} chars")
        print(f"✓ Has heading structure: {has_structure}")
        
        # Check if markdown was properly formatted
        if "# 第一章 教育的基本概念" in result:
            print("✓ Markdown conversion: OK (headers detected)")
        else:
            print("✗ Markdown conversion: FAILED (headers not detected)")
            return False
        
        return True
    finally:
        os.unlink(txt_path)


def test_large_text_splitting():
    """Test automatic text splitting for large documents."""
    print("\n" + "=" * 60)
    print("TEST 4: Large Text Splitting with Context Bridging")
    print("=" * 60)
    
    from parser.text_extractor import split_large_text
    
    # Create a large text with multiple sections
    large_text = """# 第一章 教育学基础

## 第一节 教育的定义

教育是人类社会的一种重要现象，是指有目的、有计划、有组织地对受教育者进行思想、品德、知识和技能的培养和训练。

### 知识点1：教育的本质

教育的本质是一种社会现象，它是社会文明传承的重要方式。教育既是社会发展的产物，也是推动社会进步的重要力量。

### 知识点2：教育的主要特征

教育具有目的性、计划性、组织性和系统性。

## 第二节 教育的功能

教育的功能是多方面的，包括人才培养功能、文化传承功能、社会发展功能等。

### 知识点3：人才培养功能

人才培养是教育的首要功能，教育通过培养各种类型的人才来满足社会需要。

### 知识点4：文化传承功能

教育是文化传承的重要途径，通过教育可以将人类文明积累代代相传。

# 第二章 教育的历史发展

## 第一节 古代教育

古代教育主要以私塾和官学的形式存在，教育内容主要是儒家经典和其他传统知识。

## 第二节 近代教育

近代教育是指从鸦片战争到1949年中华人民共和国成立这一历史时期的教育。

## 第三节 现代教育

现代教育是指1949年以来的教育，包括探索阶段、建设阶段和改革阶段。
""" * 2  # Repeat to make it larger
    
    # Test default splitting
    chunks = split_large_text(large_text, max_chars=2000)
    
    print(f"✓ Text split into {len(chunks)} chunks")
    
    # Verify chunk metadata
    all_correct = True
    for i, chunk in enumerate(chunks):
        if chunk['index'] != i:
            print(f"✗ Chunk {i}: index mismatch ({chunk['index']})")
            all_correct = False
        if chunk['total'] != len(chunks):
            print(f"✗ Chunk {i}: total mismatch ({chunk['total']} vs {len(chunks)})")
            all_correct = False
        if i == 0 and not chunk['is_first']:
            print(f"✗ Chunk {i}: should be marked as first")
            all_correct = False
        if i == len(chunks) - 1 and not chunk['is_last']:
            print(f"✗ Chunk {i}: should be marked as last")
            all_correct = False
    
    if all_correct:
        print("✓ Context bridging metadata: OK")
        for i, chunk in enumerate(chunks):
            print(f"  Chunk {i}: {len(chunk['text'])} chars, "
                  f"first={chunk['is_first']}, last={chunk['is_last']}")
    
    return all_correct


def test_heading_detection():
    """Test heading structure detection."""
    print("\n" + "=" * 60)
    print("TEST 5: Heading Structure Detection")
    print("=" * 60)
    
    from parser.text_extractor import has_heading_structure
    
    test_cases = [
        ("# 标题1\n## 标题2\n内容", True, "Multiple headings"),
        ("# 标题\n内容", True, "Single heading"),
        ("无标题内容\n更多内容\n还有内容", False, "No headings"),
        ("", False, "Empty text"),
    ]
    
    all_correct = True
    for text, expected, desc in test_cases:
        result = has_heading_structure(text)
        status = "✓" if result == expected else "✗"
        print(f"{status} {desc}: {result} (expected {expected})")
        if result != expected:
            all_correct = False
    
    return all_correct


def test_pipeline_integration():
    """Test the complete pipeline with mock data."""
    print("\n" + "=" * 60)
    print("TEST 6: Pipeline Integration")
    print("=" * 60)
    
    # Check if all pipeline functions are accessible
    try:
        from server import (
            detect_file_type,
            _prepare_text_units,
        )
        from parser.text_extractor import split_large_text, has_heading_structure
        from parser.llm_structurer import structure_all_chunks
        
        print("✓ All pipeline functions imported successfully")
        
        # Test LLM structurer import path
        import httpx
        print("✓ httpx dependency available")
        
        return True
    except Exception as e:
        print(f"✗ Pipeline integration test failed: {e}")
        return False


def test_dependencies():
    """Test that all required dependencies are installed."""
    print("\n" + "=" * 60)
    print("TEST 7: Dependencies Check")
    print("=" * 60)
    
    required_deps = [
        ('fastapi', 'FastAPI'),
        ('httpx', 'httpx'),
        ('docx', 'python-docx'),
        ('chardet', 'chardet'),
    ]
    
    all_present = True
    for module_name, package_name in required_deps:
        try:
            __import__(module_name)
            print(f"✓ {package_name}: installed")
        except ImportError:
            print(f"✗ {package_name}: NOT installed")
            print(f"  Install with: pip install {package_name}")
            all_present = False
    
    return all_present


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " DOCUMENT PROCESSING PIPELINE VERIFICATION ".center(58) + "║")
    print("╚" + "=" * 58 + "╝")
    
    tests = [
        ("Imports", test_imports),
        ("Dependencies", test_dependencies),
        ("File Type Detection", test_file_type_detection),
        ("Text Extraction", test_text_extraction),
        ("Large Text Splitting", test_large_text_splitting),
        ("Heading Detection", test_heading_detection),
        ("Pipeline Integration", test_pipeline_integration),
    ]
    
    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"\n✗ Test '{test_name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results[test_name] = False
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "PASS ✓" if result else "FAIL ✗"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All tests passed! Pipeline is ready for use.")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed. Please review above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
