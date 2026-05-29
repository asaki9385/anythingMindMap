"""
Extract text from Word (.docx) and plain text (.txt) files into markdown format.
"""

import os
import re
from pathlib import Path


# ────────────────────────────────────────────
# Word (.docx) → Markdown
# ────────────────────────────────────────────

def docx_to_markdown(docx_path: str) -> str:
    """Convert a .docx file to markdown, preserving heading styles, tables, and formatting."""
    from docx import Document
    from docx.table import Table as DocxTable

    doc = Document(docx_path)
    md_parts = []

    # Walk through document elements in order (paragraphs + tables)
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

    # Detect heading level from style
    heading_level = 0
    if style_name.startswith('Heading'):
        try:
            heading_level = int(style_name.split()[-1])
        except (ValueError, IndexError):
            heading_level = 0

    # Also detect Chinese heading patterns in the text
    text = para.text.strip()
    if not text:
        return ''

    if heading_level == 0:
        heading_level = _detect_heading_level(text)

    # Build markdown with inline formatting
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
    # Chinese heading patterns
    if re.match(r'^第[一二三四五六七八九十百千\d]+[章篇编]', text):
        return 1
    if re.match(r'^第[一二三四五六七八九十\d]+节', text):
        return 2
    if re.match(r'^知识点[一二三四五六七八九十\d]+', text):
        return 3
    if re.match(r'^[一二三四五六七八九十]+[、.]', text) and len(text) < 30:
        return 3

    # English academic section names
    if re.match(
        r'^(Abstract|Introduction|Related\s+Work|Methodology|Conclusion|References|Acknowledgments)\b',
        text, re.IGNORECASE,
    ):
        return 1
    # Numbered sections: 1.1.1 → L3, 1.1 → L2, 1. → L1
    if re.match(r'^\d+\.\d+\.\d+\s+\S', text):
        return 3
    if re.match(r'^\d+\.\d+\s+\S', text):
        return 2
    if re.match(r'^\d+\.\s+\S', text):
        return 1
    # All-caps short phrase (not purely numeric)
    if len(text) < 60 and text.isupper():
        return 2

    # Generic numbered catch-all (exclude multi-level numbering 1.1 / 1.1.1 etc.)
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

    # Normalize column count
    max_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < max_cols:
            r.append('')

    lines = []
    # Header row
    lines.append('| ' + ' | '.join(rows[0]) + ' |')
    lines.append('|' + '|'.join(['------'] * max_cols) + '|')
    # Data rows
    for row in rows[1:]:
        lines.append('| ' + ' | '.join(row) + ' |')

    return '\n'.join(lines)


# ────────────────────────────────────────────
# Plain text (.txt) → Markdown
# ────────────────────────────────────────────

def txt_to_markdown(txt_path: str) -> str:
    """Convert a .txt file to markdown, detecting heading patterns."""
    raw = _read_text_file(txt_path)
    lines = raw.split('\n')
    md_parts = []
    current_para = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Blank line: flush current paragraph
            if current_para:
                md_parts.append(' '.join(current_para))
                current_para = []
            continue

        heading_level = _detect_heading_level(stripped)
        if heading_level > 0 and len(stripped) < 120:
            # Flush paragraph before heading
            if current_para:
                md_parts.append(' '.join(current_para))
                current_para = []
            md_parts.append(f"{'#' * heading_level} {stripped}")
        else:
            current_para.append(stripped)

    # Flush remaining paragraph
    if current_para:
        md_parts.append(' '.join(current_para))

    return '\n\n'.join(md_parts)


def _read_text_file(file_path: str) -> str:
    """Read text file with encoding detection."""
    # Try utf-8 first
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        pass

    # Try chardet detection
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        encoding = detected.get('encoding', 'utf-8') or 'utf-8'
        return raw.decode(encoding, errors='replace')
    except Exception:
        pass

    # Fallback: gbk (common for Chinese text files on Windows)
    try:
        with open(file_path, 'r', encoding='gbk') as f:
            return f.read()
    except UnicodeDecodeError:
        pass

    # Last resort: latin-1 (never fails)
    with open(file_path, 'r', encoding='latin-1') as f:
        return f.read()


# ────────────────────────────────────────────
# Text splitting for large documents
# ────────────────────────────────────────────

def has_heading_structure(md_text: str) -> bool:
    """Check if markdown text has meaningful heading structure."""
    heading_count = 0
    for line in md_text.split('\n'):
        if re.match(r'^#{1,3}\s+\S', line.strip()):
            heading_count += 1
    return heading_count >= 2


def split_large_text(md_text: str, max_chars: int = 8000) -> list[dict]:
    """Split large markdown text into chunks for LLM processing.

    Strategy:
    - If headers exist: split by top-level headers (level 1 or 2)
    - Otherwise: split by paragraph groups respecting max_chars
    """
    if len(md_text) <= max_chars:
        return [{"text": md_text, "index": 0, "total": 1, "is_first": True, "is_last": True}]

    # Try splitting by top-level headers
    chunks = _split_by_headers(md_text, max_chars)
    if len(chunks) > 1:
        return chunks

    # Fallback: split by paragraphs
    return _split_by_paragraphs(md_text, max_chars)


def _split_by_headers(md_text: str, max_chars: int) -> list[dict]:
    """Split markdown by top-level headers."""
    # Find level-1 and level-2 headers
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

    # If any chunk is still too large, sub-split by paragraphs
    result = []
    for chunk_text in chunks:
        if len(chunk_text) <= max_chars:
            result.append(chunk_text)
        else:
            result.extend([c["text"] for c in _split_by_paragraphs(chunk_text, max_chars)])

    # Assign metadata
    total = len(result)
    return [
        {
            "text": text,
            "index": i,
            "total": total,
            "is_first": i == 0,
            "is_last": i == total - 1,
        }
        for i, text in enumerate(result)
    ]


def _split_by_paragraphs(md_text: str, max_chars: int) -> list[dict]:
    """Split text at semantic boundaries: headings first, then triple blank lines.
    Never cuts mid-paragraph unless a single paragraph exceeds max_chars (fallback).
    """
    # Collect cut candidates: document start, heading positions, triple+ newlines
    cut_candidates = [0]
    for m in re.finditer(r'^#{1,3}\s+', md_text, re.MULTILINE):
        cut_candidates.append(m.start())
    for m in re.finditer(r'\n\n\n+', md_text):
        cut_candidates.append(m.start())
    cut_candidates = sorted(set(cut_candidates))
    cut_candidates.append(len(md_text))

    # Build adjacent segments from cut point to cut point
    segments = [
        (cut_candidates[i], cut_candidates[i + 1])
        for i in range(len(cut_candidates) - 1)
    ]

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

    # Fallback: any chunk still exceeding max_chars gets split at paragraph boundaries
    result = []
    for chunk_text in chunks:
        if len(chunk_text) <= max_chars:
            result.append(chunk_text)
        else:
            result.extend(_split_oversized_chunk(chunk_text, max_chars))

    total = len(result)
    return [
        {
            "text": text,
            "index": i,
            "total": total,
            "is_first": i == 0,
            "is_last": i == total - 1,
        }
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
        # Account for '\n\n' separators between paragraphs
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

    # Absolute last resort: split by character if a paragraph alone exceeds max_chars
    result = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            for i in range(0, len(chunk), max_chars):
                result.append(chunk[i:i + max_chars].strip())
    return result
