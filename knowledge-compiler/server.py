"""
Knowledge Tree - PDF Upload & Mind Map Server
Orchestrates: PDF split → MinerU OCR → Tree build → AI enhance → Mind map JSON
"""

import json
import io
import zipfile
import os
import sys
import uuid
import shutil
import asyncio
import threading
import traceback
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from parser.pdf_splitter import split_pdf as split_pdf_fn, sanitize_filename
from parser.text_extractor import docx_to_markdown, txt_to_markdown, split_large_text, has_heading_structure
from parser.llm_structurer import structure_all_chunks
from mineru_adapter.client import upload_and_process_all, download_markdowns

import re
import httpx
from collections import defaultdict

# ────────────────────────────────────────────
# Config
# ────────────────────────────────────────────

TEMP_DIR = os.path.join(BASE_DIR, "data", "_temp_uploads")
os.makedirs(TEMP_DIR, exist_ok=True)

# AI Enhancement config
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
SUBJECT_CONFIG = {"name": "教育学", "exam": "333教育综合考研"}
ENHANCE_LEVELS = {1: True, 2: True, 3: True, 4: True, 5: False}
MAX_CONCURRENT = 5
REQUEST_DELAY = 0.3
NO_SPLIT_SIZE_MB = 200

# Task storage (in-memory)
tasks: dict = {}
tasks_lock = threading.Lock()

# ────────────────────────────────────────────
# FastAPI App
# ────────────────────────────────────────────

app = FastAPI(title="Knowledge Tree Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for UI
UI_DIR = os.path.join(BASE_DIR, "ui")
if os.path.isdir(UI_DIR):
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/homepage.html")


# ────────────────────────────────────────────
# Task management
# ────────────────────────────────────────────

class TaskProgress:
    def __init__(self, task_id: str, filename: str,
                 mineru_token: str | None = None,
                 deepseek_api_key: str | None = None):
        self.task_id = task_id
        self.filename = filename
        self.stage = "uploading"
        self.progress = 0
        self.total_stages = 5
        self.stage_names = ["splitting", "ocr_converting", "building_tree", "ai_enhancing", "merging"]
        self.status = "processing"
        self.result = None
        self.error = None
        self.messages = []
        self.mineru_token = mineru_token
        self.deepseek_api_key = deepseek_api_key
        self._event = threading.Event()

    def set_stage(self, stage: str, progress: float = None, message: str = ""):
        self.stage = stage
        if progress is not None:
            self.progress = progress
        if message:
            self.messages.append(message)
            print(f"[{self.task_id}] {message}")

    def set_done(self, result: dict):
        self.status = "done"
        self.progress = 100
        self.result = result
        self.stage = "complete"
        self._event.set()

    def set_error(self, error: str):
        self.status = "error"
        self.error = error
        self.messages.append(f"ERROR: {error}")
        self._event.set()
        print(f"[{self.task_id}] ERROR: {error}")

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "stage": self.stage,
            "progress": self.progress,
            "status": self.status,
            "messages": self.messages[-20:],
            "error": self.error,
        }


# ────────────────────────────────────────────
# Pipeline functions
# ────────────────────────────────────────────

