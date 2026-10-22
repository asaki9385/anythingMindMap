# AGENTS.md

## Project Overview

AnythingMindmap — a document-to-knowledge-tree pipeline. Upload PDF/Word/TXT, parse with OCR + LLM, generate structured knowledge trees, render as interactive mind maps. Oriented toward Chinese education (333 exam prep).

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # add DeepSeek/MinerU API keys (both optional)
python start.py         # starts server at http://localhost:8000
```

Windows: `start.bat`. macOS: `start.command`.

## Architecture

```
knowledge-compiler/
  server.py            — FastAPI app, all API routes, pipeline orchestration (53KB, main entry)
  tree_builder.py      — Markdown → tree JSON (parallel per-file)
  node_enhancer.py     — AI enhancement: summaries, keywords, exam points, Mermaid
  hierarchy_repair.py  — Multi-numbering-system hierarchy repair
  outline_skeleton.py  — 结构前置层：大纲标题恢复（纯文本 第X节/知识点X 按序列提升）、
                         同级序列校验、缺失边界占位修复；解析与增强两个入口共用
  structure_repair.py  — Enhancement-stage structural audit (order/titles)
  placement_audit.py   — 主题归属审校（实体注册表：把挂错主题的节点移回；保守安全门）
  review_engine.py     — 对话复习引擎：追问模式（提问式判定）+ 学习模式（原文接地问答）
                          + 长期学习者模型（通过/遗忘/到期调度）
  parse_report.py      — Parse report: AI-stage token metering + tree stats (data panel)
  app_settings.py      — Unified config (model providers, MinerU token, subject, enhance scope)
  parser/
    pdf_splitter.py    — PDF split by TOC/content/pages
    text_extractor.py  — docx/txt → Markdown
    llm_structurer.py  — LLM structuring for unstructured text
  mineru_adapter/
    client.py          — MinerU cloud OCR API client
  ui/                  — Static frontend (HTML/CSS/JS, no build step)
    client.html        — 统一客户端壳层（home/parse/library/mindmap/chat）
    client.js          — 场景路由、上传、资料库、跨场景交互
    client.css         — 客户端布局与 CDS 主题
    overview.html      — 资料导览（iframe embed）
    tree_mindmap.html  — 沉浸式思维导图（iframe embed / 离线导出模板）
    settings.html      — 统一设置（iframe embed）
    review_widget.js   — AI 对话复习组件
    common.js          — Shared JS utilities
    theme.css          — 基础亮/暗主题
    cds-theme.css      — CDS 设计令牌
```

## Tech Stack

- **Backend:** Python 3.10+, FastAPI, Uvicorn, PyMuPDF, httpx, python-docx
- **AI:** DeepSeek API (OpenAI-compatible), MinerU cloud OCR
- **Frontend:** Vanilla HTML/CSS/JS, ECharts (mind maps), GSAP + ScrollTrigger (animations)
- **Design:** "Warm Academic" system — Noto Serif SC + Noto Sans SC + JetBrains Mono, light/dark themes

## Key Conventions

- Frontend is plain HTML/CSS/JS — no framework, no build step, no npm. Files are served by FastAPI `StaticFiles` at `/ui`.
- CSS uses custom properties defined in `theme.css`. All new UI must use these variables for colors, spacing, typography.
- Dark mode via `[data-theme="dark"]` selector on root element. Use CSS variables, never hardcode colors.
- GSAP animations: use `gsap.to()` for scroll-triggered reveals (elements start at `opacity: 0` in CSS). Register `ScrollTrigger` plugin before use.
- API endpoints are all in `server.py`. The pipeline: upload → split → OCR → markdown → tree build → AI enhance → JSON output.
- Processing data lives in `knowledge-compiler/data/_temp_uploads/` (gitignored).
- Python dependencies: `requirements.txt`. No pyproject.toml or poetry.
- Environment config via `.env` file (see `.env.example` for all options).

## Graceful Degradation

Without API keys the system still works for documents with heading structure (regex-based parsing). OCR and AI enhancement are skipped.

## Running Tests

Standalone test scripts (no pytest config):

```bash
python test_document_processing.py
python test_advanced_processing.py
cd knowledge-compiler
python test_all_numbering.py
python test_app_settings.py
python test_configured_provider_e2e.py  # 统一配置 → 实际模型调用全链路（本地 Mock，无需 Key）
python test_parse_report.py        # 解析报告：token 累计 / 树统计口径
python test_placement_audit.py     # 主题归属审校：框标题保留 / 跨主题条目移回 / 安全门
python test_outline_skeleton.py    # 结构前置层：标题恢复 / 序列不变量 / 占位修复 / 守卫
python test_hierarchy_recovery.py  # 全链路：破坏矩阵(21模式×24本；RECOVERY_SEEDS=400 深跑) + 语料 + 模糊 + server 全链路 + 耗时
                                   #   改 outline_skeleton/tree_builder/hierarchy_repair 后必须跑
