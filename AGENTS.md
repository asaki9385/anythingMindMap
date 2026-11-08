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

Windows source startup auto-detects Python 3.10+. If none is available, it
downloads and verifies official Python 3.12.10 from domestic mirrors with an
official fallback, installs it under `.runtime/`, creates `.runtime/venv`,
installs `requirements.txt` through domestic PyPI mirrors with an official
fallback, and then starts the app. If a registered Windows Python installation
prevents the official installer from creating `.runtime/python`, the launcher
must fall back to the official embeddable Python ZIP plus the pinned pip wheel.
Record startup failures in `.runtime/bootstrap/bootstrap.log` and keep the
user-facing explanation in Chinese. The source launcher requires network access
and a writable project directory on first run; do not require administrator
rights or modify the system PATH.

Windows 用户完整操作说明：`【Windows用户必看】双击start.bat启动.txt`。

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
    tree_mindmap.html  — 知识地图页面壳（iframe embed / 离线导出模板）
    knowledge_map.css  — 地图视觉、节点、搜索、小地图、编辑栏
    map/               — map_adapter.js（树⇄Mind Elixir）、knowledge_map.js（KTMap 控制器）、map_exports.js
    vendor/            — Mind Elixir 6 IIFE/CSS 与 export-mindmap UMD（MIT，见 VENDOR.md）
    settings.html      — 统一设置（iframe embed）
    review_widget.js   — AI 对话复习组件
    common.js          — Shared JS utilities
    theme.css          — 基础亮/暗主题
    cds-theme.css      — CDS 设计令牌