def run_pipeline(task: TaskProgress, uploaded_files: list[dict]):
    """Run the full pipeline in a background thread.
    Supports mixed file types: PDF, Word (.docx), and plain text (.txt).
    """
    work_dir = os.path.join(TEMP_DIR, task.task_id)
    os.makedirs(work_dir, exist_ok=True)

    mineru_token = task.mineru_token
    deepseek_api_key = task.deepseek_api_key

    try:
        # ── Separate files by type ──
        pdf_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'pdf']
        word_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'word']
        txt_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'text']
        text_files = word_files + txt_files

        total = len(uploaded_files)
        task.set_stage("splitting", 5, f"正在准备 {total} 个文档 ({len(pdf_files)} PDF, {len(word_files)} Word, {len(txt_files)} TXT)...")

        # ── Stage 1: Prepare processing units ──
        all_units = []
        md_files = []

        # PDF units
        if pdf_files:
            pdf_units = _prepare_pdf_processing_units(pdf_files, work_dir, task)
            all_units.extend(pdf_units)

        # Word/TXT units (already extracted to markdown)
        if text_files:
            text_units = _prepare_text_units(text_files, work_dir, task)
            all_units.extend(text_units)
            md_files.extend([u["path"] for u in text_units])

        task.set_stage("splitting", 15, f"已生成 {len(all_units)} 个处理单元")

        if not all_units:
            task.set_error("预处理失败：未生成可识别文件")
            return

        # ── Stage 2: MinerU OCR → Markdown (PDF only) ──
        pdf_units = [u for u in all_units if u['split_mode'] in ('whole_pdf', 'chapter')]

        if pdf_units:
            if not mineru_token:
                task.set_stage("ocr_converting", 20, "未提供MinerU Token，使用PyMuPDF提取PDF文本")
                md_dir = os.path.join(work_dir, "markdown")
                os.makedirs(md_dir, exist_ok=True)
                fallback_md = _fallback_pdf_to_md(pdf_units, md_dir)
                md_files.extend(fallback_md)
                task.set_stage("ocr_converting", 40, f"PyMuPDF提取完成，{len(fallback_md)} 个Markdown文件")
            else:
                task.set_stage("ocr_converting", 20, f"正在识别 {len(pdf_units)} 个PDF...")
                try:
                    results = upload_and_process_all([unit["path"] for unit in pdf_units], is_ocr=True, token=mineru_token)
                    md_dir = os.path.join(work_dir, "markdown")
                    ocr_md_files = download_markdowns(results, md_dir)
                    md_files.extend(ocr_md_files)
                    task.set_stage("ocr_converting", 40, f"MinerU识别完成，{len(ocr_md_files)} 个Markdown文件")
                except Exception as e:
                    task.set_stage("ocr_converting", 40, f"MinerU不可用 ({e})，回退到PyMuPDF")
                    md_dir = os.path.join(work_dir, "markdown")
                    os.makedirs(md_dir, exist_ok=True)
                    fallback_md = _fallback_pdf_to_md(pdf_units, md_dir)
                    md_files.extend(fallback_md)
                    task.messages.append(f"回退模式：使用PDF直接提取文本，{len(fallback_md)} 个文件")
        else:
            task.set_stage("ocr_converting", 40, "无PDF文件，跳过OCR阶段")

        if not md_files:
            task.set_error("文本提取失败")
            return

        # ── Stage 2.5: LLM structuring for unstructured text ──
        unstructured_units = [u for u in all_units if u.get('split_mode') in ('text',) and not u.get('has_structure')]
        if unstructured_units:
            if not deepseek_api_key:
                task.messages.append("未提供DeepSeek API Key，跳过AI结构化，使用基础标题解析")
                llm_trees = []
            else:
                task.set_stage("building_tree", 42, f"正在用AI分析 {len(unstructured_units)} 个无结构文本文档...")
                try:
                    llm_trees = asyncio.run(_llm_structure_chunks(unstructured_units, task, deepseek_api_key))
                except Exception as e:
                    task.messages.append(f"WARNING: LLM结构化失败 ({e})，使用基础解析")
                    llm_trees = []
        else:
            llm_trees = []

        # ── Stage 3: Build trees ──
        task.set_stage("building_tree", 45, "正在构建知识树...")
        # All units (PDF + structured text) for metadata
        unit_meta_by_stem = {Path(unit["path"]).stem: unit for unit in all_units}
        tree_files = _build_trees_from_md(md_files, work_dir, unit_meta_by_stem, api_key=deepseek_api_key)

        # Add LLM-structured trees (from unstructured text)
        for i, tree in enumerate(llm_trees):
            if tree:
                tree_files.append({
                    "filename": f"llm_structured_{i}",
                    "tree": tree,
                    "source_order": unstructured_units[i]["source_order"],
                    "source_title": unstructured_units[i]["source_title"],
                    "unit_order": unstructured_units[i]["unit_order"],
                })

        task.set_stage("building_tree", 60, f"知识树构建完成，{len(tree_files)} 棵树")

        # ── Stage 4: Fix hierarchy ──
        task.set_stage("building_tree", 65, "正在修复层级结构...")
        merged_tree = _fix_hierarchy_and_merge(tree_files, work_dir)
        task.set_stage("building_tree", 75, "层级结构修复完成")

        # ── Stage 5: AI Enhancement ──
        if not deepseek_api_key:
            task.set_stage("ai_enhancing", 95, "未提供DeepSeek API Key，跳过AI增强")
            from node_enhancer import cleanup_tree_structure
            merged_tree = cleanup_tree_structure(merged_tree)
            enhanced_tree = merged_tree
        else:
            task.set_stage("ai_enhancing", 80, "正在AI增强节点(添加摘要/关键词/考点)...")
            try:
                enhanced_tree = asyncio.run(_enhance_tree(merged_tree, task, deepseek_api_key))
                task.set_stage("ai_enhancing", 95, "AI增强完成")
            except Exception as e:
                task.set_stage("ai_enhancing", 95, f"AI增强跳过 ({e})，使用未经增强的树")
                from node_enhancer import cleanup_tree_structure
                merged_tree = cleanup_tree_structure(merged_tree)
                enhanced_tree = merged_tree

        # ── Stage 6: Done ──
        task.set_stage("merging", 98, "正在准备最终数据...")
        final_result = _prepare_final_json(enhanced_tree)
        export_json_dir = os.path.join(work_dir, "json")
        os.makedirs(export_json_dir, exist_ok=True)
        with open(os.path.join(export_json_dir, "knowledge_tree.json"), "w", encoding="utf-8") as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)
        task.set_stage("complete", 100, "处理完成！")
        task.set_done(final_result)

    except Exception as e:
        task.set_error(f"{str(e)}\n{traceback.format_exc()}")


