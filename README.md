# Knowledge Tree

将文档自动解析为结构化知识树，通过 AI 增强后生成交互式思维导图。

## 功能特性

- **多格式上传** — PDF、Word (.docx)、纯文本 (.txt)、PPTX、XLSX、HTML、CSV、EPUB 等 20+ 格式，支持拖拽与批量上传
- **智能 PDF 拆分** — 按目录 / 内容 / 固定页数自动拆分为章节
- **MarkItDown 本地解析** — 默认使用 Microsoft MarkItDown 引擎，无需 API 密钥，支持所有常见文档格式
- **MinerU 云端 OCR**（可选） — 配置 MinerU API 密钥后启用增强 PDF 解析，适合扫描件和复杂排版
- **无结构文本处理** — 无标题文档通过 DeepSeek LLM 自动识别层级结构
- **AI 增强** — 为每个节点生成摘要、关键词、考点、Mermaid 流程图、表格
- **多文档合并** — 多文件上传时自动合并为一棵知识树
- **实时进度** — SSE 推送处理进度，前端实时展示
- **交互式思维导图** — ECharts 渲染，支持拖拽、缩放、展开/折叠、多种布局
- **节点编辑** — 点击节点查看详情，支持在线编辑标题和内容
- **暗色 / 亮色主题** — 全局主题切换，所有界面适配
- **文件浏览** — 浏览已生成的知识树，支持搜索和筛选
- **导出** — 一键导出为 ZIP 包，可离线查看

## 处理流程

```
上传文件
  │
  ├─ PDF ──→ 按目录/内容拆分 ──→ MinerU OCR（需 API 密钥）──→ Markdown
  │                                      ↓ (无密钥或失败)
  │                               MarkItDown 本地解析
  │
  ├─ Word/PPTX/XLSX/HTML/CSV/EPUB ──→ MarkItDown ──→ Markdown
  │
  └─ TXT  ──→ MarkItDown ──→ Markdown
                               │
                  ┌─────────────┴─────────────┐
                  │ 有标题结构？               │ 无标题结构？
                  │                           │
            Markdown 树解析             DeepSeek LLM 结构化
                  │                           │
                  └──────────┬────────────────┘
                             │
                      多文档树合并
                             │
                 AI 增强（摘要/关键词/考点/图表）
                             │
                    树结构清理与优化
                             │
                ECharts 交互式思维导图
```

## 技术栈

| 层级          | 技术                           |
| ----------- | ---------------------------- |
| 后端框架        | FastAPI + Uvicorn            |
| 文档解析（默认）    | MarkItDown（本地，支持 20+ 格式）     |
| PDF OCR（可选） | MinerU 云端 API                |
| AI 增强       | DeepSeek API (OpenAI 兼容)     |
| 前端          | 原生 HTML / CSS / JS + ECharts |
| 进度推送        | Server-Sent Events (SSE)     |

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 API 密钥

```bash
cp .env.example .env
# 编辑 .env 填入你的 API 密钥
```

| 密钥                   | 用途            | 是否必须                     |
| -------------------- | ------------- | ------------------------ |
| **DeepSeek API Key** | 文本结构化 + AI 增强 | 可选（无则跳过 AI 功能）           |
| **MinerU API Key**   | 增强 PDF 云端 OCR | 可选（无则使用 MarkItDown 本地解析） |

> 没有配置任何 API 密钥也可以运行 — MarkItDown 会自动解析文档并基于标题结构构建知识树。

### 启动服务

**Windows：**

```bash
start.bat
# 或
python start.py
```

**macOS / Linux：**

```bash
python start.py
# 或手动启动
uvicorn knowledge-compiler.server:app --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000`。

## 界面

| 页面  | 路径                        | 说明                      |
| --- | ------------------------- | ----------------------- |
| 首页  | `/ui/homepage.html`       | 入口页面，展示统计信息             |
| 上传  | `/ui/upload_mindmap.html` | 上传文件、配置 API、查看处理进度与思维导图 |
| 浏览  | `/ui/browse.html`         | 浏览已生成的知识树文件             |
| 查看器 | `/ui/tree_mindmap.html`   | 独立思维导图查看器，支持编辑与导出       |

