# Knowledge Tree — 识别·解析·结构化 全流程代码参考

> 本文档汇集了项目中与文档识别、解析、结构化相关的所有核心脚本代码。
> 流程：上传文件 → 格式识别 → 文档解析(MarkItDown/MinerU) → Markdown → 节点解析 → 层级修复 → 树构建 → AI增强 → 最终JSON

---

## 目录

1. [parser/markitdown_adapter.py — 统一文档适配器](#1-parsermarkitdown_adapterpy)
2. [parser/pdf_splitter.py — PDF拆分引擎](#2-parserpdf_splitterpy)
3. [parser/text_extractor.py — 文本分块与结构检测](#3-parsertext_extractorpy)
4. [parser/llm_structurer.py — LLM文本结构化](#4-parserllm_structurerpy)
5. [tree_builder.py — Markdown→树JSON构建器](#5-tree_builderpy)
6. [hierarchy_repair.py — 多编号体系层级修复](#6-hierarchy_repairpy)
7. [node_enhancer.py — AI增强引擎](#7-node_enhancerpy)
8. [mineru_adapter/client.py — MinerU云端OCR客户端](#8-mineru_adapterclientpy)
9. [server.py — 流水线编排(核心函数)](#9-serverpy)

---

## 1. parser/markitdown_adapter.py

统一文档→Markdown适配器，封装MarkItDown库，支持20+格式(PDF、DOCX、TXT、PPTX、XLSX、HTML、CSV、EPUB等)。

```python
"""Unified document-to-Markdown adapter using MarkItDown.

Replaces text_extractor.py for DOCX/TXT and PyMuPDF fallback for PDF.
Supports 20+ formats: PDF, DOCX, TXT, PPTX, XLSX, HTML, CSV, EPUB, etc.
"""
import os
import sys

_markitdown_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "markitdown")
if _markitdown_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from markitdown import MarkItDown

_md_instance = None


def _get_markitdown() -> MarkItDown:
    """Lazy-initialize the MarkItDown singleton."""
    global _md_instance
    if _md_instance is None:
        _md_instance = MarkItDown()
    return _md_instance


def convert_to_markdown(file_path: str) -> str:
    """Convert any supported document to Markdown.

    Supports: PDF, DOCX, TXT, PPTX, XLSX, HTML, CSV, EPUB, etc.
    Raises on failure — caller decides fallback behavior.
    """
    md = _get_markitdown()
    result = md.convert(file_path)
    return result.markdown
```

---

## 2. parser/pdf_splitter.py

PDF拆分引擎：三级回退策略——嵌入式目录(TOC) → 内容正则扫描 → 固定页数分块。使用PyMuPDF(fitz)。

```python
import os
import re
from pathlib import Path
import fitz


def get_toc(pdf_path: str) -> list:
    """Read PDF table of contents."""
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    doc.close()
    return toc


def detect_chapters_by_content(pdf_path: str) -> list:
    """Detect chapter boundaries by scanning text content.
    Looks for patterns like: 第X章, 第X节, Chapter X, etc.
    Returns TOC-style list: [[level, title, page_num], ...]
    """
    doc = fitz.open(pdf_path)
    chapters = []
    patterns = [
        r'^第[一二三四五六七八九十百千\d]+[章篇编部]',
        r'^第\s*\d+\s*[章节篇]',
        r'^Chapter\s+\d+',
        r'^CHAPTER\s+\d+',
        r'^\d+\.\s+\S',
    ]
    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = get_text_sorted_by_columns(page)
        for line in text.split('\n'):
            line = line.strip()
            if not line or len(line) > 50:
                continue
            for pattern in patterns:
                if re.match(pattern, line):
                    if not chapters or chapters[-1][2] != page_num + 1:
                        chapters.append([1, line, page_num + 1])
                    break
    doc.close()
    return chapters


def detect_columns(page) -> int:
    """Detect whether a page has single or double column layout."""
    blocks = page.get_text("blocks")
    text_blocks = [b for b in blocks if b[4].strip()]
    if len(text_blocks) < 4:
        return 1
    page_width = page.rect.width
    x_centers = sorted((b[0] + b[2]) / 2 for b in text_blocks)
    max_gap = 0
    split_idx = 0
    for i in range(len(x_centers) - 1):
        gap = x_centers[i + 1] - x_centers[i]
        if gap > max_gap:
            max_gap = gap
            split_idx = i
    if max_gap > page_width * 0.3 and split_idx >= 1 and split_idx < len(x_centers) - 2:
        return 2
    return 1


def get_text_sorted_by_columns(page) -> str:
    """Extract page text, reordering dual-column layouts so left column
    comes entirely before right column."""
    if detect_columns(page) < 2:
        return page.get_text("text")
    blocks = page.get_text("blocks")
    text_blocks = [b for b in blocks if b[4].strip()]
    page_mid_x = page.rect.width / 2
    left = sorted([b for b in text_blocks if b[0] < page_mid_x], key=lambda b: b[1])
    right = sorted([b for b in text_blocks if b[0] >= page_mid_x], key=lambda b: b[1])
    left_text = "\n".join(b[4].strip() for b in left)
    right_text = "\n".join(b[4].strip() for b in right)
    return left_text + "\n" + right_text


def sanitize_filename(name: str) -> str:
    """Remove invalid characters from filename."""
    invalid_chars = '<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, '_')
    name = re.sub(r'[\s 　﻿]+', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_').strip()


def split_pdf_by_toc(pdf_path: str, output_dir: str, toc: list = None, max_size_mb: int = 200) -> list:
    """Split PDF into chapter-level files based on TOC."""
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    if toc is None:
        toc = doc.get_toc()
    if not toc:
        raise ValueError("PDF has no TOC (table of contents)")
    output_files = []
    for i in range(len(toc)):
        level, title, start_page = toc[i]
        if i + 1 < len(toc):
            end_page = toc[i + 1][2] - 1
        else:
            end_page = doc.page_count
        if start_page > end_page:
            continue
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)
        filename = sanitize_filename(title) + ".pdf"
        filepath = os.path.join(output_dir, filename)
        new_doc.save(filepath)
        new_doc.close()
        filepath = str(Path(filepath).resolve())
        if not os.path.isfile(filepath):
            import glob
            candidates = glob.glob(os.path.join(output_dir, "*.pdf"))
            if candidates:
                filepath = max(candidates, key=os.path.getmtime)
                print(f"  WARNING: expected '{filename}' not found, using '{os.path.basename(filepath)}'")
            else:
                print(f"  ERROR: file not saved: {filepath}")
                continue
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        if size_mb > max_size_mb:
            print(f"WARNING: {filename} is {size_mb:.1f}MB (>{max_size_mb}MB)")
        output_files.append(filepath)
        print(f"  {filename}  ({start_page}-{end_page}, {size_mb:.1f}MB)")
    doc.close()
    return output_files


def split_pdf(pdf_path: str, output_dir: str = None) -> list:
    """Main entry: split PDF by TOC or content detection into chapter files."""
    if output_dir is None:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        output_dir = os.path.join(os.path.dirname(pdf_path), base + "_chapters")
    print(f"Reading: {pdf_path}")
    toc = get_toc(pdf_path)
    if not toc:
        print("No TOC found, detecting chapters by content...")
        toc = detect_chapters_by_content(pdf_path)
    if not toc:
        print("No chapters detected. Splitting by fixed page ranges.")
        toc = _split_by_page_ranges(pdf_path)
    print(f"Found {len(toc)} chapters\n")
    files = split_pdf_by_toc(pdf_path, output_dir, toc)
    print(f"\nDone: {len(files)} files -> {output_dir}")
    return files


def _split_by_page_ranges(pdf_path: str, pages_per_chunk: int = 20) -> list:
    """Fallback: split PDF by fixed page ranges."""
    doc = fitz.open(pdf_path)
    toc = []
    for i in range(0, doc.page_count, pages_per_chunk):
        toc.append([1, f"Part_{i // pages_per_chunk + 1}", i + 1])
    doc.close()
    return toc
```

---

## 3. parser/text_extractor.py

文本分块工具与结构检测。分块策略：按标题→按语义段落→按段落→硬切字符。

```python
"""Extract text from Word (.docx) and plain text (.txt) files into markdown format."""
import os
import re
from pathlib import Path


def docx_to_markdown(docx_path: str) -> str:
    """Convert a .docx file to markdown, preserving heading styles, tables, and formatting."""
    from docx import Document
    from docx.table import Table as DocxTable
    doc = Document(docx_path)
    md_parts = []
    for element in iter_block_items(doc):
        if isinstance(element, DocxTable):
            md_parts.append(_docx_table_to_md(element))
        else:
            para = element
            md_line = _docx_paragraph_to_md(para)
            if md_line:
                md_parts.append(md_line)
    return '\n\n'.join(md_parts)


def iter_block_items(doc):
    """Yield paragraphs and tables in document order."""
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield Paragraph(child, doc)
        elif child.tag == qn('w:tbl'):
            yield DocxTable(child, doc)


def _docx_paragraph_to_md(para) -> str:
    """Convert a docx paragraph to markdown string."""
    style_name = para.style.name if para.style else ''
    heading_level = 0
    if style_name.startswith('Heading'):
        try:
            heading_level = int(style_name.split()[-1])
        except (ValueError, IndexError):
            heading_level = 0
    text = para.text.strip()
    if not text:
        return ''
    if heading_level == 0:
        heading_level = _detect_heading_level(text)
    formatted = _extract_inline_formatting(para)
    if heading_level > 0:
        return f"{'#' * heading_level} {formatted}"
    else:
        return formatted


def _extract_inline_formatting(para) -> str:
    """Extract bold/italic formatting from paragraph runs."""
    parts = []
    for run in para.runs:
        text = run.text
        if not text:
            continue
        if run.bold and run.italic:
            parts.append(f"***{text}***")
        elif run.bold:
            parts.append(f"**{text}**")
        elif run.italic:
            parts.append(f"*{text}*")
        else:
            parts.append(text)
    return ''.join(parts)


def _detect_heading_level(text: str) -> int:
    """Detect heading level from text patterns (Chinese + English academic)."""
    if re.match(r'^第[一二三四五六七八九十百千\d]+[章篇编]', text):
        return 1
    if re.match(r'^第[一二三四五六七八九十\d]+节', text):
        return 2
    if re.match(r'^知识点[一二三四五六七八九十\d]+', text):
        return 3
    if re.match(r'^[一二三四五六七八九十]+[、.]', text) and len(text) < 30:
        return 3
    if re.match(
        r'^(Abstract|Introduction|Related\s+Work|Methodology|Conclusion|References|Acknowledgments)\b',
        text, re.IGNORECASE,
    ):
        return 1
    if re.match(r'^\d+\.\d+\.\d+\s+\S', text):
        return 3
    if re.match(r'^\d+\.\d+\s+\S', text):
        return 2
    if re.match(r'^\d+\.\s+\S', text):
        return 1
    if len(text) < 60 and text.isupper():
        return 2
    if re.match(r'^\d+[\.、](?!\d+[\.、])\s*\S', text) and len(text) < 40:
        return 3
    return 0


def _docx_table_to_md(table) -> str:
    """Convert a docx table to markdown pipe table format."""
    rows = []
    for row in table.rows:
        cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
        rows.append(cells)
    if not rows:
        return ''
    max_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < max_cols:
            r.append('')
    lines = []
    lines.append('| ' + ' | '.join(rows[0]) + ' |')
    lines.append('|' + '|'.join(['------'] * max_cols) + '|')
    for row in rows[1:]:
        lines.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(lines)


def txt_to_markdown(txt_path: str) -> str:
    """Convert a .txt file to markdown, detecting heading patterns."""
    raw = _read_text_file(txt_path)
    lines = raw.split('\n')
    md_parts = []
    current_para = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_para:
                md_parts.append(' '.join(current_para))
                current_para = []
            continue
        heading_level = _detect_heading_level(stripped)
        if heading_level > 0 and len(stripped) < 120:
            if current_para:
                md_parts.append(' '.join(current_para))
                current_para = []
            md_parts.append(f"{'#' * heading_level} {stripped}")
        else:
            current_para.append(stripped)
    if current_para:
        md_parts.append(' '.join(current_para))
    return '\n\n'.join(md_parts)


def _read_text_file(file_path: str) -> str:
    """Read text file with encoding detection."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        pass
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        encoding = detected.get('encoding', 'utf-8') or 'utf-8'
        return raw.decode(encoding, errors='replace')
    except Exception:
        pass
    try:
        with open(file_path, 'r', encoding='gbk') as f:
            return f.read()
    except UnicodeDecodeError:
        pass
    with open(file_path, 'r', encoding='latin-1') as f:
        return f.read()


def has_heading_structure(md_text: str) -> bool:
    """Check if markdown text has meaningful heading structure."""
    heading_count = 0
    for line in md_text.split('\n'):
        if re.match(r'^#{1,3}\s+\S', line.strip()):
            heading_count += 1
    return heading_count >= 2


def split_large_text(md_text: str, max_chars: int = 8000) -> list[dict]:
    """Split large markdown text into chunks for LLM processing."""
    if len(md_text) <= max_chars:
        return [{"text": md_text, "index": 0, "total": 1, "is_first": True, "is_last": True}]
    chunks = _split_by_headers(md_text, max_chars)
    if len(chunks) > 1:
        return chunks
    return _split_by_paragraphs(md_text, max_chars)


def _split_by_headers(md_text: str, max_chars: int) -> list[dict]:
    """Split markdown by top-level headers."""
    header_pattern = re.compile(r'^(#{1,2})\s+(.+)', re.MULTILINE)
    matches = list(header_pattern.finditer(md_text))
    if len(matches) < 2:
        return []
    chunks = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        chunk_text = md_text[start:end].strip()
        if chunk_text:
            chunks.append(chunk_text)
    result = []
    for chunk_text in chunks:
        if len(chunk_text) <= max_chars:
            result.append(chunk_text)
        else:
            result.extend([c["text"] for c in _split_by_paragraphs(chunk_text, max_chars)])
    total = len(result)
    return [
        {"text": text, "index": i, "total": total, "is_first": i == 0, "is_last": i == total - 1}
        for i, text in enumerate(result)
    ]


def _split_by_paragraphs(md_text: str, max_chars: int) -> list[dict]:
    """Split text at semantic boundaries: headings first, then triple blank lines."""
    cut_candidates = [0]
    for m in re.finditer(r'^#{1,3}\s+', md_text, re.MULTILINE):
        cut_candidates.append(m.start())
    for m in re.finditer(r'\n\n\n+', md_text):
        cut_candidates.append(m.start())
    cut_candidates = sorted(set(cut_candidates))
    cut_candidates.append(len(md_text))
    segments = [(cut_candidates[i], cut_candidates[i + 1]) for i in range(len(cut_candidates) - 1)]
    chunks = []
    chunk_start = 0
    chunk_len = 0
    for seg_start, seg_end in segments:
        seg_len = seg_end - seg_start
        if chunk_len > 0 and chunk_len + seg_len > max_chars:
            chunks.append(md_text[chunk_start:seg_start].strip())
            chunk_start = seg_start
            chunk_len = seg_len
        else:
            chunk_len += seg_len
    if chunk_len > 0:
        chunks.append(md_text[chunk_start:].strip())
    result = []
    for chunk_text in chunks:
        if len(chunk_text) <= max_chars:
            result.append(chunk_text)
        else:
            result.extend(_split_oversized_chunk(chunk_text, max_chars))
    total = len(result)
    return [
        {"text": text, "index": i, "total": total, "is_first": i == 0, "is_last": i == total - 1}
        for i, text in enumerate(result)
    ]


def _split_oversized_chunk(text: str, max_chars: int) -> list[str]:
    """Split a single oversized chunk by paragraph, then by character count as last resort."""
    paragraphs = text.split('\n\n')
    chunks = []
    current = []
    current_len = 0
    for para in paragraphs:
        para_len = len(para)
        sep = 2 if current else 0
        if current and current_len + sep + para_len > max_chars:
            chunks.append('\n\n'.join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += sep + para_len
    if current:
        chunks.append('\n\n'.join(current))
    result = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            for i in range(0, len(chunk), max_chars):
                result.append(chunk[i:i + max_chars].strip())
    return result
```

---

## 4. parser/llm_structurer.py

LLM文本结构化：将无标题结构的原始文本交给DeepSeek分析，生成树形JSON。支持跨片段上下文桥接。

```python
"""LLM-based text structuring: convert raw text chunks into structured tree JSON."""
import json
import os
import asyncio
import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
API_TIMEOUT = 30
REQUEST_DELAY = 0.5


def build_structure_prompt(chunk_text: str, chunk_meta: dict, prev_tail_context: str = "") -> str:
    """Build prompt for LLM to structure raw text into a tree."""
    meta_info = ""
    if chunk_meta.get("total", 1) > 1:
        meta_info = f"\n这是第 {chunk_meta['index'] + 1}/{chunk_meta['total']} 个文本片段。"
        if chunk_meta.get("is_first"):
            meta_info += " 这是文档的开头部分。"
        if chunk_meta.get("is_last"):
            meta_info += " 这是文档的结尾部分。"
    context_block = ""
    if prev_tail_context:
        context_block = f"""
## 上文衔接信息
{prev_tail_context}

请判断：当前文本是上文内容的【延续】还是【新主题的开始】？
- 如果是延续：当前片段的根节点 title 应与上文末尾知识点形成自然承接
- 如果是新主题：根节点 title 应体现转折，children 第一个节点可以包含"承接上文"的过渡说明
"""
    return f"""你是一位教育学学科专家，擅长从原始文本中提取知识结构。

## 任务
分析以下文本内容，将其组织成清晰的知识树结构。识别主题、子主题和关键知识点。

{meta_info}
{context_block}
## 原始文本
{chunk_text[:6000]}

## 输出要求
严格输出JSON，不要输出任何其他内容:
{{
  "title": "本片段的核心主题标题",
  "children": [
    {{
      "title": "子主题1的标题",
      "content": "该子主题的核心内容（100-300字）",
      "children": [
        {{
          "title": "更细的知识点标题",
          "content": "具体知识点内容",
          "children": []
        }}
      ]
    }}
  ]
}}

## 规则
- title: 简洁明确，反映内容主旨
- content: 保留原文的关键信息，适当精简但不丢失要点
- children: 按逻辑关系组织，体现层级结构
- 层级深度建议2-4层，不要太深
- 空children用空数组[]"""


async def structure_text_chunk(client: httpx.AsyncClient, chunk: dict,
                                prev_tail_context: str = "", api_key: str | None = None) -> tuple[dict, str]:
    """Call LLM to structure one text chunk. Returns (tree_dict, tail_context)."""
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("DeepSeek API key is required.")
    prompt = build_structure_prompt(chunk["text"], chunk, prev_tail_context)
    payload = {
        "model": MODEL, "max_tokens": 2000, "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}]
    }
    resp = await client.post(OPENAI_BASE_URL, json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tree = json.loads(content)
    tail_context = _extract_tail_context(tree)
    return tree, tail_context


def _extract_tail_context(tree: dict) -> str:
    """Extract trailing leaf nodes for cross-chunk context bridging."""
    leaves = []
    def _walk(node):
        children = node.get("children", [])
        if not children:
            leaves.append({"title": node.get("title", ""), "content": node.get("content", "")})
        else:
            for child in children:
                _walk(child)
    _walk(tree)
    if not leaves:
        return ""
    tail = leaves[-3:]
    lines = ["上文末尾知识点："]
    for i, leaf in enumerate(tail, 1):
        text = (leaf["content"] or "")[:150]
        lines.append(f"{i}. {leaf['title']}: {text}" if text else f"{i}. {leaf['title']}")
    return "\n".join(lines)


async def structure_all_chunks(chunks: list[dict], api_key: str | None = None) -> list[dict]:
    """Sequentially process chunks with context bridging."""
    trees = []
    prev_tail_context = ""
    async with httpx.AsyncClient() as client:
        for i, chunk in enumerate(chunks):
            try:
                tree, tail_context = await structure_text_chunk(client, chunk, prev_tail_context, api_key=api_key)
                trees.append(tree)
                prev_tail_context = tail_context
            except Exception as e:
                print(f"  WARNING: LLM structuring failed for chunk {i}: {e}")
                fallback = _fallback_tree(chunk)
                trees.append(fallback)
                prev_tail_context = _extract_tail_context(fallback)
            if i < len(chunks) - 1:
                await asyncio.sleep(REQUEST_DELAY)
    return trees


def _fallback_tree(chunk: dict) -> dict:
    """Create a simple tree from raw text when LLM fails."""
    text = chunk["text"]
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    children = []
    for j, para in enumerate(paragraphs[:10]):
        title = para[:30].replace('\n', ' ')
        if len(para) > 30:
            title += "..."
        children.append({"title": title, "content": para[:500], "children": []})
    return {"title": f"文本片段 {chunk['index'] + 1}", "children": children}
```

---

## 5. tree_builder.py

Markdown→树JSON核心构建器。逐行解析Markdown，识别标题层级，保留Mermaid/表格/LaTeX，过滤噪声，栈式构建树。

```python
"""333教育综合 - MD → 知识树 Builder (并行版)"""
import re
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

NOISE_EXACT = {"333", "教育综合", "考研大纲知识清单", "前言", "参考答案", "【参考答案】", "精华提要", "知识导图"}
NOISE_CONTAINS = ["丹丹", "攻略", "导学", "自测表", "考情分析", "考点延伸", "知识导图", "公众号", "微信", "热点话题", "参考答案"]

def is_noise(title):
    t = title.strip()
    if t in NOISE_EXACT:
        return True
    return any(kw in t for kw in NOISE_CONTAINS)

_TOC_CHAPTER_RE = re.compile(r'^(?:#\s+)?第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+章\s+.+/\s*\d+$')
_TOC_SECTION_RE = re.compile(r'^(?:#\s+)?第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+节\s+.+/\s*\d+$')
_TOC_ITEM_RE = re.compile(r'^(?:本章自测表|参考文献|思考题|习题)\s*/\s*\d+$')
_CHAPTER_TITLE_RE = re.compile(r'^第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+章$')

def detect_toc_section(lines, start_idx):
    """检测从 start_idx 开始是否是目录页内容。"""
    if start_idx >= len(lines):
        return False, start_idx
    line = lines[start_idx].strip()
    if not _TOC_CHAPTER_RE.match(line):
        return False, start_idx
    idx = start_idx + 1
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            idx += 1; continue
        if _TOC_SECTION_RE.match(line) or _TOC_ITEM_RE.match(line):
            idx += 1; continue
        if _TOC_CHAPTER_RE.match(line):
            idx += 1; continue
        break
    return True, idx

def merge_split_chapter_titles(nodes):
    """合并被拆分的章节标题。如 '第一章' + '心理发展与教育' → '第一章 心理发展与教育'"""
    if not nodes:
        return nodes
    merged = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        title = node.get('title', '')
        if _CHAPTER_TITLE_RE.match(title) and i + 1 < len(nodes):
            next_node = nodes[i + 1]
            next_title = next_node.get('title', '')
            if (next_title and
                not re.match(r'^第[一二三四五六七八九十百千\d]+[章节部分]', next_title) and
                not re.match(r'^[一二三四五六七八九十]+[、，,]', next_title) and
                not re.match(r'^[（(【〔][一二三四五六七八九十\d]+[）)】〔]', next_title) and
                not re.match(r'^\d+(?:\.\d+)*', next_title) and
                not re.match(r'^[①-⑳]', next_title) and
                len(next_title) < 20):
                merged_title = f"{title} {next_title}"
                merged_node = dict(node)
                merged_node['title'] = merged_title
                merged.append(merged_node)
                i += 2; continue
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
    if re.match(r'^\d+\.\d+\.\d+\s', t): return 3
    if re.match(r'^\d+\.\d+\s', t): return 2
    if re.match(r'^\d+\.\s', t): return 5
    if re.match(r'^\d+[、]', t): return 5
    if re.match(r'^\d+\.\s*\S', t): return 5
    return min(hashes, 5)

_EXPLICIT_LEVEL_RE = re.compile(
    r'^(第[一二三四五六七八九十百\d]+[章节]|知识点[一二三四五六七八九十\d]+|[（(][\d一二三四五六七八九十]+[）)]|\d+[\.、])'
)
_CHAPTER_RE = re.compile(r'^第[一二三四五六七八九十百\d]+章')
_SECTION_RE = re.compile(r'^第[一二三四五六七八九十百\d]+节')
_EN_CHAPTER_RE = re.compile(r'^\d+\.\s')
_EN_SECTION_RE = re.compile(r'^\d+\.\d+\s')

def _is_chapter(t):
    return bool(_CHAPTER_RE.match(t) or _EN_CHAPTER_RE.match(t))
def _is_section(t):
    return bool(_SECTION_RE.match(t) or _EN_SECTION_RE.match(t))

def adjust_standalone_levels(nodes):
    """Demote headers without explicit level patterns that appear inside a chapter or section."""
    if not nodes:
        return nodes
    active_chapter_level = 0
    active_section_level = 0
    first_section = None
    for node in nodes:
        t = node['title'].strip()
        if _is_chapter(t): break
        if _is_section(t):
            first_section = node; break
    if first_section is not None:
        active_section_level = 2
    for node in nodes:
        t = node['title'].strip()
        if _is_chapter(t):
            active_chapter_level = 1; active_section_level = 0
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
                i += 2; continue
        result.append(node)
        i += 1
    return result

CAPTION_RE = re.compile(
    r'^(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|图\s*\d+|表\s*\d+)[：:．.]?\s*(.+)', re.IGNORECASE,
)

def parse_md_to_nodes(md_text):
    """Core parser. Splits markdown by # headers into nodes.
    Preserves mermaid blocks, HTML tables, pipe tables, LaTeX formulas."""
    lines = md_text.split('\n')
    nodes = []
    current = None
    buf = []
    in_mermaid = False
    in_table = False
    in_html_table = False
    in_formula = False
    i = 0
    while i < len(lines):
        line = lines[i]
        is_toc, end_idx = detect_toc_section(lines, i)
        if is_toc:
            i = end_idx; continue
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            if current is not None:
                current['content'] = '\n'.join(buf).strip()
                nodes.append(current)
                buf = []
                in_mermaid = False; in_table = False; in_html_table = False; in_formula = False
            hashes = len(m.group(1))
            title = m.group(2).strip()
            if is_noise(title):
                current = None; i += 1; continue
            current = {'title': title, 'level': get_level(title, hashes), 'content': '', 'children': [], 'captions': []}
        else:
            s = line.strip()
            if current is None:
                i += 1; continue
            if s.startswith('```mermaid'):
                in_mermaid = True; buf.append(line); i += 1; continue
            if in_mermaid:
                buf.append(line)
                if s == '```': in_mermaid = False
                i += 1; continue
            if s.lower().startswith('<table'):
                in_html_table = True; buf.append(line)
                if s.lower().endswith('</table>'): in_html_table = False
                i += 1; continue
            if in_html_table:
                buf.append(line)
                if s.lower().endswith('</table>'): in_html_table = False
                i += 1; continue
            if re.match(r'^\|', s):
                in_table = True; buf.append(line); i += 1; continue
            elif in_table and s == '':
                in_table = False; i += 1; continue
            else:
                in_table = False
            if s.startswith('$$'):
                in_formula = not in_formula; buf.append(line); i += 1; continue
            if in_formula:
                buf.append(line); i += 1; continue
            cap_m = CAPTION_RE.match(s)
            if cap_m:
                label = cap_m.group(1).strip()
                caption_text = cap_m.group(2).strip()
                buf.append(f"[图表] {label}: {caption_text}")
                current.setdefault('captions', []).append({"label": label, "text": caption_text})
                i += 1; continue
            if s and not s.startswith('![') and not s.startswith('<') and not s.startswith('```'):
                buf.append(line)
        i += 1
    if current is not None:
        current['content'] = '\n'.join(buf).strip()
        nodes.append(current)
    nodes = merge_split_chapter_titles(nodes)
    return nodes

def build_tree(nodes):
    """Stack-based tree construction. Pops stack when current level <= stack top level."""
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
    """Scan tree for hierarchy anomalies (level gaps > 1, 5+ consecutive siblings)."""
    anomalies = []
    for i, node in enumerate(nodes):
        level = node.get('level', 0)
        title = node.get('title', '')
        if parent_level > 0 and level > parent_level + 1:
            anomalies.append({"node": node, "title": title, "level": level,
                "expected_max": parent_level + 1, "reason": f"level jump {parent_level} -> {level}"})
        if i >= 4:
            prev_levels = [nodes[j].get('level', 0) for j in range(i-4, i)]
            if all(l == level for l in prev_levels) and level == nodes[i-1].get('level', 0):
                anomalies.append({"node": node, "title": title, "level": level,
                    "reason": f"5+ consecutive L{level} siblings"})
        children = node.get('children', [])
        if children:
            anomalies.extend(_detect_hierarchy_anomalies(children, level))
    return anomalies

def validate_and_repair_hierarchy(roots, api_key):
    """Use LLM to validate and repair hierarchy anomalies. Falls back to original on failure."""
    anomalies = _detect_hierarchy_anomalies(roots)
    if not anomalies:
        return roots
    seen = set(); unique = []
    for a in anomalies:
        key = (a['title'], a['level'])
        if key not in seen:
            seen.add(key); unique.append(a)
    if len(unique) > 30:
        unique = unique[:30]
    items = []
    for i, a in enumerate(unique):
        items.append(f"{i+1}. \"{a['title']}\" (当前level={a['level']}, 问题: {a['reason']})")
    prompt = f"""以下是文档标题层级识别结果中存在问题的条目。请判断每个标题的正确层级（1-5）。
标题列表:
{chr(10).join(items)}
规则:
- level 1 = 章/Chapter, level 2 = 节/Section, level 3 = 知识点/子节
- level 4 = 子知识点, level 5 = 细节/条目
严格输出JSON数组，每个元素包含 "idx" 和 "correct_level"。
只输出确实需要修正的条目。"""
    import httpx
    try:
        resp = httpx.post("https://api.deepseek.com/chat/completions",
            json={"model": "deepseek-v4-flash", "max_tokens": 500, "temperature": 0.1,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, timeout=20)
        if resp.status_code != 200:
            return roots
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        corrections = result if isinstance(result, list) else result.get("corrections", result.get("repairs", []))
        import copy
        roots_copy = copy.deepcopy(roots)
        unique_copy = []; seen_copy = set()
        for a in _detect_hierarchy_anomalies(roots_copy):
            key = (a['title'], a['level'])
            if key not in seen_copy:
                seen_copy.add(key); unique_copy.append(a)
        unique_copy = unique_copy[:30]
        fixed = 0
        for c in corrections:
            idx = c.get('idx'); new_level = c.get('correct_level')
            if idx is not None and new_level and 1 <= new_level <= 5 and idx < len(unique_copy):
                a = unique_copy[idx]
                if new_level != a['level']:
                    a['node']['level'] = new_level; fixed += 1
        if fixed > 0:
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
```

---

## 6. hierarchy_repair.py

多编号体系层级修复引擎。识别中文/阿拉伯/圆圈等12种编号模式，通过语义栈算法统一层级。

```python
"""Multi-Number Hierarchy Repair Engine"""
import re
from collections import OrderedDict

_CN_NUM_MAP = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '百': 100, '千': 1000, '万': 10000,
}

def chinese_to_int(cn: str) -> int:
    """Convert Chinese number string to integer. 一→1, 十二→12, 一百二十三→123"""
    if not cn: return 0
    result = 0; current = 0
    for ch in cn:
        if ch in ('零', '〇'): continue
        val = _CN_NUM_MAP.get(ch)
        if val is None: return 0
        if val >= 10:
            if current == 0: current = 1
            result += current * val; current = 0
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

NUMBER_PATTERNS = OrderedDict({
    'chapter_cn': re.compile(r'^第([一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+)章'),
    'section_cn': re.compile(r'^第([一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+)节'),
    'part_cn': re.compile(r'^第([一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+)部分'),
    'chapter_en': re.compile(r'^(?:Chapter|CHAPTER)\s+(\d+)', re.IGNORECASE),
    'section_en': re.compile(r'^(?:Section|SECTION)\s+(\d+)', re.IGNORECASE),
    'part_en': re.compile(r'^(?:Part|PART)\s+(\d+)', re.IGNORECASE),
    'chinese_dotted': re.compile(r'^([一二三四五六七八九十]+)[、，,]\s*'),
    'chinese_bracket': re.compile(r'^[（(【〔]\s*([一二三四五六七八九十]+)\s*[）)】〕]'),
    'arabic_dotted': re.compile(r'^(\d+(?:\.\d+)*)(?:[\.、，,]|\s+)'),
    'arabic_bracket': re.compile(r'^[（(【〔]\s*(\d+)\s*[）)】〕]'),
    'arabic_right_bracket': re.compile(r'^(\d+)\s*[）)]'),
    'circle_number': re.compile(r'^([①-⑳])'),
    'knowledge_point': re.compile(r'^知识点([一二三四五六七八九十\d]+)'),
})

_ANY_NUMBERING = re.compile(
    r'^(第[一二三四五六七八九十百千\d]+[章节部分]|'
    r'(?:Chapter|CHAPTER|Section|SECTION|Part|PART)\s+\d+|'
    r'[一二三四五六七八九十]+[、，,]|'
    r'[（(【\[〔][一二三四五六七八九十\d]+[）)】\]〕]|'
    r'\d+(?:\.\d+)*[\.、，,)]|'
    r'[①-⑳]|'
    r'知识点[一二三四五六七八九十\d]+)')

TYPE_TO_SEMANTIC_LEVEL = {
    'chapter_cn': 1, 'chapter_en': 1, 'part_cn': 1, 'part_en': 1,
    'section_cn': 2, 'section_en': 2,
    'chinese_dotted': 3, 'knowledge_point': 3,
    'chinese_bracket': 4,
    'arabic_dotted': None,  # dynamic
    'arabic_bracket': 6, 'arabic_right_bracket': 6,
    'circle_number': 7,
}

_TYPE_CATEGORY = {
    'chapter_cn': 1, 'chapter_en': 1, 'part_cn': 1, 'part_en': 1,
    'section_cn': 2, 'section_en': 2,
    'chinese_dotted': 3, 'knowledge_point': 3,
    'chinese_bracket': 4,
    'arabic_dotted': 5,
    'arabic_bracket': 6, 'arabic_right_bracket': 6,
    'circle_number': 7,
}

def _extract_num(val_str: str) -> int:
    if val_str in _CIRCLE_MAP: return _CIRCLE_MAP[val_str]
    if val_str.isdigit(): return int(val_str)
    if len(val_str) == 1 and not val_str.isalnum(): return 0
    return chinese_to_int(val_str)

def has_any_numbering(title: str) -> bool:
    return bool(_ANY_NUMBERING.match(title.strip()))

def detect_numbering_type(title: str) -> dict | None:
    """Returns {type, path, semantic_level, raw_prefix} or None."""
    t = title.strip()
    if not t: return None
    for ntype, pattern in NUMBER_PATTERNS.items():
        m = pattern.match(t)
        if not m: continue
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
        return {'type': ntype, 'path': path, 'semantic_level': semantic_level, 'raw_prefix': raw_prefix}
    return None

def extract_number_path(title: str) -> list[int] | None:
    info = detect_numbering_type(title)
    return info['path'] if info else None

def infer_semantic_level(title: str) -> int | None:
    info = detect_numbering_type(title)
    return info['semantic_level'] if info else None

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
    result = text
    for pattern, replacement in _BRACKET_NORMALIZE:
        result = pattern.sub(replacement, result)
    return result

def normalize_node_numbering(node: dict) -> dict:
    result = dict(node)
    if 'title' in result:
        result['title'] = normalize_numbering(result['title'])
    return result

def normalize_nodes_numbering(nodes: list[dict]) -> list[dict]:
    return [normalize_node_numbering(n) for n in nodes]

def repair_hierarchy(nodes: list[dict]) -> list[dict]:
    """Core algorithm: semantic stack construction.
    1. Detect numbering types for all nodes.
    2. Compute rank as (category, path_depth) tuples.
    3. Pop stack when stack-top rank >= current rank.
    4. Fix level gaps > 2 between consecutive numbered nodes.
    """
    if not nodes: return nodes
    # Phase 1: Detect
    for node in nodes:
        title = node.get('title', '')
        info = detect_numbering_type(normalize_numbering(title))
        if info:
            node['semantic_level'] = info['semantic_level']
            node['numbering_type'] = info['type']
            node['number_path'] = info['path']
            node['raw_prefix'] = info['raw_prefix']
        else:
            node['semantic_level'] = None; node['numbering_type'] = None
            node['number_path'] = None; node['raw_prefix'] = None
    # Phase 2: Compute rank
    for node in nodes:
        ntype = node.get('numbering_type')
        if ntype is None:
            node['_rank_category'] = None; node['_rank_depth'] = 0; continue
        cat = _TYPE_CATEGORY.get(ntype, 99)
        path = node.get('number_path', [])
        node['_rank_category'] = cat; node['_rank_depth'] = len(path)
    # Phase 3: Semantic stack
    semantic_stack = []
    for i, node in enumerate(nodes):
        cat = node.get('_rank_category')
        depth = node.get('_rank_depth', 0)
        if cat is not None:
            while semantic_stack:
                s_cat, s_depth = semantic_stack[-1][1], semantic_stack[-1][2]
                if s_cat > cat: semantic_stack.pop()
                elif s_cat == cat and s_depth >= depth: semantic_stack.pop()
                else: break
            effective_level = max(1, len(semantic_stack) + 1)
            node['level'] = effective_level
            semantic_stack.append((effective_level, cat, depth, i))
        else:
            if semantic_stack:
                parent_eff, _, _, _ = semantic_stack[-1]
                effective_level = parent_eff + 1
            else:
                effective_level = max(1, node.get('level', 1))
            node['level'] = effective_level
            semantic_stack.append((effective_level, 99, 0, i))
    # Phase 4: Fix gaps
    _fix_level_gaps(nodes)
    for node in nodes:
        node.pop('_rank_category', None); node.pop('_rank_depth', None)
    return nodes

def _fix_level_gaps(nodes: list[dict]):
    prev_numbered_level = 0
    for node in nodes:
        if node.get('numbering_type') is None: continue
        cur = node['level']
        if prev_numbered_level > 0 and cur - prev_numbered_level > 2:
            node['level'] = prev_numbered_level + 1
        prev_numbered_level = cur

def apply_hierarchy_repair(nodes: list[dict]) -> list[dict]:
    """Full pipeline: normalize → repair → return."""
    nodes = normalize_nodes_numbering(nodes)
    nodes = repair_hierarchy(nodes)
    return nodes

def get_hierarchy_stats(nodes: list[dict]) -> dict:
    types = {}; levels = {}; numbered = 0; standalone = 0
    for node in nodes:
        ntype = node.get('numbering_type')
        if ntype: numbered += 1; types[ntype] = types.get(ntype, 0) + 1
        else: standalone += 1
        level = node.get('level', 0)
        levels[level] = levels.get(level, 0) + 1
    return {'total': len(nodes), 'numbered': numbered, 'standalone': standalone,
            'numbering_types': types, 'level_distribution': dict(sorted(levels.items()))}
```

---

## 7. node_enhancer.py

AI增强引擎：为每个节点生成摘要、关键词、考点、Mermaid流程图、表格。支持文档画像检测、节点角色推断、后序遍历增强、结构清理。

```python
"""通用 Knowledge Node Enhancer (并行版 + 全局上下文)"""
import json
import os
import re
import asyncio
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL           = "deepseek-v4-flash"
SUBJECT_CONFIG = {"name": "教育学", "exam": "333教育综合考研"}
ENHANCE_LEVELS = {1: True, 2: True, 3: True, 4: True, 5: False}
MAX_CONCURRENT  = 5
REQUEST_DELAY   = 0.3

PROFILE_RULES = {
    "exam_outline": ["考纲", "大纲", "提纲", "考点", "真题", "考试", "背诵", "重点"],
    "book_interpretation": ["导读", "解读", "评析", "精读", "书评", "作者", "著作", "文本"],
    "textbook": ["原理", "教程", "教材", "理论", "概论", "基础", "导论", "方法论"],
}

ROLE_PATTERNS = [
    (r'衔接|过渡', "bridge"), (r'^第[一二三四五六七八九十百\d]+章', "chapter"),
    (r'^第[一二三四五六七八九十百\d]+节', "section"), (r'^知识点', "knowledge_point"),
    (r'案例|例题|例证', "case"), (r'方法|步骤|路径|策略', "method"),
    (r'比较|对比|区别|联系', "comparison"), (r'定义|概念|内涵', "concept"),
]

def load_all_trees(tree_dir: str) -> list:
    files = sorted(f for f in os.listdir(tree_dir) if f.endswith(".json"))
    trees = []
    for f in files:
        path = os.path.join(tree_dir, f)
        with open(path, encoding='utf-8') as fh:
            trees.append((f, json.load(fh)))
    return trees

def build_global_index(trees: list) -> list:
    index = []
    for filename, tree in trees:
        for child in tree.get('children', []):
            index.append({
                "file": filename, "title": child.get('title', ''),
                "level": child.get('level', 0),
                "preview": (child.get('content', '') or '')[:200],
            })
    return index

def get_surrounding_context(global_index: list, current_file: str, current_title: str) -> str:
    pos = -1
    for i, item in enumerate(global_index):
        if item['file'] == current_file and item['title'] == current_title:
            pos = i; break
    if pos == -1: return ""
    parts = []
    if pos > 0:
        prev = global_index[pos - 1]
        parts.append(f"【前文衔接】上一节: {prev['title']} (来自{prev['file']})")
        if prev['preview']: parts.append(f"  摘要: {prev['preview'][:150]}")
    if pos < len(global_index) - 1:
        nxt = global_index[pos + 1]
        parts.append(f"【后文衔接】下一节: {nxt['title']} (来自{nxt['file']})")
        if nxt['preview']: parts.append(f"  摘要: {nxt['preview'][:150]}")
    return '\n'.join(parts)

def _sample_tree_text(tree: dict, max_chars: int = 2500) -> str:
    parts = [tree.get("title", "")]
    def _walk(node: dict):
        if len(" ".join(parts)) >= max_chars: return
        title = node.get("title", "")
        content = (node.get("content", "") or "")[:180]
        if title: parts.append(title)
        if content: parts.append(content)
        for child in node.get("children", [])[:4]: _walk(child)
    for child in tree.get("children", [])[:6]: _walk(child)
    return "\n".join(parts)[:max_chars]

def detect_document_profile(tree: dict) -> dict:
    """Detect content style: exam_outline, book_interpretation, textbook, generic."""
    text = _sample_tree_text(tree)
    scores = {name: 0 for name in PROFILE_RULES}
    for profile_name, keywords in PROFILE_RULES.items():
        for keyword in keywords:
            scores[profile_name] += text.count(keyword)
    profile_name = max(scores, key=scores.get)
    if scores[profile_name] == 0: profile_name = "generic"
    profiles = {
        "exam_outline": {"name": "exam_outline", "label": "考试提纲",
            "summary_focus": "提炼考纲重点、命题倾向、记忆抓手和复习顺序",
            "keyword_focus": "优先输出高频考点词、易混概念、答题术语",
            "exam_focus": "考点要贴近考试题型、频率和答题方式"},
        "book_interpretation": {"name": "book_interpretation", "label": "书籍解读",
            "summary_focus": "概括作者论证主线、章节意图、观点递进与前后呼应",
            "keyword_focus": "优先输出核心概念、作者观点、章节主旨词",
            "exam_focus": "考点可转化为阅读理解、论述题、观点辨析题"},
        "textbook": {"name": "textbook", "label": "理论教材",
            "summary_focus": "突出概念定义、逻辑结构、知识框架和应用边界",
            "keyword_focus": "优先输出概念、理论、方法、模型",
            "exam_focus": "考点聚焦概念辨析、框架记忆和应用分析"},
        "generic": {"name": "generic", "label": "通用资料",
            "summary_focus": "提炼主题、结构、要点及其逻辑关系",
            "keyword_focus": "输出最能代表内容主题的稳定关键词",
            "exam_focus": "如果适合考试化表达再给考点，否则保持通用学习导向"},
    }
    return profiles[profile_name]

def infer_node_role(node: dict) -> str:
    title = node.get("title", "").strip()
    for pattern, role in ROLE_PATTERNS:
        if re.search(pattern, title): return role
    children = node.get("children", [])
    content = node.get("content", "").strip()
    if children and not content: return "outline"
    if content and len(content) > 220: return "explanation"
    return "concept"

def describe_children_structure(node: dict) -> str:
    children = node.get("children", [])
    if not children: return "当前节点暂无子节点，按单点内容概括即可。"
    child_titles = [c.get("title", "") for c in children[:8]]
    order_hint = "并列展开"
    if any(re.match(r'^第[一二三四五六七八九十百\d]+', title) for title in child_titles):
        order_hint = "章节递进"
    elif any(re.match(r'^\d+[\.、]', title) for title in child_titles):
        order_hint = "编号分点"
    elif any("比较" in title or "对比" in title for title in child_titles):
        order_hint = "对比分析"
    return f"子节点结构倾向: {order_hint}；子节点示例: {' / '.join(child_titles[:5])}"

def build_prompt(node: dict, subject_config: dict, context_text: str,
                 surrounding_ctx: str = "", document_profile: dict | None = None) -> str:
    document_profile = document_profile or detect_document_profile({"title": node.get("title", ""), "children": [node]})
    node_role = infer_node_role(node)
    structure_guidance = describe_children_structure(node)
    ctx_block = ""
    if surrounding_ctx:
        ctx_block = f"\n## 上下文衔接\n{surrounding_ctx}\n"
    content_len = len(context_text)
    if content_len < 500: summary_guide = "50-80字，提炼核心论点和关键概念名称"
    elif content_len > 1000: summary_guide = "150-200字，涵盖核心论点、关键细节和概念名称"
    else: summary_guide = "100-150字，抓住核心论点和关键细节"
    return f"""你是{subject_config['name']}学科专家，专为{subject_config['exam']}备考服务。

## 知识节点
标题: {node['title']}
层级: L{node['level']}
资料类型: {document_profile['label']}
节点角色: {node_role}
结构提示: {structure_guidance}
{ctx_block}
## 内容
{context_text[:2000]}

## 输出要求
严格输出JSON，不要输出任何其他内容:
{{
  "summary": "{summary_guide}。避免空话，必须包含具体概念名称",
  "keywords": [{{"term": "核心术语", "context": "该术语在本文中的含义"}}],
  "highlights": [{{"text": "关键片段(20-60字)", "importance": "high", "type": "definition/theory/argument"}}],
  "exam_points": [{{"point": "考点描述", "type": "选择题/论述题", "frequency": "高频/中频/低频"}}],
  "mermaid": "graph TD...",
  "tables": ["markdown表格"],
  "node_role": "chapter/section/knowledge_point/concept/method/case/comparison/bridge/outline/explanation",
  "structure_hint": "总分/并列/递进/对比/因果/时间线/桥接"
}}
- summary重点: {document_profile['summary_focus']}
- keywords重点: {document_profile['keyword_focus']}。输出3-6个对象
- highlights: 从原文提取3-8个关键片段
- exam_points重点: {document_profile['exam_focus']}。0-3个
- mermaid: 适合流程图时生成，否则空字符串
- tables: 适合表格时生成，否则空数组"""

def get_context_text(node: dict) -> str:
    children = node.get('children', [])
    if not children:
        return node.get('content', '') or node.get('title', '')
    parts = ["本节包含以下内容:"]
    own = node.get('content', '')
    if own: parts.insert(0, f"节点内容: {own[:300]}\n")
    for child in children:
        summary = child.get('summary', '')
        content = child.get('content', '')[:150]
        line = f"- {child['title']}"
        if summary: line += f": {summary[:80]}"
        elif content: line += f": {content}"
        parts.append(line)
    return '\n'.join(parts)

def should_enhance(node: dict) -> bool:
    if not ENHANCE_LEVELS.get(node.get('level', 99), False): return False
    if node.get('summary'): return False
    if not node.get('children') and not node.get('content', '').strip(): return False
    return True

def collect_postorder(node: dict, result: list):
    for child in node.get('children', []): collect_postorder(child, result)
    if should_enhance(node): result.append(node)

async def enhance_one(client: httpx.AsyncClient, node: dict,
                      semaphore: asyncio.Semaphore, surrounding_ctx: str = "") -> None:
    async with semaphore:
        await asyncio.sleep(REQUEST_DELAY)
        prompt = build_prompt(node, SUBJECT_CONFIG, get_context_text(node), surrounding_ctx)
        payload = {
            "model": MODEL, "max_tokens": 1500, "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]
        }
        try:
            resp = await client.post(OPENAI_BASE_URL, json=payload,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                timeout=30)
            if resp.status_code != 200:
                print(f"  FAIL [{resp.status_code}] {node['title'][:30]}"); return
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            result = json.loads(raw)
            node['summary'] = result.get('summary', '')
            node['keywords'] = result.get('keywords', [])
            node['exam_points'] = result.get('exam_points', [])
            node['mermaid'] = result.get('mermaid', '') or ''
            node['tables'] = result.get('tables', []) or []
            node['highlights'] = result.get('highlights', []) or []
            print(f"  OK [L{node['level']}] {node['title'][:40]}")
        except json.JSONDecodeError:
            print(f"  WARN JSON parse fail: {node['title'][:30]}")
        except Exception as e:
            print(f"  FAIL {node['title'][:30]}: {e}")

async def enhance_tree_async(tree: dict, global_index: list, current_file: str) -> dict:
    to_enhance = []
    for ch in tree.get('children', []): collect_postorder(ch, to_enhance)
    if not to_enhance: return tree
    by_level = defaultdict(list)
    for node in to_enhance: by_level[node['level']].append(node)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        for level in sorted(by_level.keys(), reverse=True):
            batch = by_level[level]
            tasks = []
            for node in batch:
                root_title = _find_root_title(tree, node)
                ctx = get_surrounding_context(global_index, current_file, root_title)
                tasks.append(enhance_one(client, node, semaphore, ctx))
            await asyncio.gather(*tasks)
    return tree

def _find_root_title(tree: dict, target_node: dict) -> str:
    for ch in tree.get('children', []):
        if ch is target_node: return ch.get('title', '')
        if _contains_node(ch, target_node): return ch.get('title', '')
    return ''

def _contains_node(parent: dict, target: dict) -> bool:
    if parent is target: return True
    for c in parent.get('children', []):
        if _contains_node(c, target): return True
    return False

def cleanup_tree_structure(tree: dict) -> dict:
    """Post-enhancement cleanup: remove empty leaves, dedup, sort, trim noise."""
    tree = _remove_empty_leaf_nodes(tree)
    tree = _deduplicate_sibling_titles(tree)
    tree = _normalize_children_order(tree)
    tree = _trim_noise_nodes(tree)
    return tree

def _remove_empty_leaf_nodes(node: dict) -> dict:
    children = node.get("children", [])
    if not children: return node
    filtered = []
    for child in children:
        child = _remove_empty_leaf_nodes(child)
        g_children = child.get("children", [])
        has_content = bool((child.get("content") or "").strip())
        has_summary = bool(child.get("summary", ""))
        title = (child.get("title") or "").strip()
        if not title and not has_content and not has_summary and not g_children:
            continue
        filtered.append(child)
    node["children"] = filtered
    return node

def _deduplicate_sibling_titles(node: dict) -> dict:
    children = node.get("children", [])
    if not children: return node
    seen = set(); deduped = []
    for child in children:
        child = _deduplicate_sibling_titles(child)
        title = (child.get("title") or "").strip()
        if title and title in seen:
            if not child.get("children") and not (child.get("content") or "").strip(): continue
        if title: seen.add(title)
        deduped.append(child)
    node["children"] = deduped
    return node

def _normalize_children_order(node: dict) -> dict:
    children = node.get("children", [])
    if not children: return node
    for child in children: _normalize_children_order(child)
    def sort_key(c):
        title = c.get("title", "")
        m_chapter = re.match(r'^第[一二三四五六七八九十百\d]+章', title)
        m_section = re.match(r'^第[一二三四五六七八九十百\d]+节', title)
        m_kp = re.match(r'^知识点[一二三四五六七八九十\d]+', title)
        m_num = re.match(r'^[（(]?(\d+)[）).、]', title)
        if m_chapter: return (0, _cn_num_to_int(m_chapter.group(0)), "")
        if m_section: return (1, _cn_num_to_int(m_section.group(0)), "")
        if m_kp: return (2, _cn_num_to_int(m_kp.group(0)), "")
        if m_num: return (3, int(m_num.group(1)), title)
        return (4, 0, title)
    node["children"].sort(key=sort_key)
    return node

def _cn_num_to_int(text: str) -> int:
    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    nums = re.findall(r'[一二三四五六七八九十\d]+', text)
    if not nums: return 9999
    s = nums[0]
    if s.isdigit(): return int(s)
    if s in cn_map: return cn_map[s]
    if s == "十": return 10
    if s.startswith("十"): return 10 + cn_map.get(s[1], 0)
    return cn_map.get(s[0], 0) * 10 + cn_map.get(s[1:], 0) if len(s) > 1 else cn_map.get(s, 9999)

def _trim_noise_nodes(node: dict) -> dict:
    NOISE_TITLES = {"前言", "序言", "导读", "后记", "参考文献", "附录", "目录", "版权页", "封面", "封底", "致谢", "出版信息"}
    children = node.get("children", [])
    if not children: return node
    filtered = []
    for child in children:
        title = (child.get("title") or "").strip()
        if title in NOISE_TITLES and not child.get("children"): continue
        child = _trim_noise_nodes(child)
        filtered.append(child)
    node["children"] = filtered
    return node
```

---

## 8. mineru_adapter/client.py

MinerU云端OCR客户端：批量上传PDF → 云端OCR识别 → 下载Markdown。

```python
import os
import io
import time
import zipfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://mineru.net"

def detect_language(filename: str, sample_text: str | None = None) -> str:
    """Detect document language. Returns 'ch' for Chinese, 'en' for English."""
    source = (sample_text or '') + ' ' + filename
    for ch in source:
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿': return "ch"
    return "en"

def _build_headers(token: str) -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

def upload_and_process_all(pdf_paths: list, is_ocr: bool = True, token: str | None = None) -> list:
    """Batch upload PDFs and process in parallel."""
    if token is None: token = os.environ.get("MINERU_TOKEN", "")
    if not token: raise ValueError("MinerU token is required.")
    headers = _build_headers(token)
    files_meta = [
        {"name": os.path.basename(p), "data_id": f"part_{i:03d}", "is_ocr": is_ocr,
         "enable_table": True, "enable_formula": True, "language": detect_language(os.path.basename(p))}
        for i, p in enumerate(pdf_paths)
    ]
    res = requests.post(f"{BASE_URL}/api/v4/file-urls/batch", headers=headers,
        json={"files": files_meta, "model_version": "vlm"})
    result = res.json()
    assert result["code"] == 0, f"Failed to get upload URLs: {result['msg']}"
    batch_id = result["data"]["batch_id"]
    upload_urls = result["data"]["file_urls"]
    def upload_one(args):
        i, url, path = args
        with open(path, "rb") as f: r = requests.put(url, data=f)
        return r.status_code == 200
    with ThreadPoolExecutor(max_workers=5) as executor:
        tasks = [(i, url, path) for i, (url, path) in enumerate(zip(upload_urls, pdf_paths))]
        results = list(executor.map(upload_one, tasks))
    if not all(results): raise Exception("Some files failed to upload")
    return poll_batch(batch_id, token=token)

def poll_batch(batch_id: str, interval: int = 5, timeout: int = 900, token: str | None = None) -> list:
    """Poll batch until all done or failed."""
    if token is None: token = os.environ.get("MINERU_TOKEN", "")
    if not token: raise ValueError("MinerU token is required.")
    headers = _build_headers(token)
    url = f"{BASE_URL}/api/v4/extract-results/batch/{batch_id}"
    start = time.time()
    while time.time() - start < timeout:
        res = requests.get(url, headers=headers)
        data = res.json()["data"]
        files = data["extract_result"]
        done = [f for f in files if f["state"] == "done"]
        running = [f for f in files if f["state"] in ("running", "pending", "waiting-file", "converting")]
        failed = [f for f in files if f["state"] == "failed"]
        if len(done) + len(failed) == len(files):
            if failed:
                for f in failed: print(f"  FAILED: {f['file_name']} - {f.get('err_msg', 'unknown')}")
            return files
        time.sleep(interval)
    raise TimeoutError(f"Timeout after {timeout}s")

def download_markdowns(extract_result: list, output_dir: str) -> list:
    """Download zip files and extract markdown."""
    os.makedirs(output_dir, exist_ok=True)
    md_files = []
    for item in extract_result:
        if item["state"] != "done": continue
        zip_url = item.get("full_zip_url")
        if not zip_url: continue
        res = requests.get(zip_url)
        if res.status_code != 200: continue
        z = zipfile.ZipFile(io.BytesIO(res.content))
        for name in z.namelist():
            if name.endswith(".md"):
                md_content = z.read(name).decode("utf-8")
                md_name = os.path.splitext(item["file_name"])[0] + ".md"
                md_path = os.path.join(output_dir, md_name)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                md_files.append(md_path)
    return md_files
```

---

## 9. server.py

流水线编排核心函数（摘录）。上传 → 拆分 → 解析 → 结构化 → 建树 → 增强 → 输出。

```python
# ── 文件类型识别 ──
def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == '.pdf': return 'pdf'
    if ext in ('.docx', '.doc'): return 'word'
    if ext in ('.txt', '.md', '.markdown'): return 'text'
    if ext in ('.pptx', '.ppt'): return 'presentation'
    if ext in ('.xlsx', '.xls'): return 'spreadsheet'
    if ext in ('.html', '.htm'): return 'html'
    if ext == '.csv': return 'csv'
    if ext == '.epub': return 'epub'
    return 'unknown'

# ── 主流水线 ──
def run_pipeline(task: TaskProgress, uploaded_files: list[dict]):
    """Run the full pipeline in a background thread."""
    work_dir = os.path.join(TEMP_DIR, task.task_id)
    os.makedirs(work_dir, exist_ok=True)
    mineru_token = task.mineru_token
    deepseek_api_key = task.deepseek_api_key

    try:
        # Separate files by type
        pdf_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'pdf']
        non_pdf_files = [f for f in uploaded_files if detect_file_type(f['original_name']) != 'pdf']

        # Stage 1: Prepare processing units
        all_units = []; md_files = []
        if pdf_files:
            pdf_units = _prepare_pdf_processing_units(pdf_files, work_dir, task)
            all_units.extend(pdf_units)
        if non_pdf_files:
            text_units = _prepare_text_units(non_pdf_files, work_dir, task)
            all_units.extend(text_units)
            md_files.extend([u["path"] for u in text_units])

        # Stage 2: MinerU OCR → Markdown (PDF only)
        pdf_units = [u for u in all_units if u['split_mode'] in ('whole_pdf', 'chapter')]
        if pdf_units:
            if not mineru_token:
                # Fallback: MarkItDown
                fallback_md = _fallback_pdf_to_md(pdf_units, md_dir)
                md_files.extend(fallback_md)
            else:
                # MinerU OCR
                results = upload_and_process_all([unit["path"] for unit in pdf_units], is_ocr=True, token=mineru_token)
                ocr_md_files = download_markdowns(results, md_dir)
                md_files.extend(ocr_md_files)

        # Stage 2.5: LLM structuring for unstructured text
        unstructured_units = [u for u in all_units if u.get('split_mode') in ('text',) and not u.get('has_structure')]
        if unstructured_units and deepseek_api_key:
            llm_trees = asyncio.run(_llm_structure_chunks(unstructured_units, task, deepseek_api_key))

        # Stage 3: Build trees
        unit_meta_by_stem = {Path(unit["path"]).stem: unit for unit in all_units}
        tree_files = _build_trees_from_md(md_files, work_dir, unit_meta_by_stem, api_key=deepseek_api_key)

        # Stage 4: Fix hierarchy & merge
        merged_tree = _fix_hierarchy_and_merge(tree_files, work_dir)

        # Stage 5: AI Enhancement
        if deepseek_api_key:
            enhanced_tree = asyncio.run(_enhance_tree(merged_tree, task, deepseek_api_key))
        else:
            from node_enhancer import cleanup_tree_structure
            enhanced_tree = cleanup_tree_structure(merged_tree)

        # Stage 6: Final JSON
        final_result = _prepare_final_json(enhanced_tree)
        # Save to disk...

    except Exception as e:
        task.set_error(f"{str(e)}\n{traceback.format_exc()}")

# ── PDF处理单元准备 ──
def _prepare_pdf_processing_units(uploaded_pdfs, work_dir, task):
    """PDFs < 200MB → whole; larger → split by chapter."""
    # ... (see full code in server.py)

# ── 非PDF文件转Markdown ──
def _prepare_text_units(uploaded_files, work_dir, task):
    """Convert non-PDF files via MarkItDown, check structure, split if large."""
    SUPPORTED_TYPES = ('word', 'text', 'presentation', 'spreadsheet', 'html', 'csv', 'epub')
    for upload in uploaded_files:
        md_text = convert_to_markdown(file_path)
        has_structure = has_heading_structure(md_text)
        chunks = split_large_text(md_text)
        # Write chunks to .md files, return units with metadata

# ── PDF回退提取 ──
def _fallback_pdf_to_md(pdf_units, output_dir):
    """Extract text from PDFs using MarkItDown when MinerU unavailable."""
    for unit in pdf_units:
        md_text = convert_to_markdown(pdf_path)
        # Write to .md file

# ── LLM结构化 ──
async def _llm_structure_chunks(units, task, api_key):
    """Use LLM to create tree structure from unstructured text."""
    # Delegates to llm_structurer.structure_all_chunks()

# ── Markdown建树 ──
def _build_trees_from_md(md_files, work_dir, unit_meta_by_stem, api_key):
    """parse_md_to_nodes → adjust_standalone_levels → apply_hierarchy_repair → build_tree"""
    from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree
    from hierarchy_repair import apply_hierarchy_repair
    for md_path in md_files:
        nodes = parse_md_to_nodes(md_text)
        nodes = adjust_standalone_levels(nodes)
        nodes = apply_hierarchy_repair(nodes)
        tree_children = build_tree(nodes)
        # Optional: validate_and_repair_hierarchy() with LLM

# ── 层级修复与合并 ──
def _fix_hierarchy_and_merge(tree_files, work_dir):
    """Merge trees by source PDF order, add cross-PDF context bridges."""
    # Group by source_order → nest by hierarchy → shift levels → create bridge nodes

# ── AI增强 ──
async def _enhance_tree(tree, task, api_key):
    """Enhance nodes: detect profile → collect postorder → enhance each → cleanup."""
    from node_enhancer import build_prompt, get_context_text, should_enhance, collect_postorder, detect_document_profile, cleanup_tree_structure
    document_profile = detect_document_profile(tree)
    to_enhance = []
    for ch in tree.get("children", []): collect_postorder(ch, to_enhance)
    # Filter levels 1-3, cap at 30 nodes, sequential enhance with fail limit

# ── 最终JSON ──
def _prepare_final_json(tree):
    """Add depth/level defaults, run cleanup_tree_structure()."""
    from node_enhancer import cleanup_tree_structure
    tree = cleanup_tree_structure(tree)
    # Walk tree, add depth/level/summary/keywords/exam_points/mermaid/tables defaults
```

---

## 流程总览

```
用户上传文件 (PDF/DOCX/TXT/PPTX/XLSX/HTML/CSV/EPUB)
    │
    ├─ PDF ──→ _prepare_pdf_processing_units()
    │     ├─ < 200MB → 整本处理
    │     └─ ≥ 200MB → split_pdf() (TOC→内容检测→固定分块)
    │
    ├─ 非PDF ──→ _prepare_text_units()
    │     ├─ convert_to_markdown() [MarkItDown]
    │     ├─ has_heading_structure() 检测
    │     └─ split_large_text() 分块
    │
    ▼
Stage 2: OCR/文本提取
    ├─ 有MinerU Token → upload_and_process_all() → download_markdowns()
    └─ 无Token → _fallback_pdf_to_md() → convert_to_markdown()
    │
    ▼
Stage 2.5: LLM结构化 (无标题结构的文本)
    └─ structure_all_chunks() [跨片段上下文桥接]
    │
    ▼
Stage 3: 建树
    ├─ parse_md_to_nodes()       # Markdown → 扁平节点
    ├─ adjust_standalone_levels() # 修复嵌套
    ├─ apply_hierarchy_repair()   # 语义栈修复
    └─ build_tree()              # 扁平节点 → 树JSON
    │
    ▼
Stage 4: 层级修复与合并
    ├─ _nest_nodes_by_hierarchy()
    ├─ _shift_tree_levels()
    └─ _build_pdf_context_bridge()
    │
    ▼
Stage 5: AI增强
    ├─ detect_document_profile()  # 考纲/教材/书籍/通用
    ├─ collect_postorder()        # 后序遍历(叶子优先)
    ├─ build_prompt() + enhance_one() # DeepSeek API
    └─ cleanup_tree_structure()   # 去重/排序/去噪
    │
    ▼
Stage 6: 最终JSON输出
```