def _prepare_pdf_processing_units(uploaded_pdfs: list[dict], work_dir: str, task: TaskProgress) -> list[dict]:
    """Build ordered processing units from uploaded PDFs.
    PDFs smaller than threshold are processed as a whole; larger PDFs are split."""
    chapters_dir = os.path.join(work_dir, "chapters")
    os.makedirs(chapters_dir, exist_ok=True)
    units = []

    for pdf_index, upload in enumerate(uploaded_pdfs, start=1):
        pdf_path = upload["path"]
        source_title = Path(upload["original_name"]).stem
        source_prefix = f"{pdf_index:02d}"
        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)

        if size_mb < NO_SPLIT_SIZE_MB:
            filename = f"{source_prefix}_000_{sanitize_filename(source_title)}.pdf"
            output_path = os.path.join(chapters_dir, filename)
            shutil.copy2(pdf_path, output_path)
            units.append({
                "path": output_path,
                "source_order": pdf_index,
                "source_title": source_title,
                "unit_order": 0,
                "unit_title": source_title,
                "split_mode": "whole_pdf",
            })
            task.messages.append(f"{source_title}: {size_mb:.1f}MB，小于{NO_SPLIT_SIZE_MB}MB，整本直接处理")
            continue

        split_output_dir = os.path.join(chapters_dir, f"{source_prefix}_{sanitize_filename(source_title)}")
        split_files = split_pdf_fn(pdf_path, split_output_dir)
        for unit_order, split_path in enumerate(split_files, start=1):
            chapter_title = Path(split_path).stem
            filename = f"{source_prefix}_{unit_order:03d}_{sanitize_filename(chapter_title)}.pdf"
            output_path = os.path.join(chapters_dir, filename)
            # Resolve paths to handle Unicode normalization on Windows
            split_resolved = str(Path(split_path).resolve())
            output_resolved = str(Path(output_path).resolve())
            # Verify source exists before copy
            if not os.path.isfile(split_resolved):
                print(f"WARNING: split file not found: {split_resolved}")
                # Try listing directory to find actual file
                import glob
                parent = str(Path(split_path).parent)
                candidates = glob.glob(os.path.join(parent, "*.pdf"))
                if candidates:
                    # Match by order (unit_order-1 since enumerate starts at 1)
                    idx = unit_order - 1
                    if idx < len(candidates):
                        split_resolved = candidates[idx]
                        print(f"  Fallback: using {os.path.basename(split_resolved)}")
                    else:
                        print(f"  ERROR: no candidate at index {idx}")
                        continue
                else:
                    print(f"  ERROR: no PDF files in {parent}")
                    continue
            shutil.copy2(split_resolved, output_resolved)
            os.remove(split_resolved)
            units.append({
                "path": output_resolved,
                "source_order": pdf_index,
                "source_title": source_title,
                "unit_order": unit_order,
                "unit_title": chapter_title,
                "split_mode": "chapter",
            })
        if os.path.isdir(split_output_dir):
            shutil.rmtree(split_output_dir, ignore_errors=True)
        task.messages.append(f"{source_title}: {size_mb:.1f}MB，已拆分为 {len(split_files)} 个章节")

    return units


def _extract_page_text_clean(page) -> str:
    """Extract page text, filtering header/footer regions and reordering dual-column layouts."""
    page_height = page.rect.height
    page_width = page.rect.width

    header_threshold = page_height * 0.08
    footer_threshold = page_height * 0.92

    blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)

    in_content = []
    for block in blocks:
        x0, y0, x1, y1, text, *_ = block
        text = text.strip()
        if not text:
            continue
        if y1 < header_threshold or y0 > footer_threshold:
            continue
        if len(text) < 4 and text.isdigit():
            continue
        in_content.append(block)

    if not in_content:
        return ""

    if len(in_content) < 4:
        return "\n\n".join(b[4].strip() for b in in_content)

    # Dual-column detection on remaining blocks
    x_centers = sorted((b[0] + b[2]) / 2 for b in in_content)
    max_gap = 0
    split_idx = 0
    for i in range(len(x_centers) - 1):
        gap = x_centers[i + 1] - x_centers[i]
        if gap > max_gap:
            max_gap = gap
            split_idx = i

    if max_gap > page_width * 0.3 and split_idx >= 1 and split_idx < len(x_centers) - 2:
        page_mid_x = page_width / 2
        left = sorted([b for b in in_content if b[0] < page_mid_x], key=lambda b: b[1])
        right = sorted([b for b in in_content if b[0] >= page_mid_x], key=lambda b: b[1])
        left_text = "\n".join(b[4].strip() for b in left)
        right_text = "\n".join(b[4].strip() for b in right)
        return left_text + "\n" + right_text

    return "\n\n".join(b[4].strip() for b in in_content)