```

## Tech Stack

- **Backend:** Python 3.10+, FastAPI, Uvicorn, PyMuPDF, httpx, python-docx
- **AI:** DeepSeek API (OpenAI-compatible), MinerU cloud OCR
- **Frontend:** Vanilla HTML/CSS/JS, Mind Elixir 6 (vendored mind map engine), GSAP + ScrollTrigger (overview/settings animations)
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
# Windows PowerShell 5.1+
./scripts/test_windows_bootstrap.ps1
python scripts/smoke_test_windows_source.py --mode auto --port 8765
python scripts/smoke_test_windows_source.py --mode embedded --port 8766
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
python test_fidelity.py              # 原文保真：段落/字符/标题/顺序/合成节点
python test_numbering_profile.py     # 编号画像：族顺序/重启作用域/未知族/低置信度回退
python test_skeleton_quality.py      # 骨架契约：全覆盖/重叠/顺序/窗口拼接/确定性降级
python test_six_fixes.py            # 六个保守修复回归
python test_image_refs.py           # 正文 images/ 路径清理
python test_document_title.py       # 资料标题生成 / 重命名
# UI 端到端（jsdom，用 JSDOM_PATH 指定 jsdom 安装位置，勿在项目内装 node_modules）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_client.js          # 客户端壳层（五场景/路由/交互）
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_mindmap.js         # 导图页 wiring（fake engine）
JSDOM_PATH=/path/to/node_modules/jsdom node test_map_controller.js     # KTMap + 真实 Mind Elixir（jsdom 数据/DOM/事件）
JSDOM_PATH=/path/to/node_modules/jsdom node test_map_exports.js        # 讲义/Markdown/Anki/PNG 路由
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_map_sync.js        # 壳层 ↔ 地图 postMessage 同步
node test_map_adapter.js                                               # 树⇄Mind Elixir 纯数据往返/搜索/平衡
python test_tree_save.py                                               # /api/tree/{key} + /api/tree/save
python test_offline_map.py                                             # 离线树内嵌/资源清单
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

### 2026-11-07 思维导图内核迁移（新增，不要回退）

- **导图内核是 Mind Elixir 6，不是 ECharts。** `ui/tree_mindmap.html` 不再加载 ECharts/GSAP；
  `ui/vendor/mind-elixir/` 是提交的 IIFE + CSS（MIT，构建方式/校验和见 `ui/vendor/VENDOR.md`）。
  不要重新引入 ECharts tree，也不要在页面里直接调用 Mind Elixir 内部 API。
- **唯一引擎边界是 `ui/map/knowledge_map.js` 的 `KTMap`。** 页面、测试、后续学习层都通过
  `KTMap` API；`map_adapter.js` 只做纯数据（树 ⇄ Mind Elixir、稳定 id、路径、搜索索引、平衡方向）。
- **轻/重字段分离**：Mind Elixir 的 `metadata.kt` 只放摘要/关键词/考点/导学等轻字段；
  `content/tables/...` 留在 `MapAdapter.heavyById`，避免每次撤销快照背着整棵树正文。
  序列化时丢弃 `parent/style/tags/icons/branchColor/expanded/direction` 等视图字段。
- **节点 id 只在保存时写回树**；旧树首次保存由适配层补 `n_...`。服务端 `/api/tree/save`
  要求每个节点有合法唯一 id，按 id 合并（保留服务端未知字段），拒绝根标题修改。
- **浏览不用 `focusNode`**（它会换文档并清空撤销栈）；搜索/定位用 `spotlight`（CSS 暗化非相关分支）。
  默认布局 SIDE 权重平衡；节点数 >800 时默认只展开到章级（1 层），避免 1,600 节点一屏 400+ 节点。
- **排列方向必须走引擎方向命令**：`diffRefresh(data)` 会忽略顶层 `direction`；实时切换
  先调用 `initLeft/initRight/initSide/initDown`，再 `refresh(data)` 应用 SIDE 权重，最后
  `scaleFit()`。只改 select 文本或只重建节点数据会造成“选项变了但图不转”。
- **小地图走内容坐标**：`KTMapAdapter.fitBoxesToCanvas/minimapViewport` 负责纯几何；
  不要恢复解析 `translate3d(...)` 字符串。画布需保留树连线、节点块、真实视口，并支持
  点击/拖拽定位；621–900px 缩尺寸，≤620px 才隐藏。
- **键盘由产品层接管**：Mind Elixir `keypress:false`，否则内置 handler 会拦截 Ctrl+F/Cmd+Z；
  `mouseSelectionButton:2` 保留左键平移、右键框选；右键菜单是产品自己的中文菜单（`contextMenu:false`）。
- **只读/编辑双保险**：`mei.editable` 控制拖拽/内联编辑；`before` hooks 在非编辑模式 veto
  `addChild/insertSibling/insertParent/removeNodes/move*`；根节点不可改名/删除。
- **编辑工具条**：7 个动作必须保持「线性图标 + 文本」且用 flex 双向居中；编辑态顶部工具栏
  只显示 ghost「编辑中」，每屏 clay 主操作仅保留浮条「完成」。
- **保存接口**：`GET /api/tree/{key}` 返回 `{tree,rev,node_count}`；`POST /api/tree/save`
  用 `base_rev` 做 409 冲突检测，写前备份到 `json/backups/` 或 `.backups/`，原子写，
  同步 live `task.result`/`task_state.tree_edited`/cache tree；不重算报告/导学/复习单元。
- **复习进度**：`review_engine.unit_id` 仍按 path+title；改名/移动/删除会让该节点复习记录失联，
  进入编辑模式时提示；根标题只能在资料库重命名。
- **离线导出**：`_generate_offline_html(tree)` 把树以 `<script id="kt-offline-tree">` 内嵌，
  页面用 `window.KT_OFFLINE` 进入只读；ZIP 资源清单是 `_offline_asset_list()`，新增本地资源必须同步。
- **相关测试**：`test_map_adapter.js`、`test_map_controller.js`、`test_ui_mindmap.js`、
  `test_ui_map_sync.js`、`test_map_exports.js`、`test_tree_save.py`、`test_offline_map.py`。改解析/增强语义才需要提升
  `CACHE_VERSION`；本次 UI/保存层迁移不改变解析语义。

### 2026-11-04 信效度、编号画像与骨架质量（新增，不要回退）

- **先恢复再评分**：`run_pipeline` 在 `structure_score.score_fragment` 之前先跑
  `numbering_profile.probe_numbering_profile` + `outline_skeleton.normalize_markdown_outline`。
  纯文本「第X节/知识点X/第X讲」不应因为缺少 `#` 而被误判为结构不良送进 AI 骨架。
  `_build_trees_from_md` 直接复用 `unit_meta["normalized_text"]`，不要重复 normalize。
- **原文保真**：`fidelity.py` 只读审计，指标为 `paragraph_recall / char_coverage /
  heading_recall / heading_precision / order_score / hierarchy_consistency /
  ai_grounding`；报告字段 `report.fidelity`，逐段明细写 `fidelity.json`。
  `ai_grounding` 只是诊断，不得当作原文覆盖率；前端「AI 解读覆盖」与「原文覆盖」
  必须分开显示。`assert_no_content_loss` 仍是硬不变式，不能用覆盖率指标替代。
- **编号画像**：`numbering_profile.py` 从文本探测族顺序/重启作用域/未知族，
  `profile=None` 时完全走旧逻辑。支持 第X讲/单元/课/专题/篇/编、罗马、字母、
  ㉑-㊿、多级点号；`outline_skeleton._promote_titles(profile=...)` 把编号重启当作
  新边界（仅当上级边界已出现），否则交给 `audit_tree_skeleton` 放占位。
  `repair_hierarchy` 只对 `_TYPE_CATEGORY` 之外的族使用 profile rank，避免画像
  漏识别 section 时把知识点抬成节的兄弟。报告字段 `report.numbering`。
