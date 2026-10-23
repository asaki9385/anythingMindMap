# AnythingMindmap

将 PDF / Word / TXT 解析为结构化知识树，生成资料导览和交互式思维导图，并提供对话复习、长期复习调度与多种导出方式。



## 目录

- [功能特性](#功能特性)
- [工作链路](#工作链路)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [界面](#界面)
- [API 接口](#api-接口)
- [项目结构](#项目结构)
- [归档与干净拷贝](#归档与干净拷贝)
- [文档](#文档)
- [数据目录](#数据目录)
- [降级策略](#降级策略)
- [测试](#测试)
- [当前实现说明与已知限制](#当前实现说明与已知限制)
- [环境变量](#环境变量)
- [License](#license)

## 功能特性

- **多格式上传**：PDF、Word（`.docx`）、纯文本（`.txt`），支持拖拽和批量上传
- **PDF 文本提取**：MinerU 云端 OCR，失败时自动回退 PyMuPDF 本地提取
- **大体量 PDF 拆分**：超过 200MB 才按目录、内容检测或固定页数拆分；普通 PDF 整本处理
- **结构质量路由**：根据标题、编号体系和层级线索，自动选择 regex 解析或 AI 骨架两条建树路线
- **原文保真**：AI 骨架只决定标题层级和段落归属，正文由程序按原文锚定，不改写、不丢段
- **结构前置层**：解析前恢复丢失标题、剔除重复页眉、补全章名；建树后校验并补缺失边界占位
- **层级修复与归属审校**：多编号体系识别、章节顺序修正、跨主题错位节点回迁
- **多文件合并**：按上传顺序合并为一棵知识树，并在相邻 PDF 之间生成上下文衔接节点
- **资料标题**：解析后根据文件名和章节结构生成可读标题，资料库支持随时重命名；标题同步到导览、导图、缓存和导出
- **AI 增强**：摘要、关键词、考点、Mermaid 流程图、表格；支持快速 / 标准 / 全量三档范围
- **章节导学**：生成全书导学、逐章导学和前置章节建议
- **解析报告**：节点规模、AI 覆盖率、token 用量、文件页数、结构路由、审校与骨架报告
- **资料导览**：全书导学、学习路径、模块目录、复习进度
- **交互式思维导图**：ECharts 渲染，支持拖拽、缩放、搜索、章节定位、展开/折叠、节点详情、左右平衡 / 全部从左到右 / 全部从右到左 / 上下 / 下上排列和朝向父节点的定位点；单册资料的包装层会自动提升为项目根，节点展开/收起后连接线与双翼根节点同步重排，大图下限制画布像素比以降低渲染成本
- **一致的导览 / 导图切换**：切换器常驻客户端外壳，两个视图位置与样式一致；滑块动画结束后再切换 iframe
- **对话复习**：追问模式（AI 出题、静默判分）与学习模式（基于原文的自由问答）
- **长期复习调度**：记录过关次数、遗忘次数和下次到期时间，支持今日复练和薄弱点队列
- **对话内资料切换**：AI 对话页可直接切换当前文档，不离开对话页即可刷新引用、相关知识点和上下文
- **可折叠工作区**：导图大纲、AI 对话左侧导航和右侧知识库均支持折叠，并保留焦点、滚动位置和折叠状态
- **AI 回答富文本**：Markdown 列表 / 嵌套列表 / 表格、KaTeX 行内与独立公式、代码块及流式输出
- **资料库管理**：收藏 / 取消收藏、收藏夹筛选、删除资料、导入已解析数据包；导出入口同时出现在资料库卡片和导图导出菜单
- **导出**：PNG、打印讲义（支持公式）、Markdown 大纲、带 UTF-8 BOM 的 Anki 复习卡 CSV、可重新导入的资料数据包 ZIP、后端离线导出包
- **统一配置**：模型服务、MinerU Token、学科和增强范围集中管理
- **主题**：亮色 / 暗色全局主题，首页背景随主题交叉淡入

## 工作链路

项目当前有两条主链路：**资料加工链**和**学习复习链**。两者共用最终的知识树 JSON。

### 1. 资料加工链

```text
上传 PDF / DOCX / TXT
        │
        ├─ PDF
        │   ├─ < 200MB：整本处理
        │   └─ > 200MB：按目录 → 内容检测 → 固定页拆分
        │   └─ MinerU OCR → 失败则 PyMuPDF → Markdown
        ├─ DOCX：python-docx → Markdown
        └─ TXT：编码检测 → Markdown
                    │
                    ▼
        score_fragment 片段结构评分
        ├─ ≥ 0.60：regex 解析建树
        └─ < 0.60：AI 只出骨架 + 原文锚定
                    │
                    ▼
 结构前置层 → 层级修复 → 建树 → 骨架审计 → 归属审校
                    │
                    ▼
       多文件合并（来源分组 + 跨 PDF 桥接）
                    │
                    ▼
 结构审校 → AI 增强 → 章节导学 → 清理与最终 JSON
                    │
                    ▼
 knowledge_tree.json + report.json
```

#### ① 上传与任务

- 解析场景（`#parse`）调用 `POST /api/upload`，服务端生成短任务 ID 并在后台线程执行处理流程
- 原始文件保存到 `data/_temp_uploads/<task_id>/uploads/`
- 前端当前通过 `GET /api/status/{task_id}` 轮询进度；SSE 接口存在，但解析场景尚未接入
- 处理阶段：`splitting → ocr_converting → building_tree → ai_enhancing → merging → complete`

#### ② 文本提取

- PDF 小于 200MB 时整本处理；超过 200MB 才进入拆分逻辑
- MinerU 负责云端 OCR；没有 Token 或识别失败时使用 PyMuPDF
- PyMuPDF 路径会过滤页眉页脚、清理页码噪声，并对双栏页面重排
- DOCX 保留标题样式、表格和常用行内格式
- TXT 自动做编码检测，并按标题模式转换成 Markdown
- 长文本会按处理单元切块，后续逐块判断结构质量

#### ③ 结构路由

`structure_score.score_fragment` 对每个 Markdown 片段评分，主要依据：

- 标题占非空行的比例
- 编号体系是否完整、是否有多级层级
- 编号族数量、最大层级深度
- 页眉页脚和目录噪声比例

路由规则：

- `score >= 0.60`：结构可还原，走 regex 解析建树，速度快且不消耗模型 token
- `score < 0.60`：结构不可还原，交 AI 生成骨架
- AI 骨架只输出标题层级和原文段落区间 `src_range`，正文由 `content_anchor` 按区间原样挂载
- 骨架失败或没有模型配置时，退化为按段落保留原文，保证正文不丢失

#### ④ 建树与结构修复

regex 路线的主要顺序：

1. `normalize_markdown_outline`：解析前恢复纯文本标题、剔除重复页眉、补全章名
2. `parse_md_to_nodes`：把 Markdown 转成扁平节点
3. `adjust_standalone_levels`：修正独立标题层级
4. `apply_hierarchy_repair`：识别多编号体系并修复层级
5. `build_tree`：构建父子结构
6. `audit_tree_skeleton`：校验章节边界；缺失边界补“（未识别小节）”占位
7. 可选 LLM 层级校验；如果重建后骨架违规增加，会回滚
8. `placement_audit`：确定性主题归属审校，把明显挂错主题的节点移回自己的实体主题

结构前置层是“先立骨架、再挂内容”：发现不了边界时优先补占位，而不是让后续知识点挂到上一个主题下面。

#### ⑤ 多文件合并

`_fix_hierarchy_and_merge` 会：

- 按来源顺序和单元顺序排序
- 把每个来源包成一个来源节点
- 重建章 / 节层级并统一层级编号
- 在相邻来源之间插入上下文衔接节点
- 最终生成根节点 `AnythingMindmap`

单文件上传时，最终树通常是一个“单册包装”结构，前端会自动解包成章节模块。

#### ⑥ AI 增强

- 模型服务支持任意 OpenAI chat-completions 兼容端点
- 增强前会做一次模型健康检查
- 增强前再次校验骨架，随后执行结构审校
- 节点增强输出：摘要、关键词、考点、Mermaid、表格
- 增强范围：
  - `fast`：最多 30 个节点
  - `standard`：L1–L3 全量（默认）
  - `full`：L1–L4 全量
- 单节点失败会重试；多次失败后用确定性兜底内容填充
- 增强支持断点续跑：同一棵树重跑时，已成功的节点直接跳过
- 最后生成全书导学、逐章导学和前置章节建议，并清理内部字段

#### ⑦ 结果与报告

- `GET /api/result/{task_id}`：最终知识树 JSON
- `GET /api/report/{task_id}`：解析报告
- 报告包含：节点规模、模块数、AI 覆盖率、考点数、token 用量、文件与页数、结构路由、结构审校、骨架报告、耗时
- token 只统计 AI 增强阶段（结构审校 + 节点增强 + 导学）；OCR 和建树阶段不计入
- MinerU 的本地图片引用会从正文移到节点的 `images` 元数据，最终 JSON 不把 hash 路径当正文展示
- 老任务如果没有 `report.json`，会从知识树现算一份只有节点统计的降级报告

#### ⑧ 导出

导图页支持：

- 导出当前视图 PNG
- 打印当前视图讲义
- 打印全书讲义
- 导出 Markdown 大纲
- 导出 Anki 可导入的复习卡 CSV
- 通过后端导出 ZIP 数据包

ZIP 包含离线 `mindmap.html`、`cds-theme.css`、`theme.css`、`common.js`、Markdown 和 JSON。图表库仍从 CDN 加载，首次打开需要网络；如果需要完全离线，需要把 ECharts、GSAP、KaTeX、Mermaid 等前端资源一起本地化。

### 2. 学习复习链

```text
knowledge_tree.json
        │
        ▼
build_review_units（2–8 个子节点 / 带考点叶子）
        │
        ├─ 追问模式：AI 出题 → 回答 → 判分 → 提示 / 换题
        └─ 学习模式：自由问答 → 原文检索 → 接地回答
        │
        ▼
ReviewStore：passes / lapses / next_due
        │
        ▼
今日到期 / 待复测 / 薄弱点
```

#### 复习单元

- 单元来自有 2–8 个子节点的知识块，或带考点标注的叶子节点
- 默认一轮最多 40 个单元；入口页预览最多 300 个
- 支持范围：全书、指定章节、高频考点、待复练队列
- 选中的父单元会屏蔽后代，避免父子重复考

#### 追问模式

- AI 负责出题、回应和自然对话
- LLM 负责判分；代码负责计数、提示阶梯、止损和过关判定
- 提示最多 2 次；连续卡住或超过轮次后，讲解答案并收掉该点
- 内部有 L0–L4、H1–H3、S1–S4 等状态，但不展示给学习者
- 新会话开头会说明复习范围和考点数
- 打开面板时如果存在未结束且闲置 30 分钟以上的会话，会先询问“接着上次 / 换方向”

#### 学习模式

- 自由问答，不做判分、不记进度、不建会话
- `build_learn_context` 从知识树中检索相关节点，组装不超过 7000 字的上下文
- 回答只依据资料原文；出处由服务端确定性附上，不交给模型生成

#### 长期调度

`ReviewStore` 把进度保存到 `data/review/`：

- 每个单元记录 `passes`、`lapses`、`next_due`
- 过关后间隔按 `1、2、4、7、15、30` 天递增
- 提示辅助过关：明天再练
- 未过关：立即重新到期
- 入口页展示“今日到期 / 待复测 / 薄弱点”
- `scope=retest` 按到期时间优先复练

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| PDF 处理 | PyMuPDF、MinerU 云端 OCR |
| Word / TXT | python-docx、chardet |
| AI 服务 | OpenAI chat-completions 兼容端点（DeepSeek、OpenCode 等） |
| 前端 | 原生 HTML / CSS / JS + CDS 设计令牌，无构建步骤 |
| 图表与动画 | ECharts、GSAP、KaTeX、Mermaid（CDN） |
| 对话富文本 | 轻量 Markdown 渲染 + KaTeX 后处理 + 表格 / 列表样式 |
| 进度 | 前端轮询；SSE 接口保留 |
| 存储 | 本地 JSON 文件，无数据库 |
| 测试 | Python 独立测试脚本 + jsdom 前端测试 |

## 快速开始

### 环境要求

- Python 3.10+
- pip
- 联网环境（调用模型、MinerU 和部分前端 CDN 资源）

### 安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 已包含 Word / TXT 所需依赖（`python-docx`、`chardet`），无需再单独安装。

### 配置模型与 OCR

推荐直接打开设置页：

```text
http://localhost:8000/ui/settings.html
```

设置页会把配置保存到 `knowledge-compiler/data/settings.json`（`data/` 已被 `.gitignore` 忽略），并在接口返回时自动脱敏。

| 配置 | 用途 | 是否必须 |
|------|------|----------|
| OpenAI 兼容模型服务 | AI 骨架、节点增强、导学、复习判分 | 可选；没有模型时跳过增强，追问模式降级 |
| MinerU Token | PDF 云端 OCR | 可选；没有 Token 时使用 PyMuPDF |

> PyMuPDF 只能读取 PDF 的**文字层**；纯扫描件（整页是图片）没有 MinerU Token 时无法提取正文。
> 这类资料请在设置页配置 OCR。

解析优先级：

- 模型服务：本次上传覆盖 > 设置页默认服务商 > 环境变量 > 未配置
- MinerU Token：本次上传覆盖 > 设置页 > 环境变量 > 未配置

> 项目不会自动读取 `.env` 文件；如果使用环境变量，请在启动前 `export`（Windows 使用 `set`）。仓库不内置任何 API Key 或 MinerU Token，请通过设置页或环境变量配置。

### 安全默认

- 默认监听 `127.0.0.1`，只允许本机访问。
- CORS 默认只允许同源；跨域开发时用 `CORS_ORIGINS` 显式列出来源。
- 需要局域网访问时设置 `HOST=0.0.0.0`；需要公网访问时再设置 `KT_AUTH_PASSWORD`（HTTP Basic Auth），并建议放在 Nginx/HTTPS 后面。

### 启动服务

Windows：

```bash
start.bat
# 或
python start.py
```

macOS / Linux：

```bash
python start.py
# 或
start.command
# 或手动启动
python -m uvicorn knowledge-compiler.server:app --host 127.0.0.1 --port 8000
```

`start.bat` 与 `start.command` 都会在完成依赖检查后调用同一个 `start.py`，
因此端口、健康检查、浏览器打开和退出行为保持一致。

浏览器访问：

```text
http://localhost:8000
```

默认会跳转到统一客户端 `/ui/client.html`。

## 界面

统一客户端通过顶部场景 Bar 在五个工作区之间切换，共享同一份“当前资料”上下文：

| 场景 | Hash | 说明 |
|------|------|------|
| 首页 | `#home` | 居中“构建你的导图”、`Start` 进入解析、`概况` 右侧抽屉；`knowledge-compiler/ui/assets/bg1.png` / `bg1-dark.png` 随主题交叉淡入 |
| 解析 | `#parse` | 拖放上传、统一配置摘要、处理进度与解析报告；不再逐页询问模型 / OCR |
| 资料 | `#library` | 已完成知识树、未完成任务、导入数据包、收藏 / 删除、搜索筛选与跨场景入口 |
| 导图 | `#mindmap?file=...` | 导览 / 导图共享切换器、可折叠大纲、平衡双翼 ECharts 导图、搜索与导出 |
| AI 对话 | `#chat?file=...` | 学习 / 追问模式、当前文档切换、可折叠左右栏、引用来源与富文本回答 |
| 设置 | 右侧抽屉 | 模型服务、MinerU、学科、增强范围 |

底层仍复用：

- `/ui/overview.html?embed=1`：资料导览
- `/ui/tree_mindmap.html?embed=1`：沉浸式思维导图
- 旧入口 `homepage.html` / `upload_mindmap.html` / `browse.html` / `tree_display.html` 已移到 `_archive/2026-10-20-cleanup/legacy-ui/`，不再随运行包分发

静态资源由 FastAPI 的 `/ui` 挂载直接提供，无需 npm 构建。修改 JS/CSS 后刷新页面即可；服务端对 `/ui/` 资源做了 `no-cache` 处理，避免 HTML 与公共库版本混用。

### 界面与交互约定

- **设计系统**：组件样式与动效以 `ui/cds-theme.css` 的 CDS 令牌为准；实现保持现有 Warm Academic 视觉与交互基准。
- **共享视图切换**：导览 / 导图切换器由 `client.html` 外壳统一渲染，两个视图使用同一个 DOM、同一位置和同一套样式；切换时滑块先完成动画，再替换 iframe 内容。
- **沉浸式导图**：主节点居中，章节分成左右两翼；左翼节点定位点在文字右侧，右翼定位点在文字左侧，上下布局同理。另提供“全部从左到右 / 全部从右到左”单向排列。单册资料会把“项目根 → 源文件”包装层提升为项目根，章节直接挂在项目根下。两翼的拖拽、缩放、展开/收起重排和根节点对齐保持同步，连接线随可见节点一起更新；Retina 屏幕上 canvas 像素比上限为 2x，避免 4x 像素量拖慢大图操作。
- **可折叠面板**：导图大纲、AI 对话左侧导航和右侧知识库支持收起 / 展开；收起后主内容平滑居中，并保留 `aria-expanded`、焦点转移和折叠状态。
- **AI 对话富文本**：回答支持 Markdown 列表、嵌套列表、表格、行内 / 独立公式和代码块；公式由 KaTeX 在消息插入 DOM 后渲染，流式输出期间也会持续更新。
- **对话内文档切换**：当前文档卡片展开后直接在对话页切换资料，不跳转资源库；顶栏、左侧知识库、引用来源和对话上下文同步更新。
- **资料库管理**：收藏星标与收藏夹筛选、删除确认、导入数据包和单份导出都在资料库内完成；导图页的导出菜单复用同一数据包接口。
- **学习路径可读性**：路径不再把长标题压缩进固定宽度的 Mermaid 图。少量短标题使用卡片网格；章节较多或标题较长时自动切换为纵向编号轨道，文字保持完整字号并可换行，点击任一步直接进入对应章节导图。

## API 接口

### 资料处理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/upload` | POST | 上传文件，返回 `task_id` |
| `/api/status/{task_id}` | GET | 查询任务状态、阶段、进度和最近消息 |
| `/api/status/{task_id}/stream` | GET | SSE 进度接口；当前解析场景使用轮询，尚未接入此接口 |
| `/api/result/{task_id}` | GET | 获取最终知识树 JSON |
| `/api/report/{task_id}` | GET | 获取解析报告 |
| `/api/export/{task_id}` | GET | 导出离线导图 HTML + Markdown / JSON ZIP 数据包 |
| `/api/files` | GET | 列出已完成任务和历史树文件 |
| `/api/file/{filename}` | GET | 按文件名加载知识树 |
| `/api/tasks` | GET | 列出历史任务 |
| `/api/stats` | GET | 知识树数量与节点总数 |
| `/api/health` | GET | 健康检查 |
| `/api/update-node` | POST | 更新节点标题/正文；支持 `task_<task_id>` 任务树，并兼容旧版 enhanced 树 |
| `/api/rename` | POST | 重命名资料；传 `task_id` 更新任务与缓存，传 `filename` 更新旧本地树 |

### 资料库管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/favorites` | GET | 读取收藏夹 key 列表 |
| `/api/favorites` | POST | 收藏 / 取消收藏，body：`{key, favorite}` |
| `/api/package/{resource_key}` | GET | 导出可重新导入的数据包（manifest + tree + 可选报告 / 复习进度） |
| `/api/import-package` | POST | 导入数据包；支持 ZIP 和单棵 JSON 树 |
| `/api/delete_resource` | POST | 删除资料及其复习进度，body：`{key}` |

### 复习助手

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/review/units` | GET | 预览复习单元、章节分组、到期数量 |
| `/api/review/start` | POST | 开始一轮复习 |
| `/api/review/answer` | POST | 提交回答 |
| `/api/review/skip` | POST | 跳过当前考点 |
| `/api/review/end` | POST | 结束当前会话 |
| `/api/review/learn` | POST | 学习模式自由问答 |
| `/api/review/status/{session_id}` | GET | 获取会话快照 |
| `/api/review/progress` | GET | 获取待复测、薄弱点、今日到期和历史会话 |

`start / answer / skip / end / learn` 支持 `stream: true`，返回 NDJSON 流：

```text
{"t":"d","text":"增量文本"}
{"t":"snap","data":{...最终快照...}}
{"t":"err","error":"错误信息"}
```

### 配置

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/settings` | GET | 获取脱敏后的统一配置 |
| `/api/settings` | POST | 修改配置 |

`POST /api/settings` 支持的 `op`：

- `upsert_provider`：新增或更新模型服务商
- `delete_provider`：删除服务商
- `set_active`：设置默认服务商
- `set_general`：设置 MinerU Token 和学科
- `set_enhance`：切换增强范围
- `test_provider`：测试模型服务
- `test_mineru`：测试 MinerU Token

### 示例

上传文件：

```bash
curl -X POST http://localhost:8000/api/upload \
  -F "files=@document.pdf" \
  -F "files=@notes.docx"
```

查询进度：

```bash
curl http://localhost:8000/api/status/<task_id>
```

获取结果与报告：

```bash
curl http://localhost:8000/api/result/<task_id>
curl http://localhost:8000/api/report/<task_id>
```

开始一轮复习：

```bash
curl -X POST http://localhost:8000/api/review/start \
  -H "Content-Type: application/json" \
  -d '{"file":"task_<task_id>","max_units":10,"stream":false}'
```

## 项目结构

```text
mindmap/
├── start.py                         # 启动入口
├── start.bat                        # Windows 启动脚本
├── start.command                    # macOS 启动脚本
├── requirements.txt                 # Python 依赖
├── .env.example                     # 环境变量示例
├── README.md
├── AGENTS.md                        # 仓库级开发约定
├── .github/                         # GitHub Actions 发布工作流
│
├── scripts/                         # Windows 便携包构建与冒烟测试
├── packaging/                       # 便携包启动器与说明
│
├── knowledge-compiler/              # 主应用
│   ├── server.py                    # FastAPI 服务与全流程编排
│   ├── app_settings.py              # 统一配置中心
│   ├── structure_score.py           # 片段结构质量评分与路由
│   ├── skeleton_builder.py          # AI 骨架生成
│   ├── content_anchor.py            # 原文段落锚定与无损校验
│   ├── outline_skeleton.py          # 结构前置层：标题恢复与骨架审计
│   ├── hierarchy_repair.py          # 多编号体系层级修复
│   ├── tree_builder.py              # Markdown → 树结构
│   ├── placement_audit.py           # 主题归属审校
│   ├── structure_repair.py          # 增强前结构审校
│   ├── node_enhancer.py             # 节点增强与章节导学
│   ├── parse_report.py              # 解析报告与 token 统计
│   ├── review_engine.py             # 对话复习、判分与长期调度
│   ├── parser/
│   │   ├── pdf_splitter.py          # PDF 按目录 / 内容 / 页数拆分
│   │   ├── text_extractor.py        # Word / TXT → Markdown
│   │   └── llm_structurer.py        # 旧版 LLM 结构化（保留）
│   ├── mineru_adapter/
│   │   ├── client.py                # MinerU API 客户端
│   │   └── convert.py               # 批量 OCR 转换
│   └── ui/
│       ├── client.html              # 统一客户端壳层与五个场景
│       ├── client.css               # 客户端布局、CDS 主题与响应式样式
│       ├── client.js                # 场景路由、活动资料、上传与跨场景交互
│       ├── client-embed.css         # iframe 嵌入式页面共享 CDS 样式 / Hub Rail
│       ├── claude-pages.css         # 导览页、导图页的 CDS 画布样式
│       ├── cds-theme.css            # CDS 令牌与动效
│       ├── embed-mode.js            # iframe embed 模式标记
│       ├── overview.html            # 资料导览
│       ├── tree_mindmap.html        # 平衡双翼思维导图
│       ├── settings.html            # 设置
│       ├── review_widget.js         # 对话复习、流式消息与 Markdown / KaTeX 渲染
│       ├── common.js                # 共享工具、Markdown/表格/公式后处理
│       ├── theme.css                # 亮色 / 暗色主题
│       └── assets/
│           ├── bg1.png              # 首页浅色背景
│           └── bg1-dark.png         # 首页暗色背景
│
├── docs/                            # 当前架构与设计说明
└── _archive/                        # 本地历史归档（gitignored，不参与运行）
```

### 归档与干净拷贝

`_archive/` 只保留本机历史资料，并已加入 `.gitignore`。当前有两个归档批次：

| 目录 | 内容 |
| --- | --- |
| `2026-10-20-cleanup/` | 旧版页面、早期设计走查、UI 快照、外部 CDS 参考仓库与开发期脚本 |
| `2026-10-22-github-cleanup/` | 演示视频工程、Remotion/PPT 构建产物、演示脚本、历史实施记录与测试报告 |

干净拷贝时排除归档区、本机运行数据和本机配置：

```bash
rsync -a \
  --exclude '_archive/' \
  --exclude 'knowledge-compiler/data/' \
  --exclude '.env' \
  --exclude '__pycache__/' \
  --exclude 'node_modules/' \
  --exclude '.DS_Store' \
  --exclude '.claude/' \
  ./ /path/to/clean-copy/
```

`scripts/build_windows_portable.py` 已内置运行文件白名单，可作为打包范围的权威参照。确认归档内容不再需要后，可直接删除整个 `_archive/`。

### 文档

- `docs/AI对话机制与配置说明.md`：对话、判分与模型配置
- `docs/统一配置中心.md`：模型服务 / MinerU / 学科 / 增强范围
- `docs/解析报告面板.md`：解析报告的统计口径与前端展示
- `docs/MACOS_BUILD.md`：macOS DMG 构建、冒烟测试与签名边界
- `knowledge-compiler/docs/`：当前保留的解析、结构前置层与审校技术说明

## 数据目录

```text
knowledge-compiler/data/
├── settings.json                    # 统一配置（gitignored）
├── favorites.json                   # 资料库收藏 key 列表（gitignored）
├── _temp_uploads/
│   ├── <task_id>/
│   │   ├── uploads/                 # 原始上传文件
│   │   ├── chapters/                # PDF 拆分结果
│   │   ├── markdown/                # 提取后的 Markdown
│   │   ├── trees/                   # 单文件树 JSON
│   │   ├── json/
│   │   │   └── knowledge_tree.json  # 最终知识树
│   │   ├── report.json              # 解析报告
│   │   └── task_state.json          # 任务状态（重启后可读）
│   └── _enhance_progress/           # AI 增强断点
├── _cache/                          # 内容级缓存（按文件+设置哈希）
└── review/
    ├── <file_key>/
    │   └── progress.json            # 复习进度
    └── sessions/
        └── *.json                   # 复习会话快照
```

知识树、复习进度和任务状态都是本地 JSON，不依赖数据库。等待中的任务运行态仍在进程内，服务重启后状态会标记为中断；已完成结果可从磁盘恢复。相同文件 + 相同模型/OCR 设置会命中 `_cache/`。

## 降级策略

| 环节 | 正常路径 | 降级路径 |
|------|----------|----------|
| PDF OCR | MinerU 云端识别 | PyMuPDF 本地提取 |
| 结构建树 | regex 解析；结构不良片段走 AI 骨架 | 无模型时结构不良片段按段落保留原文；regex 路线不受影响 |
| 节点增强 | 模型生成摘要 / 关键词 / 考点 / 图表 | 重试后使用确定性兜底内容 |
| 章节导学 | LLM 生成 | 确定性静态导学文案 |
| 追问复习 | LLM 判分与自然对话 | 确定性判分 + 模板话术 |
| 学习模式 | LLM 基于原文回答 | 需要模型；未配置时明确提示 |
| 解析报告 | 活任务完整报告 | 磁盘报告；再退化到从知识树现算部分统计 |

## 测试

测试脚本是独立的，不依赖 pytest 配置。

基础处理：

```bash
python test_document_processing.py
python test_advanced_processing.py
```

结构、增强、报告与复习：

```bash
cd knowledge-compiler
python test_hierarchy_recovery.py
python test_outline_skeleton.py
python test_placement_audit.py
python test_structure_repair.py
python test_parse_report.py
python test_review_engine.py
python test_guide_generation.py
python test_structure_generalize.py
python test_all_numbering.py
python test_app_settings.py
python test_configured_provider_e2e.py
python test_six_fixes.py
python test_image_refs.py
python test_document_title.py
python test_library_packages.py
```

全链路深度回归（结构层改动后建议运行）：

```bash
cd knowledge-compiler
RECOVERY_SEEDS=400 python test_hierarchy_recovery.py
```

前端 jsdom 测试需要指定 `jsdom` 安装位置：

```bash
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_client.js
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_composer.js
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_lightbox.js
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_mindmap.js
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_overview.js
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_settings.js
JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_image_refs.js
```

当前前端 jsdom 回归基线：7 个测试文件、428 项全部通过。覆盖统一客户端场景与资料库操作、导图双翼 / 单向排列与展开收起后的连接线重排、设置、导览学习路径、富文本 composer、图片引用和灯箱。资料库 API 另有 `test_library_packages.py` 的收藏 / 导入 / 导出 / 删除回归测试。

大体积导图本机实测：`外国教育史`（1,591 节点）、`333中国教育史·完整版`（1,036 节点）、`教育学原理·完整解析版`（1,014 节点）均可正常加载、平衡排布、缩放、平移和展开/收起；1,591 节点全部展开的同步重排约 172ms，缩放交互约 2ms。

## 当前实现说明与已知限制

1. **普通 PDF 不会拆分**：只有大于 200MB 才触发 PDF 拆分；小文件整本处理。
2. **解析场景使用轮询**：`/api/status/{task_id}/stream` 存在，但当前解析场景没有调用 SSE。
3. **任务运行态仍以内存为主**：`task_state.json` 会持久化阶段/进度/错误，服务重启后遗留 `processing` 会标为中断；但正在执行的线程不会自动续跑。
4. **节点编辑只更新树 JSON**：`task_<task_id>` 已支持，但编辑后不会自动重算解析报告、导学、复习单元或缓存元数据；需要时重新跑一遍管线。
5. **默认未配置凭证**：模型服务与 MinerU Token 留空时，系统按降级策略运行；需要 AI 功能时请在设置页或环境变量中配置。
6. **默认无鉴权但只监听本机**：局域网/公网部署请设置 `KT_AUTH_PASSWORD`，并配置 `CORS_ORIGINS`、HTTPS 和反向代理。
7. **部分前端库依赖 CDN**：ECharts、GSAP、KaTeX、Mermaid 和 Google Fonts 从 CDN 加载；KaTeX 未加载时公式会保留原文，但列表和表格仍由本地渲染器处理。导出包也不是完全离线，首次打开需要网络。
8. **缓存有版本边界**：修改解析/增强语义后应提升 `CACHE_VERSION`，否则相同输入可能继续命中旧结果。
9. **导学投影修复只对新处理资料生效**：旧任务树里已经生成在包装节点上的导学不会自动迁移；需要重新解析一次。
10. **图片只保留引用、不渲染**：MinerU 的 `images/<hash>` 会从正文移到节点的 `images` 元数据，不再显示路径；当前版本没有图片资产服务，因此图片本身不会显示。
11. **资料数据包不包含原始文件**：数据包包含知识树、manifest、可选的解析报告和复习进度，不包含原始 PDF / DOCX / TXT；导入后可继续浏览、对话和复习，但不能从数据包重新执行 OCR。

## 环境变量

项目实际读取的环境变量：

```dotenv
# 服务器
HOST=127.0.0.1
PORT=8000

# 安全（可选）
KT_AUTH_USER=admin
KT_AUTH_PASSWORD=
CORS_ORIGINS=

# 模型服务
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.deepseek.com/chat/completions
MODEL=deepseek-v4-flash

# MinerU
MINERU_TOKEN=your_mineru_token

# 可选：自定义统一配置文件位置
KT_SETTINGS_PATH=/path/to/settings.json
```

说明：

- 项目不会自动加载 `.env`，需要在启动前 `export` 或使用设置页。
- `MAX_CONCURRENT`、`NO_SPLIT_SIZE_MB`、`API_TIMEOUT`、`MAX_TEXT_CHUNK_CHARS` 目前是代码内常量，不从环境变量读取。
- `RECOVERY_SEEDS` 仅用于结构恢复测试。

## License

MIT
