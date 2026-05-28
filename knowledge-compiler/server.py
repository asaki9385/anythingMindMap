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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "sk-7ef0ecb0da964eb6a8e331cbf952e9a1")
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
    return FileResponse(os.path.join(UI_DIR, "upload_mindmap.html"))


# ────────────────────────────────────────────
# Task management
# ────────────────────────────────────────────

class TaskProgress:
    def __init__(self, task_id: str, filename: str):
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

def run_pipeline(task: TaskProgress, uploaded_pdfs: list[dict]):
    """Run the full pipeline in a background thread."""
    work_dir = os.path.join(TEMP_DIR, task.task_id)
    os.makedirs(work_dir, exist_ok=True)

    try:
        # ── Stage 1: Split PDF ──
        task.set_stage("splitting", 5, f"正在准备 {len(uploaded_pdfs)} 个PDF...")
        pdf_units = _prepare_pdf_processing_units(uploaded_pdfs, work_dir, task)
        task.set_stage("splitting", 15, f"已生成 {len(pdf_units)} 个处理单元")

        if not pdf_units:
            task.set_error("PDF预处理失败：未生成可识别文件")
            return

        # ── Stage 2: MinerU OCR → Markdown ──
        task.set_stage("ocr_converting", 20, "正在调用MinerU识别文本...")

        try:
            results = upload_and_process_all([unit["path"] for unit in pdf_units], is_ocr=True)
            md_dir = os.path.join(work_dir, "markdown")
            md_files = download_markdowns(results, md_dir)
            task.set_stage("ocr_converting", 40, f"MinerU识别完成，{len(md_files)} 个Markdown文件")
        except Exception as e:
            task.set_stage("ocr_converting", 40, f"MinerU不可用，尝试直接处理PDF文本 (MinerU error: {e})")
            # Fallback: use PyMuPDF to extract text directly
            md_dir = os.path.join(work_dir, "markdown")
            os.makedirs(md_dir, exist_ok=True)
            md_files = _fallback_pdf_to_md(pdf_units, md_dir)
            task.messages.append(f"回退模式：使用PDF直接提取文本，{len(md_files)} 个文件")

        if not md_files:
            task.set_error("文本提取失败")
            return

        # ── Stage 3: Build trees ──
        task.set_stage("building_tree", 45, "正在构建知识树...")
        unit_meta_by_stem = {Path(unit["path"]).stem: unit for unit in pdf_units}
        tree_files = _build_trees_from_md(md_files, work_dir, unit_meta_by_stem)
        task.set_stage("building_tree", 60, f"知识树构建完成，{len(tree_files)} 棵树")

        # ── Stage 4: Fix hierarchy ──
        task.set_stage("building_tree", 65, "正在修复层级结构...")
        merged_tree = _fix_hierarchy_and_merge(tree_files, work_dir)
        task.set_stage("building_tree", 75, "层级结构修复完成")

        # ── Stage 5: AI Enhancement ──
        task.set_stage("ai_enhancing", 80, "正在AI增强节点(添加摘要/关键词/考点)...")
        try:
            enhanced_tree = asyncio.run(_enhance_tree(merged_tree, task))
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
            shutil.move(split_path, output_path)
            units.append({
                "path": output_path,
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


def _fallback_pdf_to_md(pdf_units: list[dict], output_dir: str) -> list:
    """Fallback: extract text from PDFs using PyMuPDF when MinerU is unavailable."""
    import fitz
    md_files = []
    for unit in pdf_units:
        pdf_path = unit["path"]
        doc = fitz.open(pdf_path)
        text_parts = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                text_parts.append(text)
        doc.close()

        basename = os.path.splitext(os.path.basename(pdf_path))[0]
        md_path = os.path.join(output_dir, f"{basename}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(text_parts))
        md_files.append(md_path)
    return md_files


def _build_trees_from_md(md_files: list, work_dir: str, unit_meta_by_stem: dict | None = None) -> list:
    """Build tree JSONs from markdown files using tree_builder logic."""
    from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree

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
        tree_children = build_tree(nodes)
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


async def _enhance_tree(tree: dict, task: TaskProgress) -> dict:
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
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
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
                "max_tokens": 600,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]
            }
            try:
                resp = await client.post(
                    OPENAI_BASE_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
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
async def api_upload(files: list[UploadFile] = File(...)):
    task_id = str(uuid.uuid4())[:8]
    task_label = files[0].filename if len(files) == 1 else f"{len(files)} PDFs"
    task = TaskProgress(task_id, task_label)

    # Save uploaded files
    work_dir = os.path.join(TEMP_DIR, task_id)
    os.makedirs(work_dir, exist_ok=True)
    uploads_dir = os.path.join(work_dir, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    uploaded_pdfs = []

    for index, upload in enumerate(files, start=1):
        content = await upload.read()
        filename = f"{index:02d}_{sanitize_filename(upload.filename)}"
        pdf_path = os.path.join(uploads_dir, filename)
        with open(pdf_path, "wb") as f:
            f.write(content)
        uploaded_pdfs.append({"path": pdf_path, "original_name": upload.filename})
        task.messages.append(f"文件已上传: {upload.filename} ({len(content) / 1024 / 1024:.1f} MB)")

    with tasks_lock:
        tasks[task_id] = task

    # Launch pipeline in background thread
    thread = threading.Thread(target=run_pipeline, args=(task, uploaded_pdfs), daemon=True)
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

    if task is None:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    if task.status == "error":
        return JSONResponse({"error": task.error, "messages": task.messages}, status_code=500)

    if task.status != "done":
        return JSONResponse({"status": task.status, "progress": task.progress}, status_code=202)

    return JSONResponse(task.result)


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
        _write_directory_to_zip(zf, os.path.join(work_dir, "chapters"), "chapters")
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

@app.get("/api/health")
async def api_health():
    return {"status": "ok", "tasks": len(tasks)}


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("Starting Knowledge Tree Pipeline Server...")
    print(f"Temp dir: {TEMP_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8700)
