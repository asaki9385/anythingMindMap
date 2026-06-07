"""Unified document-to-Markdown adapter using MarkItDown.

Replaces text_extractor.py for DOCX/TXT and PyMuPDF fallback for PDF.
Supports 20+ formats: PDF, DOCX, TXT, PPTX, XLSX, HTML, CSV, EPUB, etc.
"""
import os
import sys

# Ensure markitdown package is importable from knowledge-compiler/
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