def _fallback_pdf_to_md(pdf_units: list[dict], output_dir: str) -> list:
    """Fallback: extract text from PDFs using PyMuPDF when MinerU is unavailable."""
    import fitz
    md_files = []
    for unit in pdf_units:
        pdf_path = unit["path"]
        doc = fitz.open(pdf_path)
        text_parts = []
        for page in doc:
            text = _extract_page_text_clean(page)
            if text.strip():
                text_parts.append(text)
        doc.close()

        basename = os.path.splitext(os.path.basename(pdf_path))[0]
        md_path = os.path.join(output_dir, f"{basename}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(text_parts))
        md_files.append(md_path)
    return md_files


def detect_file_type(filename: str) -> str:
    """Detect file type from extension."""
    ext = Path(filename).suffix.lower()
    if ext == '.pdf':
        return 'pdf'
    if ext == '.docx':
        return 'word'
    if ext == '.txt':
        return 'text'
    return 'unknown'


def _prepare_text_units(uploaded_files: list[dict], work_dir: str, task: TaskProgress) -> list[dict]:
    """Process Word (.docx) and plain text (.txt) files into markdown units."""
    md_dir = os.path.join(work_dir, "markdown")
    os.makedirs(md_dir, exist_ok=True)
    units = []

    for file_index, upload in enumerate(uploaded_files, start=1):
        file_path = upload["path"]
        original_name = upload["original_name"]
        source_title = Path(original_name).stem
        file_type = detect_file_type(original_name)
        source_prefix = f"{file_index:02d}"

        try:
            if file_type == 'word':
                md_text = docx_to_markdown(file_path)
                task.messages.append(f"[DOCX] {source_title}: 已提取Word文档内容")
            elif file_type == 'text':
                md_text = txt_to_markdown(file_path)
                task.messages.append(f"[TXT] {source_title}: 已提取文本内容")
            else:
                continue

            # Check if the text has good heading structure
            has_structure = has_heading_structure(md_text)

            # Split large text into chunks
            chunks = split_large_text(md_text)
            task.messages.append(f"{source_title}: {len(md_text)} 字, {len(chunks)} 个处理单元")

            for chunk_idx, chunk in enumerate(chunks):
                chunk_text = chunk["text"]
                filename = f"{source_prefix}_{chunk_idx:03d}_{sanitize_filename(source_title)}.md"
                md_path = os.path.join(md_dir, filename)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(chunk_text)

                units.append({
                    "path": md_path,
                    "source_order": file_index,
                    "source_title": source_title,
                    "unit_order": chunk_idx,
                    "unit_title": source_title if len(chunks) == 1 else f"{source_title}_part{chunk_idx + 1}",
                    "split_mode": file_type,  # 'word' or 'text'
                    "has_structure": has_structure,
                    "chunk_meta": chunk,
                })

        except Exception as e:
            task.messages.append(f"WARNING: {original_name} 处理失败: {e}")
            print(f"ERROR processing {original_name}: {traceback.format_exc()}")

    return units


async def _llm_structure_chunks(units: list[dict], task: TaskProgress, api_key: str) -> list[dict]:
    """Use LLM to create tree structure from unstructured text chunks."""
    from parser.text_extractor import split_large_text

    trees = []
    for unit in units:
        md_path = unit["path"]
        with open(md_path, encoding="utf-8") as f:
            text = f.read().strip()

        if not text:
            trees.append(None)
            continue

        # Split into chunks if needed
        chunks = unit.get("chunk_meta")
        if chunks is None:
            chunk_list = split_large_text(text)
        else:
            chunk_list = [chunks]

        # Process chunks sequentially with context bridging
        try:
            chunk_trees = await structure_all_chunks(chunk_list, api_key=api_key)
            if chunk_trees:
                # Merge multiple chunk trees into one
                if len(chunk_trees) == 1:
                    trees.append(chunk_trees[0])
                else:
                    merged = {
                        "title": unit.get("source_title", "文档"),
                        "children": [],
                    }
                    for ct in chunk_trees:
                        if ct and ct.get("children"):
                            merged["children"].extend(ct["children"])
                        elif ct:
                            merged["children"].append(ct)
                    trees.append(merged)
                task.messages.append(f"  [LLM] {unit.get('source_title', '?')}: 结构化完成")
            else:
                trees.append(None)
        except Exception as e:
            task.messages.append(f"  [LLM] {unit.get('source_title', '?')}: 结构化失败 ({e})")
            trees.append(None)

    return trees


def _build_trees_from_md(md_files: list, work_dir: str, unit_meta_by_stem: dict | None = None, api_key: str = '') -> list:
    """Build tree JSONs from markdown files using tree_builder logic."""
    from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree
    from hierarchy_repair import apply_hierarchy_repair

    tree_dir = os.path.join(work_dir, "trees")
    os.makedirs(tree_dir, exist_ok=True)
    tree_files = []
    unit_meta_by_stem = unit_meta_by_stem or {}

    for md_path in sorted(md_files):
        with open(md_path, encoding="utf-8") as f:
            md_text = f.read().strip()

        filename = os.path.splitext(os.path.basename(md_path))[0]
        unit_meta = unit_meta_by_stem.get(filename, {})
        nodes = parse_md_to_nodes(md_text)
        nodes = adjust_standalone_levels(nodes)
        nodes = apply_hierarchy_repair(nodes)
        tree_children = build_tree(nodes)

        # Validate hierarchy with LLM if API key is available
        if api_key:
            try:
                from tree_builder import validate_and_repair_hierarchy
                tree_children = validate_and_repair_hierarchy(tree_children, api_key)
            except Exception as e:
                print(f"  Hierarchy validation skipped for {filename}: {e}")

        tree = {"title": unit_meta.get("unit_title", filename), "children": tree_children}

        out_path = os.path.join(tree_dir, f"{filename}_tree.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)

        tree_files.append({
            "filename": filename,
            "path": out_path,
            "tree": tree,
            "source_order": unit_meta.get("source_order", 0),
            "source_title": unit_meta.get("source_title", filename),
            "unit_order": unit_meta.get("unit_order", 0),
        })

    return tree_files


def _nest_nodes_by_hierarchy(all_nodes: list) -> list:
    """Fix hierarchy for a flat ordered node list."""
    import copy
    CHAPTER_RE = re.compile(r'^第.{1,3}[章篇]')
    SECTION_RE = re.compile(r'^第.{1,3}节')

    root_children = []
    current_chapter = None
    current_section = None

    for node in all_nodes:
        title = node.get("title", "")
        level = node.get("level", 0)

        if CHAPTER_RE.match(title):
            if "children" not in node:
                node["children"] = []
            root_children.append(node)
            current_chapter = node
            current_section = None
            for child in node.get("children", []):
                if SECTION_RE.match(child.get("title", "")) and child.get("level", 0) <= 2:
                    current_section = child

        elif SECTION_RE.match(title) and level <= 2 and current_chapter is not None:
            current_chapter["children"].append(node)
            current_section = node

        elif current_section is not None:
            current_section["children"].append(node)

        elif current_chapter is not None:
            current_chapter["children"].append(node)

        else:
            root_children.append(node)

    return root_children


def _shift_tree_levels(nodes: list, delta: int):
    """Shift levels in a subtree so source-PDF groups can sit above chapter nodes."""
    def _walk(node: dict):
        if "level" in node:
            node["level"] = max(1, node["level"] + delta)
        for child in node.get("children", []):
            _walk(child)

    for node in nodes:
        _walk(node)


def _collect_titles(nodes: list) -> list[str]:
    titles = []

    def _walk(node: dict):
        title = node.get("title") or node.get("name")
        if title:
            titles.append(title)
        for child in node.get("children", []):
            _walk(child)

    for node in nodes:
        _walk(node)
    return titles


def _build_pdf_context_bridge(previous_title: str, previous_nodes: list, current_title: str, current_nodes: list) -> dict | None:
    """Create a context bridge node between adjacent PDFs."""
    previous_titles = _collect_titles(previous_nodes)
    current_titles = _collect_titles(current_nodes)
    if not previous_titles or not current_titles:
        return None

    return {
        "title": f"与《{previous_title}》的上下文衔接",
        "level": 2,
        "summary": "",
        "keywords": [previous_title, current_title],
        "exam_points": [],
        "content": (
            f"上一份PDF《{previous_title}》的收束主题是“{previous_titles[-1]}”，"
            f"当前PDF《{current_title}》从“{current_titles[0]}”展开。"
            "在组合阅读时，应将两份PDF视为连续知识链条，关注概念承接、问题递进与方法迁移。"
        ),
        "children": [],
    }


def _fix_hierarchy_and_merge(tree_files: list, work_dir: str) -> dict:
    """Merge trees by source PDF order and add cross-PDF context bridges."""
    import copy

    grouped = defaultdict(list)
    source_titles = {}
    source_order = []
    for tf in sorted(tree_files, key=lambda item: (item.get("source_order", 0), item.get("unit_order", 0), item["filename"])):
        order = tf.get("source_order", 0)
        grouped[order].append(tf)
        source_titles[order] = tf.get("source_title", tf["filename"])
        if order not in source_order:
            source_order.append(order)

    root_children = []
    previous_source_title = None
    previous_source_nodes = None

    for order in source_order:
        source_nodes = []
        for tf in grouped[order]:
            for child in tf["tree"].get("children", []):
                source_nodes.append(copy.deepcopy(child))

        nested_children = _nest_nodes_by_hierarchy(source_nodes)
        _shift_tree_levels(nested_children, 1)

        source_title = source_titles[order]
        group_node = {
            "title": source_title,
            "level": 1,
            "summary": "",
            "keywords": [source_title],
            "exam_points": [],
            "content": f"来源PDF：{source_title}",
            "children": nested_children,
        }

        if previous_source_title is not None and previous_source_nodes is not None:
            bridge_node = _build_pdf_context_bridge(previous_source_title, previous_source_nodes, source_title, nested_children)
            if bridge_node is not None:
                group_node["children"].insert(0, bridge_node)

        root_children.append(group_node)
        previous_source_title = source_title
        previous_source_nodes = nested_children

    return {"title": "Knowledge Tree", "children": root_children}


async def _enhance_tree(tree: dict, task: TaskProgress, api_key: str) -> dict:
    """Enhance tree nodes with AI summaries, keywords, and exam points.
    Reports progress to task object and limits scope for responsiveness.
    Now includes adaptive profile detection and structural cleanup."""
    from node_enhancer import (
        build_prompt, get_context_text, should_enhance, collect_postorder,
        detect_document_profile, cleanup_tree_structure,
    )

    MAX_ENHANCE_NODES = 30
    CONSECUTIVE_FAIL_LIMIT = 5
    API_TIMEOUT = 20
    ENHANCE_LEVELS_ALLOWED = {1, 2, 3}

    document_profile = detect_document_profile(tree)
    task.messages.append(f"检测资料类型: {document_profile['label']}")

    to_enhance = []
    for ch in tree.get("children", []):
        collect_postorder(ch, to_enhance)

    # Filter: only levels 1-3
    to_enhance = [n for n in to_enhance if n.get("level", 0) in ENHANCE_LEVELS_ALLOWED]

    total = len(to_enhance)
    if total == 0:
        task.set_stage("ai_enhancing", 90, "无需增强的节点 (所有节点已有摘要或层级超出范围)")
        return tree

    # Cap to max nodes
    if total > MAX_ENHANCE_NODES:
        to_enhance.sort(key=lambda n: n.get("level", 0))
        to_enhance = to_enhance[:MAX_ENHANCE_NODES]
        task.messages.append(f"节点过多, 限制为 {MAX_ENHANCE_NODES}/{total} 个 (优先增强高层级节点)")

    total = len(to_enhance)
    task.set_stage("ai_enhancing", 80, f"AI增强中: 0/{total} 节点...")

    # Quick API health check
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        try:
            test_payload = {
                "model": MODEL,
                "max_tokens": 20,
                "temperature": 0,
                "messages": [{"role": "user", "content": "回复OK"}]
            }
            resp = await client.post(
                OPENAI_BASE_URL,
                json=test_payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
            )
            if resp.status_code != 200:
                task.set_stage("ai_enhancing", 81, f"AI API不可用 (HTTP {resp.status_code}), 跳过增强")
                return tree
        except Exception as e:
            task.set_stage("ai_enhancing", 81, f"AI API不可用 ({e}), 跳过增强")
            return tree

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    done_count = 0
    fail_count = 0
    fail_lock = asyncio.Lock()

    async def enhance_one(client: httpx.AsyncClient, node: dict) -> None:
        nonlocal done_count, fail_count
        async with semaphore:
            await asyncio.sleep(REQUEST_DELAY)
            prompt = build_prompt(node, SUBJECT_CONFIG, get_context_text(node), "", document_profile)
            payload = {
                "model": MODEL,
                "max_tokens": 1000,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]
            }
            try:
                resp = await client.post(
                    OPENAI_BASE_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    timeout=API_TIMEOUT
                )
                if resp.status_code != 200:
                    async with fail_lock:
                        fail_count += 1
                    return
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                raw = re.sub(r'^```json\s*|\s*```$', '', raw)
                result = json.loads(raw)
                node["summary"] = result.get("summary", "")
                node["keywords"] = result.get("keywords", [])
                node["exam_points"] = result.get("exam_points", [])
                node["mermaid"] = result.get("mermaid", "") or ""
                node["tables"] = result.get("tables", []) or []
            except Exception:
                async with fail_lock:
                    fail_count += 1
            finally:
                done_count += 1
                base_pct = 82
                max_pct = 94
                pct = base_pct + int((done_count / total) * (max_pct - base_pct))
                task.set_stage("ai_enhancing", pct,
                               f"AI增强中: {done_count}/{total} (失败 {fail_count})")

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        for node in to_enhance:
            if fail_count >= CONSECUTIVE_FAIL_LIMIT:
                task.set_stage("ai_enhancing", 94, f"连续失败 {fail_count} 次, 停止增强 (已完成 {done_count})")
                break

            await enhance_one(client, node)

    task.set_stage("ai_enhancing", 95,
                   f"AI增强完成: {done_count - fail_count}/{total} 成功, {fail_count} 失败")

    task.set_stage("ai_enhancing", 96, "正在整理结构...")
    tree = cleanup_tree_structure(tree)
    task.set_stage("ai_enhancing", 97, "结构整理完成")
    return tree


def _prepare_final_json(tree: dict) -> dict:
    """Add depth/level info and clean up the tree for frontend."""
    from node_enhancer import cleanup_tree_structure

    tree = cleanup_tree_structure(tree)

    def _walk(node, depth=0):
        node["depth"] = depth
        if "level" not in node:
            node["level"] = min(depth, 5)
        if "summary" not in node:
            node["summary"] = ""
        if "keywords" not in node:
            node["keywords"] = []
        if "exam_points" not in node:
            node["exam_points"] = []
        if "mermaid" not in node:
            node["mermaid"] = ""
        if "tables" not in node:
            node["tables"] = []
        if "children" not in node:
            node["children"] = []
        for child in node.get("children", []):
            _walk(child, depth + 1)

    for ch in tree.get("children", []):
        _walk(ch, 1)

    return tree


# ────────────────────────────────────────────
# API Endpoints
# ────────────────────────────────────────────

@app.post("/api/upload")
async def api_upload(
    files: list[UploadFile] = File(...),
    mineru_token: str = Form(""),
    deepseek_api_key: str = Form(""),
):
    task_id = str(uuid.uuid4())[:8]
    task_label = files[0].filename if len(files) == 1 else f"{len(files)} 个文档"
    task = TaskProgress(
        task_id,
        task_label,
        mineru_token=mineru_token.strip() or None,
        deepseek_api_key=deepseek_api_key.strip() or None,
    )

    # Save uploaded files
    work_dir = os.path.join(TEMP_DIR, task_id)
    os.makedirs(work_dir, exist_ok=True)
    uploads_dir = os.path.join(work_dir, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    uploaded_files = []

    for index, upload in enumerate(files, start=1):
        content = await upload.read()
        filename = f"{index:02d}_{sanitize_filename(upload.filename)}"
        file_path = os.path.join(uploads_dir, filename)
        with open(file_path, "wb") as f:
            f.write(content)
        uploaded_files.append({"path": file_path, "original_name": upload.filename})
        ft = detect_file_type(upload.filename)
        type_label = {"pdf": "PDF", "word": "Word", "text": "TXT"}.get(ft, ft)
        task.messages.append(f"文件已上传: [{type_label}] {upload.filename} ({len(content) / 1024 / 1024:.1f} MB)")

    with tasks_lock:
        tasks[task_id] = task

    # Launch pipeline in background thread
    thread = threading.Thread(target=run_pipeline, args=(task, uploaded_files), daemon=True)
    thread.start()

    return JSONResponse({"task_id": task_id, "filenames": [upload.filename for upload in files]})


@app.get("/api/status/{task_id}")
async def api_status(task_id: str):
    with tasks_lock:
        task = tasks.get(task_id)

    if task is None:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    return JSONResponse(task.to_dict())


@app.get("/api/status/{task_id}/stream")
async def api_status_stream(task_id: str, request: Request):
    """SSE endpoint for real-time progress updates."""
    with tasks_lock:
        task = tasks.get(task_id)

    if task is None:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    async def event_generator():
        last_progress = -1
        while True:
            if await request.is_disconnected():
                break

            current_data = task.to_dict()
            if current_data["progress"] != last_progress:
                yield f"data: {json.dumps(current_data, ensure_ascii=False)}\n\n"
                last_progress = current_data["progress"]

            if task.status in ("done", "error"):
                yield f"data: {json.dumps(task.to_dict(), ensure_ascii=False)}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/result/{task_id}")
async def api_result(task_id: str):
    with tasks_lock:
        task = tasks.get(task_id)

    if task is not None:
        if task.status == "error":
            return JSONResponse({"error": task.error, "messages": task.messages}, status_code=500)
        if task.status != "done":
            return JSONResponse({"status": task.status, "progress": task.progress}, status_code=202)
        return JSONResponse(task.result)

    # Fallback: try loading from disk
    json_path = os.path.join(TEMP_DIR, task_id, "json", "knowledge_tree.json")
    if os.path.isfile(json_path):
        with open(json_path, encoding="utf-8") as f:
            return JSONResponse(json.load(f))

    return JSONResponse({"error": "Task not found"}, status_code=404)


@app.get("/api/export/{task_id}")
async def api_export(task_id: str):
    """Export the completed result as folders + HTML that loads local JSON."""
    with tasks_lock:
        task = tasks.get(task_id)

    work_dir = os.path.join(TEMP_DIR, task_id)
    export_json_dir = os.path.join(work_dir, "json")
    final_json_path = os.path.join(export_json_dir, "knowledge_tree.json")

    if task is not None and task.status == "done":
        final_result = task.result
    elif os.path.exists(final_json_path):
        with open(final_json_path, encoding="utf-8") as f:
            final_result = json.load(f)
    elif task is None:
        return JSONResponse({"error": "Task not found or export data expired"}, status_code=404)
    else:
        return JSONResponse({"error": "Task not yet completed"}, status_code=400)

    html_content = _generate_offline_html()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mindmap.html", html_content)
        _write_directory_to_zip(zf, os.path.join(work_dir, "markdown"), "md")
        if os.path.isdir(export_json_dir):
            _write_directory_to_zip(zf, export_json_dir, "json")
        else:
            _write_directory_to_zip(zf, os.path.join(work_dir, "trees"), "json")
            zf.writestr(
                "json/knowledge_tree.json",
                json.dumps(final_result, ensure_ascii=False, indent=2),
            )
    buf.seek(0)

    filename = f"knowledge_tree_{task_id}.zip"
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def _write_directory_to_zip(zf: zipfile.ZipFile, source_dir: str, archive_dir: str):
    """Write a directory into the zip under the given archive folder."""
    if not os.path.isdir(source_dir):
        return

    for root, _, files in os.walk(source_dir):
        for name in sorted(files):
            file_path = os.path.join(root, name)
            rel_path = os.path.relpath(file_path, source_dir)
            archive_path = os.path.join(archive_dir, rel_path).replace("\\", "/")
            zf.write(file_path, archive_path)


def _generate_offline_html() -> str:
    """Generate offline HTML that loads tree data from json/knowledge_tree.json."""
    template_path = os.path.join(UI_DIR, "tree_mindmap.html")
    html = Path(template_path).read_text(encoding="utf-8")

    html = html.replace(
        "<title>Knowledge Tree - Mind Map</title>",
        "<title>Knowledge Tree - Offline Mind Map</title>",
    )
    html = html.replace("Loading knowledge tree...", "Loading json/knowledge_tree.json ...")
    html = html.replace(" | Parts: ", " | Chapters: ")

    loader_start = html.index("async function loadAllParts() {")
    loader_end = html.index("function flattenNodes", loader_start)
    loader_replacement = """async function loadTreeData() {
  const response = await fetch('./json/knowledge_tree.json', { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load json/knowledge_tree.json: ${response.status}`);
  }
  const rawData = await response.json();
  return transformNode(rawData);
}

"""
    html = html[:loader_start] + loader_replacement + html[loader_end:]
    html = html.replace("treeData = await loadAllParts();", "treeData = await loadTreeData();")
    html = html.replace(
        "Make sure you are serving this file via HTTP (e.g. <code>python -m http.server</code>), not opening directly as file://",
        "Make sure <code>mindmap.html</code> is next to the <code>chapters/</code>, <code>md/</code>, and <code>json/</code> folders after unzip, and serve the folder via HTTP (e.g. <code>python -m http.server</code>)",
    )
    return html

@app.post("/api/update-node")
async def api_update_node(data: dict):
    """Update a node's title/content in the source JSON file (enhanced tree)."""
    filename = data.get("filename", "")
    path_in_file = data.get("path", [])
    new_title = data.get("title", "").strip()
    new_content = data.get("content")  # optional, None means don't update

    if not filename or not new_title:
        return JSONResponse({"error": "filename and title are required"}, status_code=400)

    # Sanitize filename to prevent path traversal
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed", safe_filename)

    if not os.path.exists(filepath):
        return JSONResponse({"error": f"File not found: {safe_filename}"}, status_code=404)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return JSONResponse({"error": f"Failed to read file: {str(e)}"}, status_code=500)

    # Navigate to the target node
    try:
        node = tree
        for idx in path_in_file:
            node = node["children"][idx]
    except (IndexError, KeyError, TypeError):
        return JSONResponse({"error": "Invalid node path"}, status_code=400)

    node["title"] = new_title
    if "name" in node:
        node["name"] = new_title
    if new_content is not None:
        node["content"] = new_content

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)
    except IOError as e:
        return JSONResponse({"error": f"Failed to write file: {str(e)}"}, status_code=500)

    return {"status": "ok", "title": new_title, "content_updated": new_content is not None}


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "tasks": len(tasks)}


