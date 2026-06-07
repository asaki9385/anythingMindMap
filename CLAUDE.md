# CLAUDE.md

## Project Overview

Knowledge Tree — a document-to-knowledge-tree pipeline. Upload PDF/Word/TXT, parse with MarkItDown (default) or MinerU OCR (when API key provided) + LLM, generate structured knowledge trees, render as interactive mind maps. Oriented toward Chinese education (333 exam prep).

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # add DeepSeek API key (required for AI features), MinerU key (optional)
python start.py         # starts server at http://localhost:8000
```

Windows: `start.bat`. macOS: `start.command`.

**Parsing engine:** MarkItDown is the default (local, no API key needed). MinerU cloud OCR is optional — set `MINERU_API_KEY` in `.env` or enter it on the upload page for enhanced PDF parsing.

## Architecture

```
knowledge-compiler/
  server.py            — FastAPI app, all API routes, pipeline orchestration (53KB, main entry)
  tree_builder.py      — Markdown → tree JSON (parallel per-file)
  node_enhancer.py     — AI enhancement: summaries, keywords, exam points, Mermaid
  hierarchy_repair.py  — Multi-numbering-system hierarchy repair
  markitdown/          — Microsoft MarkItDown library (copied source, default parsing engine)
    __init__.py        — Public API: MarkItDown, DocumentConverterResult, etc.
    _markitdown.py     — Core orchestrator: converter registry, format detection
    converters/        — 20+ format converters (PDF, DOCX, PPTX, XLSX, HTML, etc.)
    converter_utils/   — Utilities (DOCX math OMML→LaTeX, etc.)
  parser/
    markitdown_adapter.py — Unified doc→Markdown adapter (wraps markitdown)
    pdf_splitter.py    — PDF split by TOC/content/pages
    text_extractor.py  — split_large_text(), has_heading_structure() (kept for chunking)
    llm_structurer.py  — LLM structuring for unstructured text
  mineru_adapter/
    client.py          — MinerU cloud OCR API client (premium PDF parsing)
  ui/                  — Static frontend (HTML/CSS/JS, no build step)
    homepage.html      — Landing page with GSAP ScrollTrigger
    upload_mindmap.html — Upload + mind map viewer
    browse.html        — File browser
    tree_mindmap.html  — Standalone mind map viewer
    theme.css          — "Warm Academic" design system
    common.js          — Shared JS utilities
```

## Tech Stack

- **Backend:** Python 3.10+, FastAPI, Uvicorn, httpx
- **Parsing:** MarkItDown (default, local), MinerU cloud OCR (premium, when API key provided)
- **AI:** DeepSeek API (OpenAI-compatible)
- **Frontend:** Vanilla HTML/CSS/JS, ECharts (mind maps), GSAP + ScrollTrigger (animations)
- **Design:** "Warm Academic" system — Noto Serif SC + Noto Sans SC + JetBrains Mono, light/dark themes

## Key Conventions

- Frontend is plain HTML/CSS/JS — no framework, no build step, no npm. Files are served by FastAPI `StaticFiles` at `/ui`.
- CSS uses custom properties defined in `theme.css`. All new UI must use these variables for colors, spacing, typography.
- Dark mode via `[data-theme="dark"]` selector on root element. Use CSS variables, never hardcode colors.
- GSAP animations: use `gsap.to()` for scroll-triggered reveals (elements start at `opacity: 0` in CSS). Register `ScrollTrigger` plugin before use.
- API endpoints are all in `server.py`. The pipeline: upload → split → MarkItDown/MinerU → markdown → tree build → AI enhance → JSON output.
- Processing data lives in `knowledge-compiler/data/_temp_uploads/` (gitignored).
- Python dependencies: `requirements.txt`. No pyproject.toml or poetry.
- Environment config via `.env` file (see `.env.example` for all options).

## Graceful Degradation

- **No MinerU key:** MarkItDown handles all document parsing locally (no network needed). Supports PDF, DOCX, TXT, PPTX, XLSX, HTML, CSV, EPUB, etc.
- **No DeepSeek key:** MarkItDown still converts documents; LLM structuring and AI enhancement are skipped.
- MinerU key can be set per-request (upload form) or globally via `.env` `MINERU_API_KEY`.

## Running Tests

Standalone test scripts (no pytest config):

```bash
python test_document_processing.py
python test_advanced_processing.py
python knowledge-compiler/test_all_numbering.py
```

## Important Notes

- `server.py` is the single backend entry point — all routes and pipeline logic live there.
- `markitdown/` is a copy of Microsoft's MarkItDown source (not a pip install). Update by copying from `markitdown-main/packages/markitdown/src/markitdown/`.
- `builder/`, `core/`, `splitter/` directories are empty placeholders (unused).
- The project uses SSE (Server-Sent Events) for real-time upload/processing progress.
- Google Fonts are loaded via CDN — requires internet access.
