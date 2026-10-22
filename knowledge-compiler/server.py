"""
AnythingMindmap - PDF Upload & Knowledge Map Server
Orchestrates: PDF split → MinerU OCR → Tree build → AI enhance → Mind map JSON
"""

import json
import io
import re
import zipfile
import os
import sys
import time
import uuid
import shutil
import asyncio
import threading
import traceback
import base64
import hmac
import hashlib
import tempfile
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
from structure_score import score_fragment
from mineru_adapter.client import upload_and_process_all, download_markdowns

import parse_report

import re
import httpx
from collections import defaultdict

# ────────────────────────────────────────────
# Config
# ────────────────────────────────────────────

TEMP_DIR = os.path.join(BASE_DIR, "data", "_temp_uploads")
os.makedirs(TEMP_DIR, exist_ok=True)
ENHANCED_DIR = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed")
os.makedirs(ENHANCED_DIR, exist_ok=True)
FAVORITES_PATH = os.path.join(BASE_DIR, "data", "favorites.json")

# 内容级缓存：相同文件 + 相同模型/OCR 设置 + 相同管线版本时复用最终结果。
CACHE_DIR = os.path.join(BASE_DIR, "data", "_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_VERSION = "2026-10-17-v1"
cache_lock = threading.Lock()

# AI Enhancement config
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
SUBJECT_CONFIG = {"name": "教育学", "exam": "333教育综合考研"}
ENHANCE_LEVELS = {1: True, 2: True, 3: True, 4: True, 5: False}
MAX_CONCURRENT = 5
REQUEST_DELAY = 0.3
NO_SPLIT_SIZE_MB = 200

# Task storage (in-memory; persisted to task_state.json as well)
tasks: dict = {}
tasks_lock = threading.Lock()

# CORS：默认只允许同源。需要跨域开发时用 CORS_ORIGINS 显式列出，例如
# CORS_ORIGINS=http://localhost:8700,http://127.0.0.1:8700
CORS_ORIGINS = [
    item.strip()
    for item in os.environ.get("CORS_ORIGINS", "").split(",")
    if item.strip()
]

# 可选 HTTP Basic Auth：设置 KT_AUTH_PASSWORD 后，所有页面/API（健康检查除外）
# 都需要用户名+密码。默认不启用，保持本地单用户使用的零配置体验。
AUTH_USER = os.environ.get("KT_AUTH_USER", "admin")
AUTH_PASSWORD = os.environ.get("KT_AUTH_PASSWORD", "")

# ────────────────────────────────────────────
# FastAPI App
# ────────────────────────────────────────────

app = FastAPI(title="AnythingMindmap Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """可选 Basic Auth；默认不设密码，保持本地零配置体验。"""
    if (
        not AUTH_PASSWORD
        or request.method == "OPTIONS"
        or request.url.path == "/api/health"
    ):
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            expected = f"{AUTH_USER}:{AUTH_PASSWORD}"
            if hmac.compare_digest(decoded, expected):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        content="Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="AnythingMindmap"'},
        media_type="text/plain",
    )

@app.middleware("http")
async def no_cache_static(request, call_next):
    """静态资源统一 no-cache。

    StaticFiles 默认不发 Cache-Control，浏览器会按启发式规则（约 last-modified 至今的 10%）
    长时间缓存 JS/CSS，于是出现「HTML 是新的、common.js 还是旧的」的混版状态，典型报错是
    KT.bookModules is not a function。加 no-cache 后每次仍走 ETag 协商：文件没变返回 304，
    代价很小，但不会再出现混版。
    """
    response = await call_next(request)
    if request.url.path.startswith("/ui/") and response.status_code < 400:
        response.headers["Cache-Control"] = "no-cache"
    return response


# Mount static files for UI
UI_DIR = os.path.join(BASE_DIR, "ui")
if os.path.isdir(UI_DIR):
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/client.html")


# ────────────────────────────────────────────
# Task management
# ────────────────────────────────────────────

class TaskProgress:
    def __init__(self, task_id: str, filename: str,
                 mineru_token: str | None = None,
                 deepseek_api_key: str | None = None):
        self.task_id = task_id
        self.filename = filename
        # 资料级可读标题（AI 生成/用户编辑）；filename 保留原始文件名用于溯源。
        self.title = ""
        self.source_names = []
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
        # 解析报告（token 用量 / 节点规模 / 文件与页数），完成后填充，
        # 由 /api/report/{task_id} 与 /api/status 一起交给前端的数据面板。
        self.report = None
        self.token_usage = None      # parse_report.UsageMeter 实例
        self.ai_meta = None          # 增强阶段：模型、目标/成功/兜底节点数
        self.audit_meta = None       # 结构审校报告
        self.skeleton_meta = None    # 骨架恢复/校验报告（解析前置 + 增强前置）
        self.structure_judgements = []   # 片段级结构判断（每片段走 regex 还是 AI 骨架）
        self.cache_key = ""
        self.cache_hit = False
        self._event = threading.Event()

    def _state_path(self) -> str:
        return os.path.join(TEMP_DIR, self.task_id, "task_state.json")

    def persist_state(self) -> None:
        """把任务状态原子落盘；失败不影响主流程。"""
        try:
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = self.to_dict()
            payload["messages"] = self.messages[-50:]
            payload["updated_at"] = time.time()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[{self.task_id}] WARN task state persist failed: {e}")

    def set_stage(self, stage: str, progress: float = None, message: str = ""):
        self.stage = stage
        if progress is not None:
            self.progress = progress
        if message:
            self.messages.append(message)
            print(f"[{self.task_id}] {message}")
        self.persist_state()

    def set_done(self, result: dict):
        self.status = "done"
        self.progress = 100
        self.result = result
        self.stage = "complete"
        self._event.set()
        self.persist_state()

    def set_error(self, error: str):
        self.status = "error"
        self.error = error
        self.messages.append(f"ERROR: {error}")
        self._event.set()
        print(f"[{self.task_id}] ERROR: {error}")
        self.persist_state()

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "title": self.title,
            "source_names": list(self.source_names),
            "stage": self.stage,
            "progress": self.progress,
            "status": self.status,
            "messages": self.messages[-20:],
            "error": self.error,
            "report": self.report,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
        }


def load_task_state(task_id: str, recover_interrupted: bool = False):
    """读取 task_state.json；内存任务丢失后仍能返回真实状态。

    recover_interrupted=True 时，把上次进程遗留的 processing 标成 error，
    避免前端无限轮询一个已经随进程退出的任务。
    """
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", task_id or ""):
        return None
    path = os.path.join(TEMP_DIR, task_id, "task_state.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    if recover_interrupted and state.get("status") == "processing":
        state["status"] = "error"
        state["error"] = "服务重启，任务已中断，请重新上传"
        state["updated_at"] = time.time()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception:
            pass
    return state


# ────────────────────────────────────────────
# Content cache
# ────────────────────────────────────────────

def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_cache_key(uploaded_files: list[dict], chat: dict | None,
                     mineru_token: str | None) -> str:
    """输入文件 + 模型/OCR 设置 + 管线版本 → 内容缓存键。"""
    import app_settings
    files = []
    for f in uploaded_files:
        digest = f.get("sha256") or _file_sha256(f["path"])
        files.append({"name": f.get("original_name", ""), "sha256": digest})
    chat_sig = None
    if chat:
        chat_sig = {
            "base_url": chat.get("base_url", ""),
            "model": chat.get("model", ""),
            "api_key": hashlib.sha256(
                (chat.get("api_key") or "").encode("utf-8")
            ).hexdigest()[:16],
        }
    payload = {
        "version": CACHE_VERSION,
        "files": files,
        "chat": chat_sig,
        "mineru": hashlib.sha256(
            (mineru_token or "").encode("utf-8")
        ).hexdigest()[:16],
        "subject": app_settings.subject_config(),
        "enhance": app_settings.enhance_config(),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _copy_result_dirs(src_dir: str, dst_dir: str) -> None:
    os.makedirs(dst_dir, exist_ok=True)
    for name in ("json", "markdown", "trees"):
        src = os.path.join(src_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst_dir, name), dirs_exist_ok=True)
    report_src = os.path.join(src_dir, "report.json")
    if os.path.isfile(report_src):
        shutil.copy2(report_src, os.path.join(dst_dir, "report.json"))


def _store_cache(cache_key: str, work_dir: str) -> None:
    """把可复用结果原子写入 data/_cache/<key>/；失败不影响主流程。"""
    if not cache_key:
        return
    final_dir = os.path.join(CACHE_DIR, cache_key)
    if os.path.isdir(final_dir):
        return
    with cache_lock:
        if os.path.isdir(final_dir):
            return
        tmp_dir = tempfile.mkdtemp(prefix=f".{cache_key[:8]}-", dir=CACHE_DIR)
        try:
            _copy_result_dirs(work_dir, tmp_dir)
            title = ""
            try:
                with open(os.path.join(work_dir, "json", "knowledge_tree.json"), encoding="utf-8") as f:
                    title = (json.load(f).get("title") or "").strip()
            except Exception:
                pass
            with open(os.path.join(tmp_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "cache_key": cache_key,
                    "version": CACHE_VERSION,
                    "title": title,
                    "created_at": time.time(),
                }, f, ensure_ascii=False, indent=1)
            os.replace(tmp_dir, final_dir)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"[cache] store skipped: {e}")


def _restore_cache(task: "TaskProgress", uploaded_files: list[dict], started: float) -> bool:
    """命中缓存时把最终树/报告复制到当前任务目录，并直接完成任务。"""
    cache_key = getattr(task, "cache_key", "")
    src_dir = os.path.join(CACHE_DIR, cache_key)
    tree_src = os.path.join(src_dir, "json", "knowledge_tree.json")
    if not cache_key or not os.path.isfile(tree_src):
        return False
    work_dir = os.path.join(TEMP_DIR, task.task_id)
    try:
        _copy_result_dirs(src_dir, work_dir)
        with open(tree_src, encoding="utf-8") as f:
            tree = json.load(f)
        # 旧缓存可能仍带 images/ 正文引用；命中时顺手清理并写回任务目录。
        from tree_builder import sanitize_tree_images
        tree = sanitize_tree_images(tree)
        task.source_names = [
            f.get("original_name", "") for f in uploaded_files if f.get("original_name")
        ]
        cached_title = ""
        try:
            with open(os.path.join(src_dir, "meta.json"), encoding="utf-8") as f:
                cached_title = (json.load(f).get("title") or "").strip()
        except Exception:
            pass
        from document_title import apply_document_title, fallback_document_title
        title = cached_title or (tree.get("title") or "").strip()
        if not title or title in ("Knowledge Tree", "AnythingMindmap"):
            title = fallback_document_title(task.source_names or [task.filename], tree)
        task.title = title
        apply_document_title(tree, title)
        dst_tree = os.path.join(work_dir, "json", "knowledge_tree.json")
        if os.path.isdir(os.path.dirname(dst_tree)):
            with open(dst_tree, "w", encoding="utf-8") as f:
                json.dump(tree, f, ensure_ascii=False, indent=2)
        report_path = os.path.join(work_dir, "report.json")
        if os.path.isfile(report_path):
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
        else:
            report = parse_report.build_report(tree, task_id=task.task_id)

        pdf_files = [f for f in uploaded_files if detect_file_type(f["original_name"]) == "pdf"]
        type_counts = {
            "pdf": len(pdf_files),
            "word": sum(1 for f in uploaded_files if detect_file_type(f["original_name"]) == "word"),
            "text": sum(1 for f in uploaded_files if detect_file_type(f["original_name"]) == "text"),
        }
        report["task_id"] = task.task_id
        report["cached"] = True
        report["cache_key"] = cache_key
        report["generated_at"] = time.time()
        report["duration_sec"] = round(time.time() - started, 1)
        report["files"] = {
            "count": len(uploaded_files),
            "names": [f.get("original_name", "") for f in uploaded_files][:20],
            "pages": _count_pdf_pages(pdf_files),
            **type_counts,
        }
        task.report = report
        _save_report(work_dir, report)
        task.cache_hit = True
        task.set_stage("merging", 98, "正在准备缓存结果…")
        task.messages.append("命中内容缓存：相同文件与设置，已跳过 OCR 与 AI 增强")
        task.messages.append(parse_report.summarize_for_message(report))
        task.set_done(tree)
        return True
    except Exception as e:
        task.messages.append(f"缓存读取失败，继续正常处理 ({e})")
        return False


def _update_cached_tree(cache_key: str, tree: dict) -> None:
    """节点被编辑后同步缓存树，避免下次重传命中旧内容。"""
    if not cache_key:
        return
    path = os.path.join(CACHE_DIR, cache_key, "json", "knowledge_tree.json")
    if not os.path.isfile(path):
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        meta_path = os.path.join(CACHE_DIR, cache_key, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if isinstance(meta, dict):
                meta["title"] = (tree.get("title") or "").strip()
                with open(meta_path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=1)
                os.replace(meta_path + ".tmp", meta_path)
    except Exception as e:
        print(f"[cache] update skipped: {e}")


# ────────────────────────────────────────────
# Pipeline functions
# ────────────────────────────────────────────

def run_pipeline(task: TaskProgress, uploaded_files: list[dict]):
    """Run the full pipeline in a background thread.
    Supports mixed file types: PDF, Word (.docx), and plain text (.txt).

    凭证解析（统一配置中心）：
      本次上传填写的 > data/settings.json 统一配置 > 环境变量 > 无（离线降级）
    """
    work_dir = os.path.join(TEMP_DIR, task.task_id)
    os.makedirs(work_dir, exist_ok=True)
    started = time.time()
    if not task.source_names:
        task.source_names = [
            f.get("original_name", "") for f in uploaded_files if f.get("original_name")
        ]

    import app_settings
    chat = app_settings.resolve_chat(api_key=task.deepseek_api_key or "")
    mineru_token = task.mineru_token or app_settings.mineru_token() or None
    if chat:
        task.messages.append(f"模型服务：{chat['provider_name']}（{chat['model']}）")
    if mineru_token and not task.mineru_token:
        task.messages.append("MinerU Token：来自统一配置")

    try:
        # 以线程启动时实际解析到的模型/OCR 配置重算键，避免上传与执行之间
        # 设置变化导致命中错误缓存。
        try:
            if uploaded_files:
                task.cache_key = _build_cache_key(uploaded_files, chat, mineru_token)
        except Exception as e:
            task.cache_key = ""
            task.messages.append(f"缓存键计算跳过 ({e})")

        # 内容级缓存：相同文件 + 相同模型/OCR 设置 + 相同管线版本直接复用。
        if task.cache_key and _restore_cache(task, uploaded_files, started):
            return

        # ── Separate files by type ──
        pdf_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'pdf']
        word_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'word']
        txt_files = [f for f in uploaded_files if detect_file_type(f['original_name']) == 'text']
        text_files = word_files + txt_files
        # 报告用：PDF 总页数。MinerU 不返回 token，页数是"OCR 实际干了多少活"最接近的度量。
        pdf_pages = _count_pdf_pages(pdf_files)

        total = len(uploaded_files)
        task.set_stage("splitting", 5, f"正在准备 {total} 个文档…")

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

        task.set_stage("splitting", 15, f"已拆分为 {len(all_units)} 个部分")

        if not all_units:
            task.set_error("预处理失败：未生成可识别文件")
            return

        # ── Stage 2: MinerU OCR → Markdown (PDF only) ──
        pdf_units = [u for u in all_units if u['split_mode'] in ('whole_pdf', 'chapter')]

        if pdf_units:
            if not mineru_token:
                task.set_stage("ocr_converting", 20, "未配置云端识别，改用本地文字提取")
                md_dir = os.path.join(work_dir, "markdown")
                os.makedirs(md_dir, exist_ok=True)
                fallback_md = _fallback_pdf_to_md(pdf_units, md_dir)
                md_files.extend(fallback_md)
                task.set_stage("ocr_converting", 40, f"文字提取完成，共 {len(fallback_md)} 个文件")
            else:
                task.set_stage("ocr_converting", 20, f"正在识别 {len(pdf_units)} 个文档…")
                try:
                    results = upload_and_process_all([unit["path"] for unit in pdf_units], is_ocr=True, token=mineru_token)
                    md_dir = os.path.join(work_dir, "markdown")
                    ocr_md_files = download_markdowns(results, md_dir)
                    md_files.extend(ocr_md_files)
                    task.set_stage("ocr_converting", 40, f"识别完成，共 {len(ocr_md_files)} 个文件")
                except Exception as e:
                    task.set_stage("ocr_converting", 40, "云端识别未成功，已改用本地文字提取")
                    md_dir = os.path.join(work_dir, "markdown")
                    os.makedirs(md_dir, exist_ok=True)
                    fallback_md = _fallback_pdf_to_md(pdf_units, md_dir)
                    md_files.extend(fallback_md)
                    task.messages.append(f"回退模式：使用PDF直接提取文本，{len(fallback_md)} 个文件")
        else:
            task.set_stage("ocr_converting", 40, "无 PDF 文件，跳过文字识别")

        if not md_files:
            task.set_error("文本提取失败")
            return

        # ── Stage 2.5: 片段级结构路由 ──
        # 每个片段独立判定结构质量：
        #   · 结构可还原 → regex 建树（快、忠实、不花钱）
        #   · 结构不可还原 → AI 出骨架 + 原文锚定（保真，不改写正文）
        # 该路由与文件类型无关，因此取代了原先 `split_mode in ('text',)` 的过滤
        # （那个过滤把 Word/PDF 挡在 AI 路径之外，且无标题文档会被 regex 静默丢弃）。
        unit_meta_by_stem = {Path(unit["path"]).stem: unit for unit in all_units}
        regex_md_files, skeleton_units = [], []
        structure_judgements = []          # 供结果面板展示：每片段走哪条路径
        for md_path in md_files:
            try:
                with open(md_path, encoding="utf-8") as _f:
                    text = _f.read()
            except Exception:
                regex_md_files.append(md_path)
                continue
            judged = score_fragment(text)
            structure_judgements.append(judged)
            unit = unit_meta_by_stem.get(Path(md_path).stem, {}) or {}
            unit["structure_quality"] = judged
            if judged["route"] == "skeleton":
                skeleton_units.append({**unit, "path": md_path, "text": text})
            else:
                regex_md_files.append(md_path)
        task.structure_judgements = structure_judgements

        n_sk = len(skeleton_units)
        if n_sk:
            task.messages.append(
                f"结构判断：{len(regex_md_files)} 个片段走解析建树，"
                f"{n_sk} 个片段结构不可还原，交 AI 出骨架")
        elif regex_md_files:
            task.messages.append(f"结构判断：{len(regex_md_files)} 个片段结构可还原，直接解析建树")

        llm_trees = []
        if skeleton_units:
            if not chat:
                task.messages.append("未配置模型服务（设置页可配置），"
                                     "结构不良片段改为按段落保留原文")
            task.set_stage("building_tree", 42,
                           f"正在为 {n_sk} 个结构不良片段生成骨架…")
            try:
                llm_trees = asyncio.run(_skeleton_units_to_trees(skeleton_units, task, chat))
            except Exception as e:
                task.messages.append(f"WARNING: 骨架生成失败 ({e})，按段落保留原文")
                llm_trees = [_fallback_unit_tree(u) for u in skeleton_units]

        # ── Stage 3: Build trees ──
        task.set_stage("building_tree", 45, "正在构建知识树...")
        # All units (PDF + structured text) for metadata
        unit_meta_by_stem = {Path(unit["path"]).stem: unit for unit in all_units}
        tree_files = _build_trees_from_md(regex_md_files, work_dir, unit_meta_by_stem,
                                          api_key=(chat or {}).get("api_key", ""))

        # 汇总各文件的骨架报告（解析前置）：恢复了几处标题、剔了几处页眉、
        # 有没有自动分隔的缺失边界。最终随解析报告给数据面板。
        try:
            from outline_skeleton import merge_reports
            skel_reports = [tf.get("skeleton") for tf in tree_files if tf.get("skeleton")]
            if skel_reports:
                task.skeleton_meta = merge_reports(skel_reports)
                meta = task.skeleton_meta
                n_rec = len(meta.get("recovered", []))
                n_drop = len(meta.get("dropped_headers", []))
                n_ph = len(meta.get("placeholders", []))
                if n_rec or n_drop or n_ph:
                    task.messages.append(
                        f"章节边界恢复：补回 {n_rec} 个标题，剔除 {n_drop} 处重复页眉"
                        + (f"，自动分隔 {n_ph} 处未识别小节" if n_ph else ""))
        except Exception as e:
            print(f"  Skeleton report merge skipped: {e}")

        # Add AI-skeleton trees (from structurally-broken fragments)
        for i, tree in enumerate(llm_trees):
            if tree:
                src = skeleton_units[i]
                tree_files.append({
                    "filename": f"ai_skeleton_{i}",
                    "tree": tree,
                    "source_order": src.get("source_order", 0),
                    "source_title": src.get("source_title", ""),
                    "unit_order": src.get("unit_order", 0),
                })

        task.set_stage("building_tree", 60, "知识树构建完成")

        # ── Stage 4: Fix hierarchy ──
        task.set_stage("building_tree", 65, "正在整理章节层级…")
        merged_tree = _fix_hierarchy_and_merge(tree_files, work_dir)
        # MinerU 图片引用只进 node["images"]，不进入正文，也不喂给 AI 增强。
        from tree_builder import sanitize_tree_images
        merged_tree = sanitize_tree_images(merged_tree)
        # 先给确定性标题：即使 AI 不可用，资料库也不会显示 task_<id> 或 Part_N。
        from document_title import apply_document_title, fallback_document_title
        task.title = fallback_document_title(task.source_names, merged_tree)
        apply_document_title(merged_tree, task.title)
        task.set_stage("building_tree", 75, "章节层级整理完成")

        # ── Stage 5: AI Enhancement ──
        if not chat:
            task.set_stage("ai_enhancing", 95, "未配置 AI 服务，跳过内容解读")
            from node_enhancer import cleanup_tree_structure
            merged_tree = cleanup_tree_structure(merged_tree)
            enhanced_tree = merged_tree
        else:
            task.set_stage("ai_enhancing", 80, "正在为知识点生成摘要与考点…")
            try:
                enhanced_tree = asyncio.run(_enhance_tree(merged_tree, task, chat))
                task.set_stage("ai_enhancing", 95, "内容解读完成")
            except Exception as e:
                task.set_stage("ai_enhancing", 95, "内容解读未完成，保留原始知识树")
                from node_enhancer import cleanup_tree_structure
                merged_tree = cleanup_tree_structure(merged_tree)
                enhanced_tree = merged_tree

        # ── Stage 6: Done ──
        task.set_stage("merging", 98, "正在准备最终数据…")
        final_result = _prepare_final_json(enhanced_tree)
        export_json_dir = os.path.join(work_dir, "json")
        os.makedirs(export_json_dir, exist_ok=True)
        with open(os.path.join(export_json_dir, "knowledge_tree.json"), "w", encoding="utf-8") as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)
        task.set_stage("complete", 100, "处理完成！")
        task.report = _compose_report(
            task, final_result, uploaded_files,
            {"pdf": len(pdf_files), "word": len(word_files), "text": len(txt_files)},
            pdf_pages, started)
        _save_report(work_dir, task.report)
        _store_cache(task.cache_key, work_dir)
        task.messages.append(parse_report.summarize_for_message(task.report))
        task.set_done(final_result)

    except Exception as e:
        task.set_error(f"{str(e)}\n{traceback.format_exc()}")


# ────────────────────────────────────────────
# 解析报告（上传页数据面板的数据源）
# ────────────────────────────────────────────

def _count_pdf_pages(uploaded_pdfs: list[dict]) -> int:
    """上传 PDF 的总页数。任何失败都只少算页数，不影响流程。"""
    try:
        import fitz
    except Exception:
        return 0
    total = 0
    for f in uploaded_pdfs:
        try:
            doc = fitz.open(f["path"])
            total += doc.page_count
            doc.close()
        except Exception:
            continue
    return total


def _compose_report(task: TaskProgress, tree: dict, uploaded_files: list[dict],
                    type_counts: dict, pdf_pages: int, started: float) -> dict:
    """活任务才有完整信息（token / 页数 / 耗时），一次性组装成报告。"""
    return parse_report.build_report(
        tree,
        task_id=task.task_id,
        files={
            "count": len(uploaded_files),
            "names": [f.get("original_name", "") for f in uploaded_files][:20],
            "pages": pdf_pages,
            "pdf": type_counts.get("pdf", 0),
            "word": type_counts.get("word", 0),
            "text": type_counts.get("text", 0),
        },
        tokens=task.token_usage.to_dict() if task.token_usage else None,
        structure=parse_report.structure_summary(getattr(task, "structure_judgements", None) or []),
        ai=task.ai_meta or None,
        audit=task.audit_meta or None,
        skeleton=getattr(task, "skeleton_meta", None) or None,
        duration_sec=time.time() - started,
        generated_at=time.time(),
    )


def _save_report(work_dir: str, report: dict) -> None:
    """落盘，让报告在任务从内存消失后（重启 / 老任务）仍可查。失败不影响流程。"""
    try:
        with open(os.path.join(work_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[报告] 落盘失败（不影响流程）: {e}")


def load_report(task_id: str) -> dict | None:
    """取解析报告：内存活任务 → 磁盘 → 从树现算的降级版。

    降级版只有节点统计（token / 页数这类信息只有活任务记得住），
    但足以让老任务也能打开数据面板，而不是看到一片空白或报错。
    """
    with tasks_lock:
        task = tasks.get(task_id)
    if task is not None and task.report:
        return task.report

    path = os.path.join(TEMP_DIR, task_id, "report.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    tree_path = os.path.join(TEMP_DIR, task_id, "json", "knowledge_tree.json")
    if os.path.isfile(tree_path):
        try:
            with open(tree_path, encoding="utf-8") as f:
                tree = json.load(f)
            report = parse_report.build_report(tree, task_id=task_id)
            report["partial"] = True      # 前端据此提示"仅有节点统计"
            return report
        except Exception:
            pass
    return None


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


def _fallback_unit_tree(unit: dict) -> dict:
    """无骨架时的保底：按段落分块，原文一字不丢。

    任何情况下都不允许"无标题文档 → 空树、正文全丢"。
    """
    from skeleton_builder import fragment_to_tree
    root, rep = fragment_to_tree(unit.get("text") or "", None,
                                unit.get("source_title") or "未命名")
    return {"title": unit.get("unit_title") or unit.get("source_title") or "未命名",
            "children": root.get("children") or []}


async def _skeleton_units_to_trees(units: list, task: TaskProgress,
                                   chat: dict | None) -> list:
    """对结构不可还原的片段：AI 只出骨架，原文由程序按区间锚定。

    与旧 _llm_structure_chunks 的区别：模型不写 content（不改写正文），
    因此不会有"改写丢字 / JSON 截断后只剩 10 段"的问题。无 API key 时
    自动降级为按段落保留原文，绝不丢字。
    """
    import httpx
    from skeleton_builder import fragment_to_tree, generate_skeleton
    from content_anchor import split_paragraphs

    trees = []
    api_key = (chat or {}).get("api_key", "")
    base_url = (chat or {}).get("base_url", "")
    model = (chat or {}).get("model", "")

    async with httpx.AsyncClient(timeout=60) as client:
        for i, unit in enumerate(units):
            text = unit.get("text") or ""
            paras = split_paragraphs(text)
            skeleton = None
            if api_key and base_url and paras:
                try:
                    skeleton = await generate_skeleton(
                        client, paras, base_url, api_key, model,
                        doc_hint=unit.get("source_title") or "")
                except Exception as e:
                    task.messages.append(f"骨架生成跳过 ({e})")
            root, rep = fragment_to_tree(text, skeleton,
                                        unit.get("source_title") or "未命名")
            task.messages.append(
                f"片段[{i + 1}/{len(units)}]："
                f"{'AI骨架' if skeleton else '段落保底'}"
                f"，{rep.get('paragraphs', 0)} 段原文保留"
                + (f"，丢 {rep.get('lost', 0)} 段" if rep.get('lost') else ""))
            unit_tree = {"title": unit.get("unit_title")
                         or unit.get("source_title") or "未命名",
                         "children": root.get("children") or []}
            try:
                from placement_audit import fix_topic_placement
                pr = fix_topic_placement(unit_tree["children"])
                for mv in pr.get("moved", []):
                    task.messages.append(f"归属修正：「{mv['title']}」 → 「{mv['to']}」")
            except Exception as e:
                print(f"  Placement audit skipped (skeleton): {e}")
            trees.append(unit_tree)
    return trees


async def _llm_structure_chunks(units: list[dict], task: TaskProgress, chat: dict) -> list[dict]:
    """【已废弃，主管线不再调用】旧的"一步到位"式 AI 结构化。

    由 _skeleton_units_to_trees 取代：后者只让模型出骨架、原文由程序锚定，
    因此不会有改写丢字 / JSON 截断丢字。保留仅供对照与脚本使用。
    """
    """Use LLM to create tree structure from unstructured text chunks.

    chat: 统一配置解析出的模型服务 {base_url, model, api_key, ...}
    """
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
            chunk_trees = await structure_all_chunks(
                chunk_list, api_key=chat["api_key"],
                base_url=chat["base_url"], model=chat["model"])
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
    from outline_skeleton import (normalize_markdown_outline, audit_tree_skeleton,
                                  check_skeleton, merge_reports)

    tree_dir = os.path.join(work_dir, "trees")
    os.makedirs(tree_dir, exist_ok=True)
    tree_files = []
    unit_meta_by_stem = unit_meta_by_stem or {}

    for md_path in sorted(md_files):
        with open(md_path, encoding="utf-8") as f:
            md_text = f.read().strip()

        filename = os.path.splitext(os.path.basename(md_path))[0]
        unit_meta = unit_meta_by_stem.get(filename, {})

        # ── 结构前置（解析入口）：先立骨架，再挂内容 ──
        # 恢复丢失的大纲标题（纯文本"第X节/知识点X"按序列连续性提升）、
        # 剔除重复页眉、补全章名。对结构完好的文档零改动。
        skeleton = None
        try:
            md_text, skeleton = normalize_markdown_outline(md_text, doc_hint=filename)
            for r in skeleton.get("recovered", []):
                print(f"  骨架恢复： 「{r['title']}」 (line {r['line']})")
            for d in skeleton.get("dropped_headers", []):
                print(f"  页眉剔除： 「{d['text']}」 ×{d['count']}")
        except Exception as e:
            print(f"  Outline normalization skipped for {filename}: {e}")

        nodes = parse_md_to_nodes(md_text)
        nodes = adjust_standalone_levels(nodes)
        nodes = apply_hierarchy_repair(nodes)
        tree_children = build_tree(nodes)

        # ── 结构前置（建树后）：序列校验 + 缺失边界占位修复 ──
        try:
            audit = audit_tree_skeleton(tree_children)
            skeleton = merge_reports([skeleton, audit])
            for ph in audit.get("placeholders", []):
                print(f"  边界占位： 「{ph['parent']}」 下从「{ph['at']}」处自动分隔")
        except Exception as e:
            print(f"  Skeleton audit skipped for {filename}: {e}")

        # Validate hierarchy with LLM if API key is available
        if api_key:
            try:
                from tree_builder import validate_and_repair_hierarchy
                tree_children = validate_and_repair_hierarchy(tree_children, api_key)
            except Exception as e:
                print(f"  Hierarchy validation skipped for {filename}: {e}")

        # LLM 校验/重建之后再核一遍骨架（该步曾把整节弹成顶层根节点）
        if skeleton is not None:
            try:
                post = check_skeleton(tree_children)
                skeleton["violations_after"] = len(post)
                skeleton["errors_after"] = sum(
                    1 for v in post if v.get("severity") == "error")
                for v in post:
                    if v.get("severity") == "warn":
                        skeleton["warnings"].append(v)
            except Exception:
                pass

        # 主题归属审校（确定性）：把「挂在别的主题下」的内容移回它自己的主题
        # （例：韩愈章节末尾的攻略框里，颜之推的条目 → 移回颜之推知识点）
        placement = {"moved": [], "skipped": []}
        try:
            from placement_audit import fix_topic_placement
            placement = fix_topic_placement(tree_children)
            for mv in placement.get("moved", []):
                print(f"  归属修正： 「{mv['title']}」  {mv['from']}  →  「{mv['to']}」")
        except Exception as e:
            print(f"  Placement audit skipped for {filename}: {e}")

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
            "placement_moved": placement.get("moved", []),
            "skeleton": skeleton or {},
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

    return {"title": "AnythingMindmap", "children": root_children}


async def _enhance_tree(tree: dict, task: TaskProgress, chat: dict) -> dict:
    """Enhance tree nodes with AI summaries, keywords, and exam points.

    chat: 统一配置解析出的模型服务 {base_url, model, api_key, ...}。

    P2 hardening: per-node retry + failure classification + deterministic
    fallback (node.fallback=True), so the pipeline never dead-ends and a
    re-run only retries failed nodes (they have no usable summary yet)."""
    import app_settings
    from node_enhancer import (
        detect_document_profile, cleanup_tree_structure, enhance_book_nodes,
        tree_fingerprint,
    )

    api_key = chat["api_key"]
    # AI 阶段的 token 用量（结构审校 + 节点增强 + 导学），最终随解析报告返回。
    # 只统计本阶段：OCR、无结构文本的 LLM 结构化、建树期的层级校验都在进入这里之前完成。
    usage = parse_report.UsageMeter()
    task.token_usage = usage
    API_TIMEOUT = 20
    # 增强范围档位（设置页可切换）：快速=最前 30 个 / 标准=L1–L3 全量 / 全量=L1–L4 全量
    enh_cfg = app_settings.enhance_config()
    ENHANCE_LEVELS_ALLOWED = set(enh_cfg["levels"])
    MAX_ENHANCE_NODES = int(enh_cfg["max_nodes"] or 0)

    document_profile = detect_document_profile(tree)
    task.messages.append(f"检测资料类型: {document_profile['label']}（考点按「{app_settings.subject_config()['exam']}」标准）")
    task.messages.append(f"模型服务: {chat['provider_name']} / {chat['model']}")

    # 增强范围（档位化）：让"覆盖率"在开跑前就有交代
    _lv = sorted(ENHANCE_LEVELS_ALLOWED)
    _levels_str = f"L{_lv[0]}–L{_lv[-1]}" if _lv else "-"
    _cap_str = f"最多 {MAX_ENHANCE_NODES} 个节点" if MAX_ENHANCE_NODES else "不设节点上限"
    task.messages.append(f"增强范围：{enh_cfg['label']}（{_levels_str}，{_cap_str}）")

    # Quick API health check
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        try:
            test_payload = {
                "model": chat["model"],
                "max_tokens": 20,
                "temperature": 0,
                "messages": [{"role": "user", "content": "回复OK"}],
                **app_settings.chat_body_extras(chat["base_url"]),
            }
            resp = await client.post(
                chat["base_url"],
                json=test_payload,
                headers=app_settings.chat_headers(chat["base_url"], api_key)
            )
            if resp.status_code != 200:
                task.set_stage("ai_enhancing", 81, f"AI API不可用 (HTTP {resp.status_code}), 跳过增强")
                return tree
        except Exception as e:
            task.set_stage("ai_enhancing", 81, f"AI API不可用 ({e}), 跳过增强")
            return tree

    task.set_stage("ai_enhancing", 80, "正在为知识点生成内容解读…")

    # ── 结构前置（增强入口）──
    # 合并/清理阶段可能再次破坏边界；在结构审校之前，用解析阶段同一套
    # 不变式再核一遍：能确定性分隔的补占位，其余记报告，绝不带着坏骨架增强。
    try:
        from outline_skeleton import audit_tree_skeleton, merge_reports
        skel_env = audit_tree_skeleton(tree.get("children") or [])
        task.skeleton_meta = merge_reports([task.skeleton_meta, skel_env])
        if skel_env.get("placeholders"):
            task.messages.append(
                f"章节边界校验：自动分隔 {len(skel_env['placeholders'])} 处未识别小节")
        errs = skel_env.get("errors_before", 0)
        if errs:
            print(f"[骨架] 增强前置发现 {errs} 处错误级违规，已修复 "
                  f"{errs - skel_env.get('errors_after', 0)} 处")
    except Exception as e:
        print(f"  Enhance-front skeleton audit skipped: {e}")

    # ── 结构审校：在增强之前把顺序/标题问题解决掉 ──
    # 已固化进增强环节，不再对外提供开关（原设置页的「结构审校」卡片已移除）。
    # 顺序：确定性修正（有编号即可判定，全部修掉）→ LLM 兜底编号重复/断档、
    #       改写"坏标题"。审校只重排、只改坏标题，绝不增删节点。
    # 放在增强之前，是为了让后续的摘要/关键词贴着正确结构生成；
    # 同时审校后 src_index 已重排，最后一轮 cleanup_tree_structure 不会把结论冲掉。
    try:
        import structure_repair
        task.set_stage("ai_enhancing", 81, "正在校对章节顺序与标题…")

        def on_audit_progress(done, total):
            task.set_stage("ai_enhancing", 81,
                           f"正在校对结构：{done}/{total}")

        audit_report = await structure_repair.audit_book_tree(
            tree,
            url=chat["base_url"], api_key=api_key, model=chat["model"],
            on_progress=on_audit_progress, usage=usage,
        )
        task.audit_meta = audit_report
        fixed = audit_report.get("deterministic_groups", 0)
        llm_groups = audit_report.get("llm_groups", 0)
        bad_before = audit_report.get("bad_titles_before")
        bad_after = audit_report.get("bad_titles_after")
        if fixed or llm_groups:
            task.messages.append(f"已修正 {fixed} 处章节顺序"
                                 + (f"，其中 {llm_groups} 处由 AI 判断" if llm_groups else ""))
        if isinstance(bad_before, int) and isinstance(bad_after, int) and bad_before:
            task.messages.append(f"已补全残缺标题，尚有 {bad_after} 个待补")
    except Exception as e:
        task.messages.append("结构校对未完成，按解析结果继续")

    # 资料级标题：在结构校对后用一次短请求润色；失败保留解析阶段的兜底标题。
    try:
        from document_title import apply_document_title, generate_document_title
        generated_title = await generate_document_title(
            tree, task.source_names or [task.filename], chat, usage=usage)
        if generated_title:
            task.title = generated_title
            apply_document_title(tree, generated_title)
            task.messages.append(f"资料标题：{generated_title}")
            task.persist_state()
    except Exception as e:
        task.messages.append(f"资料标题生成跳过（{e}）")

    # P2 断点续跑：同一份资料（树指纹相同）重跑时，已成功节点直接跳过。
    # 必须在结构审校之后取指纹——审校可能改了标题，指纹要归属修好后的那棵树。
    progress_dir = os.path.join(TEMP_DIR, "_enhance_progress")
    os.makedirs(progress_dir, exist_ok=True)
    progress_path = os.path.join(progress_dir, tree_fingerprint(tree) + ".json")

    enhance_stats = {}

    def on_progress(stats, total):
        enhance_stats.update(stats)
        enhance_stats["total"] = total
        base_pct, max_pct = 82, 94
        pct = base_pct + int((stats["done"] / total) * (max_pct - base_pct))
        task.set_stage("ai_enhancing", pct,
                       f"正在生成解读：{stats['done']}/{total}")

    tree = await enhance_book_nodes(
        tree, api_key,
        allowed_levels=ENHANCE_LEVELS_ALLOWED,
        max_nodes=MAX_ENHANCE_NODES,
        concurrency=MAX_CONCURRENT,
        request_delay=REQUEST_DELAY,
        on_progress=on_progress,
        document_profile=document_profile,
        progress_path=progress_path,
        base_url=chat["base_url"],
        model=chat["model"],
        subject_config=app_settings.subject_config(),
        usage=usage,
    )
    # 数据面板要的增强口径：目标多少个节点、成功多少、兜底多少
    task.ai_meta = {
        "provider": chat.get("provider_name", ""),
        "model": chat.get("model", ""),
        "scope": enh_cfg["scope"],
        "scope_label": enh_cfg["label"],
        "allowed_levels": sorted(ENHANCE_LEVELS_ALLOWED),
        "max_nodes": MAX_ENHANCE_NODES,
        "targeted": enhance_stats.get("total", 0),
        "ok": enhance_stats.get("ok", 0),
        "fallback": enhance_stats.get("fallback", 0),
    }

    # P3: guide + prerequisites for top-level modules & book root.
    # Every failure degrades to the frontend's static fallback copy — never blocks.
    from node_enhancer import generate_guides
    task.set_stage("ai_enhancing", 95, "正在生成章节导学…")
    def on_guide_progress(done, total):
        task.set_stage("ai_enhancing", 95,
                       f"导学生成中: {done}/{total} 章")
    try:
        tree = await generate_guides(
            tree, api_key,
            concurrency=MAX_CONCURRENT,
            base_url=chat["base_url"],
            model=chat["model"],
            on_progress=on_guide_progress,
            usage=usage,
        )
        guide_count = sum(1 for ch in tree.get("children", [])
                          if (ch.get("guide") or "").strip() and not ch.get("_guide_fallback"))
        task.messages.append(f"导学已生成：{guide_count}/{len(tree.get('children', []))} 章")
    except Exception as e:
        task.messages.append(f"导学生成跳过 ({e}) — 使用静态导学")

    task.set_stage("ai_enhancing", 96, "正在整理结构…")
    tree = cleanup_tree_structure(tree)
    task.set_stage("ai_enhancing", 97, "整理完成")
    # 审校用的内部字段到此不再需要：_prepare_final_json 只跑在"成功写入"那条
    # 路径上，而 _enhance_tree 返回的树会被 task.result 直接交给前端，
    # 所以这里也要剥一次，避免 seq_index 之类泄漏到 /api/result。
    _strip_all_internal(tree)
    return tree


def _prepare_final_json(tree: dict) -> dict:
    """Add depth/level info and clean up the tree for frontend."""
    from node_enhancer import cleanup_tree_structure
    from tree_builder import sanitize_tree_images

    # 最终防线：旧缓存/旧任务/手工编辑都可能残留 images/ 路径。
    tree = sanitize_tree_images(tree)
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
        if "guide" not in node:
            node["guide"] = ""
        if "prerequisites" not in node:
            node["prerequisites"] = []
        for child in node.get("children", []):
            _walk(child, depth + 1)

    for ch in tree.get("children", []):
        _walk(ch, 1)

    _strip_all_internal(tree)

    return tree


def _strip_all_internal(node: dict) -> None:
    """整树剥离内部字段（含根节点自身）。"""
    for key in ("src_index", "seq_index", "src_hash", "_audit_hint"):
        node.pop(key, None)
    for child in node.get("children", []):
        _strip_all_internal(child)


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
    task.persist_state()
    uploaded_files = []

    for index, upload in enumerate(files, start=1):
        content = await upload.read()
        filename = f"{index:02d}_{sanitize_filename(upload.filename)}"
        file_path = os.path.join(uploads_dir, filename)
        with open(file_path, "wb") as f:
            f.write(content)
        uploaded_files.append({
            "path": file_path,
            "original_name": upload.filename,
            "sha256": hashlib.sha256(content).hexdigest(),
        })
        ft = detect_file_type(upload.filename)
        type_label = {"pdf": "PDF", "word": "Word", "text": "TXT"}.get(ft, ft)
        task.messages.append(f"文件已上传: [{type_label}] {upload.filename} ({len(content) / 1024 / 1024:.1f} MB)")

    task.source_names = [item["original_name"] for item in uploaded_files]
    task.persist_state()

    import app_settings
    chat_for_cache = app_settings.resolve_chat(api_key=task.deepseek_api_key or "")
    mineru_for_cache = task.mineru_token or app_settings.mineru_token() or None
    task.cache_key = _build_cache_key(uploaded_files, chat_for_cache, mineru_for_cache)
    task.persist_state()

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
        state = load_task_state(task_id, recover_interrupted=True)
        if state is not None:
            return JSONResponse(state)
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


@app.get("/api/report/{task_id}")
async def api_report(task_id: str):
    """解析报告 —— 上传页数据面板的数据源。

    活任务给全量（含 token 用量、文件与页数、耗时）；任务已从内存消失时
    退回磁盘上的 report.json；再没有就用知识树现算一份只有节点统计的
    降级版（partial=True），保证老任务也能打开面板而不是看到报错。
    """
    report = load_report(task_id)
    if report is None:
        return JSONResponse({"error": "报告不存在（任务未完成或已清理）"}, status_code=404)
    return JSONResponse(report)


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
        # 离线页面是 tree_mindmap.html 的副本，必须带上它依赖的 theme.css / common.js，
        # 否则解压后打开会缺样式、缺公共库（表现为 KT.xxx is not a function）。
        for asset in ("cds-theme.css", "theme.css", "common.js"):
            asset_path = os.path.join(UI_DIR, asset)
            if os.path.isfile(asset_path):
                zf.write(asset_path, asset)
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
        "<title>AnythingMindmap - Mind Map</title>",
        "<title>AnythingMindmap - Offline Mind Map</title>",
    )
    html = html.replace("正在加载知识树…", "正在加载 json/knowledge_tree.json …")
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
    # 离线包没有后端：去掉对话复习组件，隐藏依赖服务端的入口（总览 / 设置），
    # 并去掉静态资源的 ?v= 查询串（file:// 下没必要，也避免个别浏览器解析差异）。
    # 版本号用正则剥掉：?v= 会随公共库改动递增，写死会漏替换导致离线包缺资源。
    html = re.sub(r'<script src="review_widget\.js(\?v=\d+)?"\s*></script>', "", html)
    html = html.replace('<a class="tb-link" id="btnOverview"', '<a class="tb-link" id="btnOverview" style="display:none"')
    html = html.replace('<a href="/ui/settings.html" class="theme-toggle"', '<a href="#" class="theme-toggle" style="display:none"')
    html = re.sub(r'(common\.js|theme\.css)\?v=\d+', r'\1', html)
    html = html.replace(
        "Make sure you are serving this file via HTTP (e.g. <code>python -m http.server</code>), not opening directly as file://",
        "Make sure <code>mindmap.html</code> is next to the <code>chapters/</code>, <code>md/</code>, and <code>json/</code> folders after unzip, and serve the folder via HTTP (e.g. <code>python -m http.server</code>)",
    )
    return html

@app.post("/api/update-node")
async def api_update_node(data: dict):
    """Update a node's title/content in the task tree or legacy tree file."""
    filename = data.get("filename", "")
    path_in_file = data.get("path", [])
    new_title = data.get("title", "").strip()
    new_content = data.get("content")  # optional, None means don't update

    if not filename or not new_title:
        return JSONResponse({"error": "filename and title are required"}, status_code=400)
    if not isinstance(path_in_file, list) or not all(isinstance(i, int) for i in path_in_file):
        return JSONResponse({"error": "Invalid node path"}, status_code=400)

    # Sanitize filename to prevent path traversal
    safe_filename = os.path.basename(filename)
    task_id = None
    filepath = ""

    # New task trees: filename is task_<task_id>.
    if safe_filename.startswith("task_"):
        candidate = safe_filename[5:]
        if re.fullmatch(r"[0-9A-Za-z_-]{1,64}", candidate or ""):
            candidate_path = os.path.join(TEMP_DIR, candidate, "json", "knowledge_tree.json")
            if os.path.isfile(candidate_path):
                task_id = candidate
                filepath = candidate_path

    # Legacy enhanced trees: keep old behavior as a fallback.
    if not filepath:
        legacy_path = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed", safe_filename)
        if os.path.isfile(legacy_path):
            filepath = legacy_path

    if not filepath:
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
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except IOError as e:
        return JSONResponse({"error": f"Failed to write file: {str(e)}"}, status_code=500)

    # Keep the live task result in sync so a page reload sees the edit immediately.
    cache_key = ""
    if task_id:
        with tasks_lock:
            task = tasks.get(task_id)
        if task is not None and task.status == "done":
            task.result = tree
            cache_key = getattr(task, "cache_key", "")
        if not cache_key:
            state = load_task_state(task_id)
            cache_key = (state or {}).get("cache_key", "")
        if cache_key:
            _update_cached_tree(cache_key, tree)

    return {"status": "ok", "title": new_title, "content_updated": new_content is not None}


@app.post("/api/rename")
async def api_rename(data: dict):
    """重命名一份资料：任务树（task_id）或旧本地树（filename）。"""
    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
    title = re.sub(r"[\x00-\x1f\x7f]+", " ", title).strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    if len(title) > 60:
        return JSONResponse({"error": "title too long (max 60)"}, status_code=400)

    task_id = str(data.get("task_id") or "").strip()
    filename = str(data.get("filename") or "").strip()
    from document_title import apply_document_title

    if task_id:
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", task_id):
            return JSONResponse({"error": "Invalid task_id"}, status_code=400)
        work_dir = os.path.join(TEMP_DIR, task_id)
        json_path = os.path.join(work_dir, "json", "knowledge_tree.json")
        if not os.path.isfile(json_path):
            return JSONResponse({"error": "任务尚未生成知识树"}, status_code=409)
        try:
            with open(json_path, encoding="utf-8") as f:
                tree = json.load(f)
            if not isinstance(tree, dict):
                raise ValueError("unsupported tree format")
            apply_document_title(tree, title)
            tmp_path = json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(tree, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, json_path)
        except Exception as e:
            return JSONResponse({"error": f"Failed to rename: {e}"}, status_code=500)

        state = load_task_state(task_id)
        if state is not None:
            state["title"] = title
            state["updated_at"] = time.time()
            try:
                state_path = os.path.join(work_dir, "task_state.json")
                tmp_state = state_path + ".tmp"
                with open(tmp_state, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
                os.replace(tmp_state, state_path)
            except Exception:
                pass

        cache_key = ""
        with tasks_lock:
            live = tasks.get(task_id)
        if live is not None:
            live.title = title
            live.result = tree
            cache_key = getattr(live, "cache_key", "") or (state or {}).get("cache_key", "")
            live.persist_state()
        else:
            cache_key = (state or {}).get("cache_key", "")
        if cache_key:
            _update_cached_tree(cache_key, tree)
        return {"status": "ok", "title": title, "task_id": task_id}

    if filename:
        safe_filename = os.path.basename(filename)
        legacy_path = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed", safe_filename)
        if not os.path.isfile(legacy_path):
            return JSONResponse({"error": f"File not found: {safe_filename}"}, status_code=404)
        try:
            with open(legacy_path, encoding="utf-8") as f:
                tree = json.load(f)
            if not isinstance(tree, dict):
                raise ValueError("unsupported tree format")
            apply_document_title(tree, title)
            tmp_path = legacy_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(tree, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, legacy_path)
        except Exception as e:
            return JSONResponse({"error": f"Failed to rename: {e}"}, status_code=500)
        return {"status": "ok", "title": title, "filename": safe_filename}

    return JSONResponse({"error": "task_id or filename is required"}, status_code=400)


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


def _read_tree_title(path: str) -> str:
    """读取树根 title；损坏/非 dict 时返回空串。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return (data.get("title") or "").strip()
    except Exception:
        pass
    return ""


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
                tree_data = None
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        tree_data = json.load(fh)
                    node_count = _count_nodes(tree_data)
                except Exception:
                    pass

                from document_title import clean_source_title, fallback_document_title
                original_files = []
                if isinstance(tree_data, dict):
                    raw_sources = tree_data.get("original_files") or tree_data.get("source_files") or []
                    if isinstance(raw_sources, str):
                        raw_sources = [raw_sources]
                    original_files = [str(x) for x in raw_sources if str(x).strip()]
                title = _read_tree_title(fpath) or clean_source_title(f)
                if not title or title in ("Knowledge Tree", "AnythingMindmap"):
                    title = fallback_document_title(original_files, tree_data or {})
                files.append({
                    "filename": f,
                    "title": title,
                    "source": "enhanced",
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "original_files": original_files,
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
                tree_data = None
                try:
                    with open(json_path, encoding="utf-8") as fh:
                        tree_data = json.load(fh)
                    node_count = _count_nodes(tree_data)
                except Exception:
                    pass

                from document_title import fallback_document_title
                title = ""
                state = load_task_state(task_dir)
                if state:
                    title = (state.get("title") or "").strip()
                if not title:
                    title = _read_tree_title(json_path)
                if not title or title in ("Knowledge Tree", "AnythingMindmap"):
                    title = fallback_document_title(original_files, tree_data or {})

                files.append({
                    "filename": f"task_{task_dir}",
                    "task_id": task_dir,
                    "title": title,
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
        # 跳过隐藏文件、非目录和内部目录（如 _enhance_progress）。
        if task_dir.startswith(".") or task_dir.startswith("_") or not os.path.isdir(work_dir):
            continue
        json_path = os.path.join(work_dir, "json", "knowledge_tree.json")
        status = "done" if os.path.isfile(json_path) else "incomplete"
        error = None

        # Find original filename from uploads（去掉 01_ 之类的序号前缀，与 /api/files 保持一致）
        uploads_dir = os.path.join(work_dir, "uploads")
        original_name = task_dir
        if os.path.isdir(uploads_dir):
            upload_files = sorted(f for f in os.listdir(uploads_dir) if not f.startswith("."))
            if upload_files:
                original_name = re.sub(r'^\d+_', '', upload_files[0])

        stat = os.stat(work_dir)
        modified_ts = stat.st_mtime
        with tasks_lock:
            live = tasks.get(task_dir)
        state = None
        if live is not None:
            status = live.status
            original_name = live.filename or original_name
            error = getattr(live, "error", None)
        else:
            state = load_task_state(task_dir, recover_interrupted=True)
            if state is not None:
                status = state.get("status") or status
                original_name = state.get("filename") or original_name
                modified_ts = float(state.get("updated_at") or modified_ts)
                error = state.get("error")

        from document_title import clean_source_title
        title = ""
        if live is not None:
            title = (getattr(live, "title", "") or "").strip()
        elif state is not None:
            title = (state.get("title") or "").strip()
        if not title:
            title = _read_tree_title(json_path)
        if not title or title in ("Knowledge Tree", "AnythingMindmap"):
            title = clean_source_title(original_name) or original_name or task_dir

        item = {
            "task_id": task_dir,
            "filename": original_name,
            "title": title,
            "status": status,
            "modified": datetime.fromtimestamp(modified_ts).isoformat(),
        }
        if error:
            item["error"] = error
        result.append(item)

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
# Review Assistant API (对话复习助手 · AI 自然对话陪练)
# 对学习者只呈现自然对话；判定与计数在 review_engine 内部静默执行（决定何时换题/复测）。
# ────────────────────────────────────────────

from functools import partial as _partial
from review_engine import (
    build_review_units, ReviewSession, ReviewStore, llm_judge, llm_say, new_session_id,
    build_learn_context, llm_learn, normalise_chat_prefs,
)
import app_settings as _app_settings

REVIEW_DIR = os.path.join(BASE_DIR, "data", "review")
_review_store = ReviewStore(REVIEW_DIR)
_review_sessions: dict = {}
_review_lock = threading.Lock()


def _review_stream(run):
    """把一次会话操作包成 NDJSON 流：逐字 delta → 最终 snapshot。

    事件：{"t":"d","text":…} 流式出字；{"t":"snap","data":…} 最终快照；{"t":"err","error":…}
    """
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        box: dict = {}

        def emit_delta(payload):
            # 普通增量传字符串；考点讲解等可传 {"text":…, "kind":"reveal"}，
            # 让浏览器从第一个 token 起就渲染正确的消息类型。
            if isinstance(payload, dict):
                item = {"t": "d"}
                item.update(payload)
            else:
                item = {"t": "d", "text": str(payload or "")}
            q.put_nowait(item)

        async def produce():
            try:
                box["snap"] = await run(emit_delta)
            except Exception as e:  # noqa: BLE001
                box["error"] = str(e)
            finally:
                q.put_nowait(None)

        task = asyncio.create_task(produce())
        while True:
            item = await q.get()
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"
        await task
        if "error" in box:
            yield json.dumps({"t": "err", "error": box["error"]}, ensure_ascii=False) + "\n"
        else:
            yield json.dumps({"t": "snap", "data": box["snap"]}, ensure_ascii=False) + "\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _load_tree_for_review(file_key: str):
    """与 /api/file 相同的查找逻辑：支持 task_<id> 或历史文件名。"""
    safe = os.path.basename((file_key or "").strip())
    if not safe:
        return None
    enhanced_dir = os.path.join(BASE_DIR, "data", "tree_parts_enhanced_fixed")
    fp = os.path.join(enhanced_dir, safe)
    if os.path.isfile(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    if os.path.isdir(TEMP_DIR):
        for task_dir in os.listdir(TEMP_DIR):
            if f"task_{task_dir}" == safe:
                jp = os.path.join(TEMP_DIR, task_dir, "json", "knowledge_tree.json")
                if os.path.isfile(jp):
                    try:
                        with open(jp, encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        return None
    return None


def _review_scope_from(payload: dict) -> dict:
    scope = {}
    if payload.get("high_freq"):
        scope["high_freq"] = True
    ch = (payload.get("chapter") or "").strip()
    if ch:
        scope["chapter"] = ch
    if payload.get("scope") == "retest":
        # 薄弱点 + 复测队列，按到期时间排序（已到期在前）——长期学习者模型的调度入口
        ids = _review_store.due_unit_ids(payload.get("file") or "")
        if not ids:
            return {"__empty_retest__": True}
        scope["unit_ids"] = ids
    if payload.get("unit_ids"):
        scope["unit_ids"] = payload["unit_ids"]
    return scope


@app.get("/api/review/units")
async def api_review_units(file: str, scope: str = "", chapter: str = "",
                           high_freq: bool = False, max_units: int = 300):
    """预览复习单元清单（不需要 API Key）。

    供"复习方向"选择列表使用：默认取足量单元（300，封顶 1000），
    保证章节分组（groups）覆盖全书；start 阶段仍按会话自己的上限取单元。
    """
    tree = _load_tree_for_review(file)
    if tree is None:
        return JSONResponse({"error": f"未找到资料: {file}"}, status_code=404)
    scope_dict = _review_scope_from(
        {"file": file, "scope": scope, "chapter": chapter, "high_freq": high_freq})
    prog = _review_store.load_progress(file)
    if scope_dict.get("__empty_retest__"):
        return {"units": [], "total_candidates": 0, "truncated": 0,
                "retest_count": 0, "weak_count": len(prog.get("weak_units", [])),
                "due_count": 0, "groups": [], "empty_retest": True}
    r = build_review_units(tree, scope=scope_dict,
                           max_units=max(1, min(1000, int(max_units or 300))))
    seen, groups = {}, []
    for u in r["units"]:
        g = u.get("group") or "未分组"
        if g not in seen:
            seen[g] = 0
            groups.append(g)
        seen[g] += 1

    # 长期学习者模型：到期判定（薄弱点 + 复测队列，含 next_due）
    entries = prog.get("units", {}) or {}
    now = time.time()
    due_ids, seen_due = set(), set()
    for src in ("weak_units", "retest_queue"):
        for x in prog.get(src, []):
            uid = x.get("unit_id")
            if not uid or uid in seen_due:
                continue
            seen_due.add(uid)
            e = entries.get(uid) or {}
            due = float(e.get("next_due") or x.get("due_at") or 0)
            if not due or due <= now:
                due_ids.add(uid)
    due_by_group = {}
    for u in r["units"]:
        if u["unit_id"] in due_ids:
            g = u.get("group") or "未分组"
            due_by_group[g] = due_by_group.get(g, 0) + 1

    return {
        "units": [{"unit_id": u["unit_id"], "title": u["title"], "group": u["group"],
                   "reference_count": len(u["reference_points"]),
                   "has_exam": u["has_exam"], "high_freq": u["high_freq"],
                   "due": u["unit_id"] in due_ids,
                   "passes": int((entries.get(u["unit_id"]) or {}).get("passes") or 0)} for u in r["units"]],
        "total_candidates": r["total_candidates"],
        "truncated": r["truncated"],
        "retest_count": len(prog.get("retest_queue", [])),
        "weak_count": len(prog.get("weak_units", [])),
        "due_count": len(due_ids),
        "groups": [{"name": g, "count": seen[g], "due": due_by_group.get(g, 0)} for g in groups],
    }


@app.post("/api/review/start")
async def api_review_start(data: dict):
    """开始一轮复习。对话话语由 LLM 生成；服务不可用时退回自然话术模板。"""
    file_key = (data.get("file") or "").strip()
    if not file_key:
        return JSONResponse({"error": "缺少 file 参数"}, status_code=400)
    tree = _load_tree_for_review(file_key)
    if tree is None:
        return JSONResponse({"error": f"未找到资料: {file_key}"}, status_code=404)

    scope_dict = _review_scope_from(data)
    if scope_dict.get("__empty_retest__"):
        return JSONResponse({"error": "待复测队列是空的，先完成一轮复习吧"}, status_code=400)
    r = build_review_units(tree, scope=scope_dict,
                           max_units=int(data.get("max_units") or 40))
    if not r["units"]:
        return JSONResponse({"error": "该范围内没有找到合适的复习单元"}, status_code=400)

    api_key = (data.get("api_key") or "").strip()
    chat = _app_settings.resolve_chat(api_key=api_key) or {}
    prefs = normalise_chat_prefs(data.get("prefs"))
    judge = _partial(llm_judge, api_key=chat.get("api_key", ""),
                     base_url=chat.get("base_url", ""), model=chat.get("model", ""),
                     preferences=prefs)
    sayer = _partial(llm_say, api_key=chat.get("api_key", ""),
                     base_url=chat.get("base_url", ""), model=chat.get("model", ""),
                     preferences=prefs)

    # 开场说明：这轮在复习什么、共几个考点（AI 直接出题时不再"凭空发问"）
    chapter = (data.get("chapter") or "").strip()
    if data.get("scope") == "retest":
        intro_label = f"待复练队列（{len(r['units'])} 个考点，按到期优先）"
    elif chapter:
        intro_label = f"{chapter}（{len(r['units'])} 个考点）"
    else:
        intro_label = f"全书混合（{len(r['units'])} 个考点）"
    if r.get("truncated"):
        intro_label += f"，另 {r['truncated']} 个暂缓到下一轮"

    prog = _review_store.load_progress(file_key)
    session = ReviewSession(
        new_session_id(), file_key, r["units"], judge=judge, sayer=sayer,
        tree_title=tree.get("title") or "", store=_review_store,
        session_no=len(prog.get("sessions", [])) + 1,
        intro_label=intro_label, prefs=prefs,
    )

    async def _run(on_delta):
        snap = await session.start(on_delta=on_delta)
        snap["truncated"] = r["truncated"]
        snap["degraded"] = not chat.get("api_key")
        snap["provider"] = chat.get("provider_name", "")
        return snap

    if data.get("stream"):
        with _review_lock:
            _review_sessions[session.session_id] = session
        return _review_stream(_run)
    with _review_lock:
        _review_sessions[session.session_id] = session
    return await _run(None)


def _get_review_session(session_id: str):
    """内存会话优先；不存在则尝试从磁盘续跑（进程重启后仍可继续）。"""
    if not session_id:
        return None
    with _review_lock:
        sess = _review_sessions.get(session_id)
    if sess:
        return sess
    state = _review_store.load_session(session_id)
    if not state:
        return None
    chat = _app_settings.resolve_chat() or {}
    prefs = normalise_chat_prefs(state.get("prefs"))
    judge = _partial(llm_judge, api_key=chat.get("api_key", ""),
                     base_url=chat.get("base_url", ""), model=chat.get("model", ""),
                     preferences=prefs)
    sayer = _partial(llm_say, api_key=chat.get("api_key", ""),
                     base_url=chat.get("base_url", ""), model=chat.get("model", ""),
                     preferences=prefs)
    sess = ReviewSession.from_state(state, judge=judge, sayer=sayer, store=_review_store)
    with _review_lock:
        _review_sessions[session_id] = sess
    return sess


@app.post("/api/review/answer")
async def api_review_answer(data: dict):
    sess = _get_review_session((data.get("session_id") or "").strip())
    if not sess:
        return JSONResponse({"error": "会话不存在，请重新开始"}, status_code=404)
    text = data.get("text") or ""
    if data.get("stream"):
        return _review_stream(lambda on_delta: sess.handle_answer(text, on_delta=on_delta))
    return await sess.handle_answer(text)


@app.post("/api/review/skip")
async def api_review_skip(data: dict):
    sess = _get_review_session((data.get("session_id") or "").strip())
    if not sess:
        return JSONResponse({"error": "会话不存在，请重新开始"}, status_code=404)
    if data.get("stream"):
        return _review_stream(lambda on_delta: sess.skip(on_delta=on_delta))
    return await sess.skip()


@app.post("/api/review/end")
async def api_review_end(data: dict):
    sess = _get_review_session((data.get("session_id") or "").strip())
    if not sess:
        return JSONResponse({"error": "会话不存在，请重新开始"}, status_code=404)
    if data.get("stream"):
        return _review_stream(lambda on_delta: sess.end_now(on_delta=on_delta))
    return await sess.end_now()


@app.post("/api/review/learn")
async def api_review_learn(data: dict):
    """学习模式：围绕当前资料/章节的自由问答。

    只依据知识树里的原文回答（检索定位 → 原文接地）；出处由服务端确定性
    附上（不用模型生成，防编造）。不判分、不记进度、不建会话。
    """
    file_key = (data.get("file") or "").strip()
    question = (data.get("question") or "").strip()
    if not file_key or not question:
        return JSONResponse({"error": "缺少 file 或 question"}, status_code=400)
    tree = _load_tree_for_review(file_key)
    if tree is None:
        return JSONResponse({"error": f"未找到资料: {file_key}"}, status_code=404)
    chapter = (data.get("chapter") or "").strip()
    history = data.get("history") if isinstance(data.get("history"), list) else []
    completion_mode = bool(data.get("completion_mode"))
    reply_style = (data.get("reply_style") or "auto").strip()
    thinking_effort = (data.get("thinking_effort") or "standard").strip()
    citation_style = (data.get("citation_style") or "auto").strip()
    language = (data.get("language") or "zh").strip()
    custom_instruction = str(data.get("custom_instruction") or "").strip()[:300]
    ctx = build_learn_context(tree, chapter=chapter, question=question)
    chat = _app_settings.resolve_chat() or {}

    async def _run(on_delta):
        if not chat.get("api_key"):
            return {"reply": "还没有可用的模型服务——学习模式需要 AI。到设置页加一个服务商就能用了。",
                    "sources": [], "degraded": True, "provider": ""}
        text = await llm_learn(ctx.get("text") or "", question, history,
                               completion_mode=completion_mode,
                               reply_style=reply_style,
                               thinking_effort=thinking_effort,
                               citation_style=citation_style,
                               language=language,
                               custom_instruction=custom_instruction,
                               api_key=chat.get("api_key", ""),
                               base_url=chat.get("base_url", ""),
                               model=chat.get("model", ""), on_delta=on_delta)
        if not text:
            return {"reply": "模型这次没有返回内容，稍后再问一次；如果反复出现，去设置页做一次服务商测试。",
                    "sources": [], "degraded": True,
                    "provider": chat.get("provider_name", "")}
        return {"reply": text, "sources": ctx.get("sources") or [],
                "degraded": False, "provider": chat.get("provider_name", ""),
                "completion_mode": completion_mode}

    if data.get("stream"):
        return _review_stream(_run)
    return await _run(None)


@app.get("/api/review/status/{session_id}")
async def api_review_status(session_id: str):
    sess = _get_review_session(session_id)
    if not sess:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return sess.snapshot()


@app.get("/api/review/progress")
async def api_review_progress(file: str):
    """复习进度（待复测/薄弱点/今日到期/历次会话），用于入口页展示。"""
    prog = _review_store.load_progress(file)
    entries = prog.get("units", {}) or {}
    now = time.time()
    due, seen_due = [], set()
    for src in ("weak_units", "retest_queue"):
        for x in prog.get(src, []):
            uid = x.get("unit_id")
            if not uid or uid in seen_due:
                continue
            seen_due.add(uid)
            e = entries.get(uid) or {}
            nd = float(e.get("next_due") or x.get("due_at") or 0)
            if not nd or nd <= now:
                due.append({"unit_id": uid, "title": x.get("title", ""),
                            "path": x.get("path", [])})
    return {
        "retest": prog.get("retest_queue", []),
        "weak": prog.get("weak_units", []),
        "due": due,
        "due_count": len(due),
        "unit_count": len(prog.get("units", {})),
        "sessions": [{"session_id": s.get("session_id"),
                      "ended_at": s.get("ended_at"),
                      "passed": s.get("passed", 0), "half": s.get("half", 0),
                      "failed": s.get("failed", 0)}
                     for s in prog.get("sessions", [])[-5:]],
    }


# ────────────────────────────────────────────
# Library management API
# 收藏、删除、可重新导入的资料数据包
# ────────────────────────────────────────────
_favorites_lock = threading.Lock()


def _safe_library_key(value: str) -> str:
    key = os.path.basename(str(value or "").strip())
    if not key or key in (".", ".."):
        return ""
    return key


def _task_id_from_key(key: str) -> str:
    if not key.startswith("task_"):
        return ""
    task_id = key[5:]
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", task_id or "") or task_id.startswith("_"):
        return ""
    return task_id


def _resolve_tree_resource(key: str):
    """Locate a completed tree by the client-facing resource key."""
    key = _safe_library_key(key)
    if not key:
        return None
    task_id = _task_id_from_key(key)
    if task_id:
        work_dir = os.path.join(TEMP_DIR, task_id)
        tree_path = os.path.join(work_dir, "json", "knowledge_tree.json")
        if os.path.isfile(tree_path):
            return {"kind": "task", "key": key, "task_id": task_id,
                    "work_dir": work_dir, "tree_path": tree_path}
        return None
    path = os.path.join(ENHANCED_DIR, key)
    if os.path.isfile(path):
        return {"kind": "enhanced", "key": key, "tree_path": path}
    return None


def _read_favorites_file() -> list:
    try:
        with open(FAVORITES_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        keys = raw.get("keys") if isinstance(raw, dict) else raw
        if isinstance(keys, list):
            return [str(x) for x in keys if str(x).strip()]
    except Exception:
        pass
    return []


def _write_favorites_file(keys: list) -> None:
    ordered = []
    for key in keys:
        if key and key not in ordered:
            ordered.append(key)
    os.makedirs(os.path.dirname(FAVORITES_PATH), exist_ok=True)
    tmp = FAVORITES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"keys": ordered, "updated_at": time.time()}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FAVORITES_PATH)


def _load_favorites() -> list:
    with _favorites_lock:
        return _read_favorites_file()


def _set_favorite(key: str, favorite: bool) -> list:
    key = _safe_library_key(key)
    if not key:
        return _read_favorites_file()
    with _favorites_lock:
        keys = _read_favorites_file()
        if favorite:
            if key not in keys:
                keys.append(key)
        else:
            keys = [x for x in keys if x != key]
        _write_favorites_file(keys)
        return keys


def _review_storage_key(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.一-鿿-]+", "_", key or "unknown")[:80]


def _delete_review_data(key: str) -> None:
    """Remove progress and session snapshots that belong to a deleted document."""
    try:
        shutil.rmtree(os.path.join(REVIEW_DIR, _review_storage_key(key)), ignore_errors=True)
    except Exception:
        pass
    sessions_dir = os.path.join(REVIEW_DIR, "sessions")
    if not os.path.isdir(sessions_dir):
        return
    for name in os.listdir(sessions_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(sessions_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("file_key") == key:
                os.remove(path)
        except Exception:
            continue


@app.get("/api/favorites")
async def api_favorites_get():
    return {"keys": _load_favorites()}


@app.post("/api/favorites")
async def api_favorites_post(data: dict):
    key = _safe_library_key(data.get("key") or "")
    if not key:
        return JSONResponse({"error": "缺少资料 key"}, status_code=400)
    keys = _set_favorite(key, bool(data.get("favorite")))
    return {"ok": True, "key": key, "keys": keys}


@app.post("/api/delete_resource")
async def api_delete_resource(data: dict):
    key = _safe_library_key(data.get("key") or "")
    if not key:
        return JSONResponse({"error": "缺少资料 key"}, status_code=400)

    task_id = _task_id_from_key(key)
    if task_id:
        work_dir = os.path.join(TEMP_DIR, task_id)
        if not os.path.isdir(work_dir):
            return JSONResponse({"error": "资料不存在"}, status_code=404)
        with tasks_lock:
            live = tasks.get(task_id)
        if live is not None and live.status not in ("done", "error"):
            return JSONResponse({"error": "该任务仍在处理中，暂时不能删除"}, status_code=409)
        try:
            shutil.rmtree(work_dir)
        except Exception as e:
            return JSONResponse({"error": f"删除失败：{e}"}, status_code=500)
        kind = "task"
    else:
        path = os.path.join(ENHANCED_DIR, key)
        if not os.path.isfile(path):
            return JSONResponse({"error": "资料不存在"}, status_code=404)
        try:
            os.remove(path)
        except Exception as e:
            return JSONResponse({"error": f"删除失败：{e}"}, status_code=500)
        kind = "enhanced"

    _delete_review_data(key)
    _set_favorite(key, False)
    return {"ok": True, "key": key, "kind": kind}


@app.get("/api/package/{resource_key}")
async def api_package_export(resource_key: str):
    """Portable parsed-document package for re-import on this or another install."""
    resource = _resolve_tree_resource(resource_key)
    if not resource:
        return JSONResponse({"error": "资料不存在或尚未完成解析"}, status_code=404)
    try:
        with open(resource["tree_path"], encoding="utf-8") as f:
            tree = json.load(f)
    except Exception as e:
        return JSONResponse({"error": f"读取资料失败：{e}"}, status_code=500)
    if not isinstance(tree, dict):
        return JSONResponse({"error": "资料格式无效"}, status_code=422)

    key = resource["key"]
    title = str(tree.get("title") or tree.get("name") or key).strip() or key
    raw_sources = tree.get("original_files") or tree.get("source_files") or []
    if isinstance(raw_sources, str):
        raw_sources = [raw_sources]
    original_files = [str(x) for x in raw_sources if str(x).strip()]

    manifest = {
        "format": "anythingmindmap.package",
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "resource": {
            "key": key,
            "title": title,
            "source": resource["kind"],
            "original_files": original_files,
            "node_count": _count_nodes(tree),
            "favorite": key in _load_favorites(),
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("tree.json", json.dumps(tree, ensure_ascii=False, indent=2))
        report_path = os.path.join(resource.get("work_dir", ""), "report.json") if resource["kind"] == "task" else ""
        if report_path and os.path.isfile(report_path):
            zf.write(report_path, "report.json")
        review_path = os.path.join(REVIEW_DIR, _review_storage_key(key), "progress.json")
        if os.path.isfile(review_path):
            zf.write(review_path, "review_progress.json")
        zf.writestr("README.txt",
                    "AnythingMindmap 资料数据包\n\n"
                    "请在资料库点击“导入数据包”，选择本 ZIP 即可重新导入已解析的知识树。\n"
                    "数据包只包含解析结果，不包含原始 PDF / DOCX / TXT 文件。\n")
    filename = "anythingmindmap_package_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/import-package")
async def api_import_package(file: UploadFile = File(...)):
    """Import a package exported by /api/package or a legacy export ZIP."""
    limit = 50 * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        return JSONResponse({"error": "数据包超过 50MB 限制"}, status_code=413)

    manifest = {}
    review_progress = None
    tree = None
    names = set()
    raw_content = content[3:] if content.startswith(b"\xef\xbb\xbf") else content
    if raw_content.lstrip().startswith(b"{"):
        try:
            tree = json.loads(raw_content.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"error": f"JSON 解析失败：{e}"}, status_code=400)
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = set(zf.namelist())
                if "manifest.json" in names:
                    try:
                        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                    except Exception:
                        manifest = {}
                if manifest and manifest.get("format") not in ("anythingmindmap.package", "anythingmindmap-package"):
                    return JSONResponse({"error": "不是 AnythingMindmap 数据包"}, status_code=422)
                tree_name = next((n for n in ("tree.json", "json/knowledge_tree.json", "knowledge_tree.json")
                                  if n in names), "")
                if not tree_name:
                    return JSONResponse({"error": "数据包中缺少 tree.json"}, status_code=422)
                try:
                    tree = json.loads(zf.read(tree_name).decode("utf-8"))
                except Exception as e:
                    return JSONResponse({"error": f"知识树解析失败：{e}"}, status_code=422)
                if "review_progress.json" in names:
                    try:
                        review_progress = json.loads(zf.read("review_progress.json").decode("utf-8"))
                    except Exception:
                        review_progress = None
        except zipfile.BadZipFile:
            return JSONResponse({"error": "文件不是有效的 ZIP 数据包"}, status_code=400)

    if not isinstance(tree, dict):
        return JSONResponse({"error": "知识树数据格式无效"}, status_code=422)

    manifest_resource = manifest.get("resource") if isinstance(manifest, dict) else {}
    if not isinstance(manifest_resource, dict):
        manifest_resource = {}
    title = str(tree.get("title") or tree.get("name") or manifest_resource.get("title")
                or Path(file.filename or "").stem or "导入的资料").strip() or "导入的资料"
    if not tree.get("title"):
        tree["title"] = title
    if not tree.get("original_files") and manifest_resource.get("original_files"):
        tree["original_files"] = [str(x) for x in manifest_resource.get("original_files") if str(x).strip()]

    slug = re.sub(r"[^0-9A-Za-z一-鿿_-]+", "_", title).strip("_")[:36] or "resource"
    new_key = f"imported_{slug}_{uuid.uuid4().hex[:6]}.json"
    target = os.path.join(ENHANCED_DIR, new_key)
    tmp = target + ".tmp"
    try:
        os.makedirs(ENHANCED_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return JSONResponse({"error": f"保存导入资料失败：{e}"}, status_code=500)

    review_imported = False
    if isinstance(review_progress, dict):
        try:
            review_progress["file_key"] = new_key
            review_progress["sessions"] = []
            review_dir = os.path.join(REVIEW_DIR, _review_storage_key(new_key))
            os.makedirs(review_dir, exist_ok=True)
            review_path = os.path.join(review_dir, "progress.json")
            tmp_review = review_path + ".tmp"
            with open(tmp_review, "w", encoding="utf-8") as f:
                json.dump(review_progress, f, ensure_ascii=False, indent=2)
            os.replace(tmp_review, review_path)
            review_imported = True
        except Exception:
            review_imported = False

    if manifest_resource.get("favorite"):
        _set_favorite(new_key, True)

    return {
        "ok": True,
        "key": new_key,
        "title": title,
        "node_count": _count_nodes(tree),
        "review_progress": review_imported,
    }


# ────────────────────────────────────────────
# Settings API (统一配置中心)
# 模型服务（OpenAI 兼容，多套+默认） / MinerU Token / 学科设置
# 密钥只落 data/settings.json；本节所有响应一律脱敏
# ────────────────────────────────────────────

@app.get("/api/settings")
async def api_settings_get():
    return _app_settings.public_view()


@app.post("/api/settings")
async def api_settings_post(data: dict):
    op = (data.get("op") or "").strip()
    try:
        if op == "upsert_provider":
            pid = _app_settings.upsert_provider(data.get("provider") or {})
            return {"ok": True, "id": pid, "settings": _app_settings.public_view()}
        if op == "delete_provider":
            _app_settings.delete_provider((data.get("id") or "").strip())
            return {"ok": True, "settings": _app_settings.public_view()}
        if op == "set_active":
            _app_settings.set_active((data.get("id") or "").strip())
            return {"ok": True, "settings": _app_settings.public_view()}
        if op == "set_general":
            _app_settings.set_general(data)
            return {"ok": True, "settings": _app_settings.public_view()}
        if op == "set_enhance":
            cfg = _app_settings.set_enhance((data.get("scope") or "").strip())
            return {"ok": True, "enhance": cfg, "settings": _app_settings.public_view()}
        if op == "test_provider":
            # 内联参数优先；只给 id 时用已保存的 Key/端点做测试
            base = (data.get("base_url") or "").strip()
            model = (data.get("model") or "").strip()
            key = (data.get("api_key") or "").strip()
            if data.get("id") or (not base and not key):
                doc = _app_settings.load()
                prov = _app_settings._find(doc, (data.get("id") or "").strip()) \
                    or _app_settings._find(doc, doc.get("active_id", ""))
                if prov:
                    base = base or prov.get("base_url", "")
                    model = model or prov.get("model", "")
                    key = key or prov.get("api_key", "")
            if not base and not key:
                return JSONResponse({"ok": False, "error": "没有可测试的服务商"}, status_code=400)
            result = await _app_settings.test_chat(base, key, model)
            if result.get("ok") and data.get("id"):
                doc = _app_settings.load()
                prov = _app_settings._find(doc, (data.get("id") or "").strip())
                if prov:
                    prov["verified_at"] = time.time()
                    _app_settings.save(doc)
            return result
        if op == "test_mineru":
            token = (data.get("mineru_token") or "").strip() or _app_settings.mineru_token()
            return _app_settings.test_mineru(token)
        return JSONResponse({"error": f"未知 op: {op}"}, status_code=400)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting AnythingMindmap Pipeline Server on {host}:{port}...")
    print(f"Temp dir: {TEMP_DIR}")
    uvicorn.run(app, host=host, port=port)