@app.get("/api/stats")
async def api_stats():
    """Return knowledge tree count and total node count."""
    tree_count = 0
    node_count = 0

    # Count from tree_parts_enhanced_fixed
    enhanced_dir = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed")
    if os.path.isdir(enhanced_dir):
        for f in os.listdir(enhanced_dir):
            if f.endswith(".json"):
                tree_count += 1
                try:
                    with open(os.path.join(enhanced_dir, f), encoding="utf-8") as fh:
                        data = json.load(fh)
                    node_count += _count_nodes(data)
                except Exception:
                    pass

    # Count from completed tasks
    if os.path.isdir(TEMP_DIR):
        for task_dir in os.listdir(TEMP_DIR):
            json_path = os.path.join(TEMP_DIR, task_dir, "json", "knowledge_tree.json")
            if os.path.isfile(json_path):
                tree_count += 1
                try:
                    with open(json_path, encoding="utf-8") as fh:
                        data = json.load(fh)
                    node_count += _count_nodes(data)
                except Exception:
                    pass

    return {"tree_count": tree_count, "node_count": node_count}


def _count_nodes(node: dict) -> int:
    """Count total nodes in a tree recursively."""
    count = 1
    for child in node.get("children", []):
        count += _count_nodes(child)
    return count


@app.get("/api/files")
async def api_files():
    """List knowledge tree JSON files available locally."""
    files = []

    # From tree_parts_enhanced_fixed
    enhanced_dir = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed")
    if os.path.isdir(enhanced_dir):
        for f in sorted(os.listdir(enhanced_dir)):
            if f.endswith(".json"):
                fpath = os.path.join(enhanced_dir, f)
                stat = os.stat(fpath)

                # Count nodes
                node_count = 0
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        data = json.load(fh)
                    node_count = _count_nodes(data)
                except Exception:
                    pass

                files.append({
                    "filename": f,
                    "source": "enhanced",
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "original_files": [],
                    "node_count": node_count,
                })

    # From completed tasks
    if os.path.isdir(TEMP_DIR):
        for task_dir in sorted(os.listdir(TEMP_DIR)):
            json_path = os.path.join(TEMP_DIR, task_dir, "json", "knowledge_tree.json")
            if os.path.isfile(json_path):
                stat = os.stat(json_path)

                # Find original uploaded filenames
                uploads_dir = os.path.join(TEMP_DIR, task_dir, "uploads")
                original_files = []
                if os.path.isdir(uploads_dir):
                    for uf in sorted(os.listdir(uploads_dir)):
                        # Remove index prefix like "01_"
                        clean_name = re.sub(r'^\d+_', '', uf)
                        original_files.append(clean_name)

                # Count nodes in the tree
                node_count = 0
                try:
                    with open(json_path, encoding="utf-8") as fh:
                        data = json.load(fh)
                    node_count = _count_nodes(data)
                except Exception:
                    pass

                files.append({
                    "filename": f"task_{task_dir}",
                    "task_id": task_dir,
                    "source": "task",
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "original_files": original_files,
                    "node_count": node_count,
                })

    return {"files": files}