## API 接口

| 端点                             | 方法   | 说明                |
| ------------------------------ | ---- | ----------------- |
| `/api/upload`                  | POST | 上传文件，返回 `task_id` |
| `/api/status/{task_id}`        | GET  | 查询任务状态与进度         |
| `/api/status/{task_id}/stream` | GET  | SSE 实时进度推送        |
| `/api/result/{task_id}`        | GET  | 获取知识树 JSON        |
| `/api/export/{task_id}`        | GET  | 导出 ZIP 包          |
| `/api/update-node`             | POST | 更新节点标题 / 内容       |
| `/api/files`                   | GET  | 列出已生成的知识树文件       |
| `/api/file/{filename}`         | GET  | 获取指定文件内容          |
| `/api/tasks`                   | GET  | 列出所有任务            |
| `/api/stats`                   | GET  | 获取统计数据            |
| `/api/health`                  | GET  | 健康检查              |

### 示例

```bash
# 上传文件
curl -X POST http://localhost:8000/api/upload \
  -F "files=@document.pdf" \
  -F "files=@notes.docx"

# 查询进度（SSE）
curl http://localhost:8000/api/status/{task_id}/stream
```

## 项目结构

```
KnowledgeTree/
├── start.py                         # 启动入口
├── start.bat                        # Windows 启动脚本
├── start.command                    # macOS 启动脚本
├── requirements.txt                 # Python 依赖
├── .env.example                     # 环境变量模板
│
└── knowledge-compiler/              # 主应用
    ├── server.py                    # FastAPI 服务器（全流程编排）
    ├── config.py                    # 配置模块
    ├── tree_builder.py              # Markdown → 树 JSON 构建器
    ├── node_enhancer.py             # AI 增强（摘要/关键词/考点/图表）
    ├── hierarchy_repair.py          # 层级结构修复
    │
    ├── markitdown/                  # MarkItDown 文档解析引擎（默认）
    │   ├── __init__.py              # 公共 API
    │   ├── _markitdown.py           # 核心调度器
    │   ├── converters/              # 20+ 格式转换器
    │   └── converter_utils/         # 工具函数
    │
    ├── parser/
    │   ├── markitdown_adapter.py    # MarkItDown 适配器（统一入口）
    │   ├── pdf_splitter.py          # PDF 按目录/内容/页数拆分
    │   ├── text_extractor.py        # 文本分块工具
    │   └── llm_structurer.py        # LLM 文本结构化
    │
    ├── mineru_adapter/
    │   ├── client.py                # MinerU 云端 API 客户端（可选）
    │   └── convert.py               # 批量 PDF OCR 转换
    │
    └── ui/
        ├── homepage.html            # 首页
        ├── upload_mindmap.html      # 上传 + 思维导图 + 搜索
        ├── browse.html              # 文件浏览
        ├── tree_mindmap.html        # 独立思维导图查看器
        ├── tree_display.html        # 树状浏览器
        ├── theme.css                # 主题样式（亮/暗）
        └── common.js                # 共享工具函数
```

## 降级策略

| 环节       | 完整功能               | 无 API 时的回退            |
| -------- | ------------------ | --------------------- |
| 文档解析     | MinerU 云端 OCR（PDF） | MarkItDown 本地解析（所有格式） |
| 文本结构化    | DeepSeek LLM 识别层级  | 基于正则的标题模式匹配           |
| 节点 AI 增强 | 摘要/关键词/考点/流程图      | 跳过增强，保留原始树            |

## 环境变量

在 `.env` 文件中设置（完整模板见 `.env.example`）：

```dotenv
# API
OPENAI_API_KEY=sk-your_key_here
OPENAI_BASE_URL=https://api.deepseek.com/chat/completions
MODEL=deepseek-v4-flash
API_TIMEOUT=30

# MinerU（可选，增强 PDF 解析）
MINERU_API_KEY=

# 服务器
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=info

# 文件处理
MAX_TEXT_CHUNK_CHARS=8000
NO_SPLIT_SIZE_MB=200

# 并发控制
MAX_CONCURRENT=5
```

## License

MIT