python test_structure_repair.py
python test_review_engine.py
python test_guide_generation.py
python test_structure_generalize.py
python test_six_fixes.py            # 六个保守修复回归
python test_image_refs.py           # 正文 images/ 路径清理
python test_document_title.py       # 资料标题生成 / 重命名
# UI 端到端（jsdom，用 JSDOM_PATH 指定 jsdom 安装位置，勿在项目内装 node_modules）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_client.js          # 客户端壳层（五场景/路由/交互）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_mindmap.js         # 导图页 + 对话组件（模式开关/导出）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_overview.js        # 信息页（导学/学习路径/复习进度）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_settings.js        # 设置页（含增强范围档位）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_image_refs.js      # 图片引用前端兜底
```

## Important Notes

- `server.py` is the single backend entry point — all routes and pipeline logic live there.

### 2026-10-17 conservative fixes（不要回退）

- **导学模块投影**：`node_enhancer.guide_modules()` 统一复用 `parse_report.book_modules`
  的解包口径。单册上传是 root → 一本书 → 章，直接遍历 `tree.children` 会把导学和
  先修挂到包装节点；生成导学、统计、前端展示必须使用同一投影。
- **资料标题**：`document_title.py` 负责清洗文件名、兜底标题、LLM 标题解析与
  `apply_document_title`；标题同时写 `task.title`、树根 `title`、`task_state.json`、
  缓存 `meta.json`。`/api/rename` 支持 `task_id`（任务树）和 `filename`（旧本地树）；
  单册包装时同步包装节点标题。前端资料库（`client.js`）用铅笔按钮重命名。
- **图片引用不进正文**：`tree_builder.sanitize_tree_images` 把本地 `images/...` 从
  `content/summary/guide/exam_points` 等字段移到 `node["images"]`；`common.js` 的
  `stripLocalImageRefs` 是旧任务的前端兜底。当前不渲染图片；需要渲染时先补
  MinerU 图片资产提取与鉴权路由，不要直接把 hash 路径塞回正文。
- **Markdown 兜底不丢正文**：`tree_builder.parse_md_to_nodes` 现在会保留首个标题前的
  导言（`文档导言` 节点）、图片引用、普通 HTML、代码块，以及“前言/参考答案”等
  内容型标题下的正文；只有完全无正文/子节点的噪音标题才会丢弃。不要再加
  `![`、`<`、code fence、`is_noise` 的直接跳过。
- **骨架不静默丢段落**：`skeleton_builder.fragment_to_tree` 对
  `MAX_PARAS_PER_CALL=120` 之外的尾部段落会接“（后续内容）”节点；非连续缺口
  直接回退 `paragraphs_to_tree`。任何 `lost>0` 都不能继续使用骨架树。
- **节点编辑**：`/api/update-node` 支持 `task_<task_id>` 指向
  `_temp_uploads/<task_id>/json/knowledge_tree.json`，并兼容旧的
  `data/tree_parts_enhanced_fixed/`；`tree_mindmap.html` 使用同源 `API_BASE`
  （file:// 才回退 8000），不要再写死 8700。编辑会同步内存 result 和内容缓存树。
- **凭证默认留空**：`app_settings.py` 的 `BUILTIN_CHAT` / `BUILTIN_MINERU_TOKEN`
  不随仓库携带 Key，运行时通过设置页或环境变量配置；默认 `HOST=127.0.0.1`，
  CORS 只允许同源（`CORS_ORIGINS` 显式配置）；设置 `KT_AUTH_PASSWORD` 后启用
  HTTP Basic Auth，`/api/health` 不拦截。不要提交公开可用的真实 Key 或 `allow_origins=["*"]`。
- **任务状态与缓存**：`TaskProgress.persist_state()` 写
  `data/_temp_uploads/<task_id>/task_state.json`；`load_task_state(recover_interrupted=True)`
  会把重启遗留的 `processing` 标成中断。内容缓存位于 `data/_cache/<key>/`，
  键由文件 SHA-256 + 模型/OCR 设置 + `CACHE_VERSION` 组成；修改解析/增强语义
  必须提升 `CACHE_VERSION`。`/api/tasks` 跳过 `_` 开头的内部目录。
- **相关回归**：`python test_six_fixes.py` 覆盖长片段续节点、Markdown 保留、
  导学投影、task 编辑、状态/缓存、安全默认值；`python test_image_refs.py` 与
  `node test_ui_image_refs.js` 覆盖本地图片引用清理。
- Upload progress is **polled** (`GET /api/status/{id}`) by the upload page.
  `GET /api/status/{id}/stream` (SSE) exists but has no caller.
- Parsing finishes into a **data panel, not a redirect** — the upload page shows
  `/api/report/{id}` (node scale, AI coverage, AI-stage tokens, files/pages) and only
  navigates when the user clicks. See `docs/解析报告面板.md`.
- 容器族标题（知识点 X 无编号 / 考点 N / 考点 X）与编号知识点同级（hierarchy_repair），
  其子项（（一）/1./①）必须挂在容器下；连续的无编号同级标题是兄弟（不链式互套）；
  裸「知识点」/「考点」+ 下一行名称按拆分标题合并（tree_builder）。
- Structure front layer (骨架优先): `outline_skeleton.normalize_markdown_outline` runs
  BEFORE `parse_md_to_nodes` (plain-text `第X节/知识点X` lines are promoted by numbering
  sequence continuity; repeated running headers dropped; chapter subtitles merged), and
  `audit_tree_skeleton` runs after `build_tree` (per file) and again at the START of
  `_enhance_tree` before `structure_repair` (kp-number restarts → 「（未识别小节）」placeholder;
  violations go into the parse report panel). Never write raw parse fixes that bypass this
  layer; downstream guards: LLM level validation rolls back if rebuild increases skeleton
  violations; `structure_repair._rename_allowed` refuses renames that add structural tokens
  (第X章节/知识点X); `hierarchy_repair.detect_numbering_type` ignores `「333 教育综合…」`
  page headers (pure int > 99 + whitespace).
- Parse stage guards content placement: `placement_audit.py` (deterministic) moves nodes
  that sit under a different topic back to their own 实体主题 (runs per file in
  `server._build_trees_from_md` after LLM hierarchy validation, and in `build_single_tree`).
  Since the structure front layer exists, this is now only the last safety net, not the main fix.
  `tree_builder.is_noise` must never drop content-box headings (丹丹攻略/自测表/考情分析…);
  `hierarchy_repair` classifies them as box containers (category 4) so following items
  nest inside the box instead of leaking into the previous numbered section.
- Tree stats in `parse_report.py` must stay口径-aligned with the frontend
  (`KT.bookModules` unwrapping; node count includes the root).
- 对话系统双模式：追问模式（默认；AI 出题、判定静默执行）与学习模式（自由问答、
  只依据原文、出处由服务端从检索层确定性附上——不交给模型生成）。切在
  `review_widget.js` 头部「学习/追问」开关，偏好存 localStorage `kt_review_mode`；
  信息页章节卡入口（`startChapter`）固定走追问。学习模式接口 `POST /api/review/learn`
  （流式 NDJSON，检索上下文由 `review_engine.build_learn_context` 组装，上限 7000 字）；
  无会话、不判分、不记进度。
  追问的四个口径（2026-10-16 修）：① 新会话第一条消息先说清复习范围
  （`ReviewSession(intro_label=…)`：章节/复练队列 + 考点数）——入口直接开考时不再"凭空发问"；
  ② 整句「不知道/不会/忘了/想不起来了/没印象/答不上来」一律按求助处理
  （`is_help_only`，不计轮次、不触发 S2/S1 卡壳止损）：先 H1 再 H2，提示用尽才讲解并
  收掉该点（S4_abandon）；③ 提示词明确禁止"翻书/抄标题"式操作建议，提示必须是回忆线索；
  ④ 打开面板时若存在"未结束且已闲置 ≥30 分钟"的会话（`RESUME_PROMPT_AFTER`），先出
  「接着上次 / 换方向」询问，而不是直接续上（闲置判定用最后一条消息的服务端 ts）。
- 长期学习者模型（review_engine.ReviewStore）：每个单元记录 `passes/lapses/next_due`；
  间隔阶梯 `REVIEW_STEPS_DAYS = [1,2,4,7,15,30]` 天（过关前进、提示辅助=明天、未过关=立即到期）。
  「薄弱点重练」（scope=retest）按到期时间排序（`due_unit_ids`：薄弱点+复测队列）；
  `/api/review/units`、`/api/review/progress` 暴露 `due_count/due`，入口页显示「今日到期」。
- 导出（导图页导出菜单）：PNG / 讲义（当前视图·全书）/ Markdown 大纲（标题层级+摘要关键词考点注记）
  / 复习卡 CSV（Anki 可导入：Front/Back/Tags，卡片口径与对话复习的单元同源——考点节点 ∪ 2–8 子节点）。
  `_downloadText` 在无 `URL.createObjectURL` 的环境退化为 data URI（jsdom 测试走这条）。
- 增强范围档位（设置页「增强范围」）：fast=最前 30 个节点 / standard=全部 L1–L3（默认）/
  full=L1–L4 全量；`app_settings.enhance_config()` → `server._enhance_tree` 的
  `ENHANCE_LEVELS_ALLOWED/MAX_ENHANCE_NODES`。档位只影响新上传；旧资料需重新解析才生效。
  解析报告面板按 `ai.scope_label/allowed_levels/max_nodes` 展示范围（不再写死「优先解读靠前章节」）。
- Google Fonts are loaded via CDN — requires internet access.