@app.get("/api/tasks")
async def api_tasks():
    """List historical tasks with status and metadata."""
    result = []

    if not os.path.isdir(TEMP_DIR):
        return {"tasks": result}

    for task_dir in sorted(os.listdir(TEMP_DIR), reverse=True):
        work_dir = os.path.join(TEMP_DIR, task_dir)
        json_path = os.path.join(work_dir, "json", "knowledge_tree.json")
        status = "done" if os.path.isfile(json_path) else "incomplete"

        # Find original filename from uploads
        uploads_dir = os.path.join(work_dir, "uploads")
        original_name = task_dir
        if os.path.isdir(uploads_dir):
            upload_files = os.listdir(uploads_dir)
            if upload_files:
                original_name = upload_files[0]

        stat = os.stat(work_dir)
        result.append({
            "task_id": task_dir,
            "filename": original_name,
            "status": status,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })

    return {"tasks": result}


@app.get("/api/file/{filename}")
async def api_file(filename: str):
    """Load a specific knowledge tree JSON file."""
    safe_filename = os.path.basename(filename)

    # Search in tree_parts_enhanced_fixed
    enhanced_dir = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed")
    filepath = os.path.join(enhanced_dir, safe_filename)
    if os.path.isfile(filepath):
        with open(filepath, encoding="utf-8") as f:
            return JSONResponse(json.load(f))

    # Search in task directories
    if os.path.isdir(TEMP_DIR):
        for task_dir in os.listdir(TEMP_DIR):
            json_path = os.path.join(TEMP_DIR, task_dir, "json", "knowledge_tree.json")
            if os.path.isfile(json_path) and f"task_{task_dir}" == safe_filename:
                with open(json_path, encoding="utf-8") as f:
                    return JSONResponse(json.load(f))

    return JSONResponse({"error": f"File not found: {safe_filename}"}, status_code=404)


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting Knowledge Tree Pipeline Server on {host}:{port}...")
    print(f"Temp dir: {TEMP_DIR}")
    uvicorn.run(app, host=host, port=port)