- **骨架质量**：`skeleton_quality.py` 负责严格契约（全覆盖/叶间不重叠/顺序/
  深度≤6/标题接地）、多窗口重叠拼接、确定性骨架与质量分。`skeleton_builder`
  失败时把契约错误反馈给下一次 prompt；无模型/骨架被拒时用
  `deterministic_skeleton`（编号切边界，否则每 6 段一块、每 3 块一层），
  仍失败才 `paragraphs_to_tree`。报告字段 `report.skeleton_quality`。
- **AI 树纳入同一治理**：`_skeleton_units_to_trees` 产出后同样跑
  `apply_hierarchy_repair` + `audit_tree_skeleton` + `placement_audit`，
  不再有“第二套树”。
- **相关测试**：`test_fidelity.py`、`test_numbering_profile.py`、
  `test_skeleton_quality.py`；`test_hierarchy_recovery.py` 的每个破坏模式
  追加了原文段落召回/字符覆盖断言。修改解析/增强语义时提升 `CACHE_VERSION`。

### 2026-11-08 导图页信息架构 + 节点内容编辑（新增，不要回退）

- **导览/导图切换不再浮在 iframe 上**：`client.html` 新增 `#previewFrameBar`
  （`.preview-frame-bar`，44px，surface + 发丝底边）承载 `#previewViewSwitch` 与
  「新窗口 / AI 对话」；`.preview-frame-pane` 改为 flex 列，iframe 位于条下。
  `.preview-side-expand` / `.mindmap-rail-expand` / `.preview-loading` 用
  `--preview-bar-h` 下移。不要再把切换器 `position:absolute` 盖在画布上——那是
  2026-11 前「导览/导图按钮压住导图缩放条」的根因。
- **导图页工具栏**：`tree_mindmap.html` 拆成 `.ktm-brandbar`（标识 / 标题 / 设置 /
  主题）与 `.ktm-toolrow > .ktm-toolbar`（`.ktm-toolgroup` + `.ktm-toolsep`）。
  控件统一 32px 高、8px 圆角、ghost 底（hover `--ktm-fill-hover`）；每屏只允许
  一个 clay 实心主操作（编辑中的「完成」）。embed 模式隐藏整条页头，
  `.ktm-toolbar` 变成底部居中浮岛（16px 内缩、surface-popover + shadow-popover +
  16px 圆角），菜单向上弹出；`#btnDrawer/#btnReview/#btnOverview` 在 embed 隐藏。
- **菜单尺寸**：`.ktm-menu-panel` 用 `width:max-content; min-width:232px;
  max-width:min(360px, calc(100vw - 24px))`；菜单项必须单行
  （`white-space:nowrap`，提示 `margin-left:auto`）。不要再给 `.ktm-menu-select`
  写 `width:100%`，否则 label 被挤成两行。
- **CDS 令牌桥**：`knowledge_map.css` 顶部定义 `--ktm-*`（ghost fill / popover 阴影 /
  field 阴影 / ease），底层读 `--cds-*` 并在缺失时回退；不要在导图页引入裸 HEX、
  `transition: all` 或 34/42px 这类不在 CDS 阶梯上的高度。
- **节点内容编辑**：编辑模式下详情面板即节点检视器——名称（单行输入）、摘要、
  关键词（chip 增删）、原文、笔记都可就地改写。改动经 `applyNodePatch` 调用
  `mei.reshapeNode`（一次编辑 = 一条撤销记录），600ms 防抖 + blur/change 提交；
  输入框带 IME 守卫，写面板前会先 blur 掉画布上的 `#input-box`。
  正文是重字段：首次编辑时把旧值补进 `metadata.kt`，**并把同一份旧值写进引擎
  刚压栈的那条历史记录 `before` 快照**（`mei.historyStack.getEntries()`；引擎的
  undo 只认 `getData()` 快照，不读 `operation.origin`，只改 live metadata 会被撤销
  丢失）；`map_adapter._fromMeNode` 改为 light 覆盖 heavy，撤销/重做才还原得回来。
  不要把未编辑节点的正文塞进 metadata（撤销快照会变大）。
- **编辑入口**：右键「编辑名称与内容」、编辑浮条「内容」、F2 / 双击（引擎内联改名）。
  根标题仍不可改（服务端拒绝），面板与 `applyNodePatch` 都直接提示。
- **相关测试**：`test_map_controller.js` 增加 9 项编辑断言（面板字段、改名同步画布、
  正文入库、撤销还原、根节点只读）；`test_ui_mindmap.js` / `test_ui_client.js` 保持通过。
