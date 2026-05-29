# KnowledgeTree

将文档（PDF/Word/TXT）自动解析、结构化，并通过 AI 增强为交互式知识图谱（思维导图）。

## 功能特性

- **多格式支持** — PDF、Word (.docx)、纯文本 (.txt)，拖拽即上传
- **智能 PDF 拆分** — 按目录/内容/固定页数自动拆分为章节
- **OCR 文字提取** — MinerU 云端 OCR，失败时自动回退到 PyMuPDF 本地提取
- **标题结构解析** — 从 Markdown 标题层级自动构建树状 JSON
- **无结构文本处理** — 无标题的文档通过 DeepSeek LLM 自动识别层级结构
- **AI 增强** — 为每个节点生成摘要、关键词、考点、Mermaid 流程图、表格
- **多文档合并** — 多文件上传时自动合并为一棵知识树，跨文档上下文衔接
- **实时进度** — SSE 推送处理进度，前端实时展示
- **交互式可视化** — ECharts 思维导图，支持拖拽、缩放、展开/折叠、多布局
- **节点编辑** — 点击节点查看详情，支持在线编辑标题和内容
- **导出** — 一键导出为 ZIP 包，可离线查看

## 处理流程

```
上传文件
  │
  ├─ PDF ──→ 按目录/内容拆分 ──→ MinerU OCR ──→ Markdown
  │                                      ↓ (失败)
  │                               PyMuPDF 本地提取
  │
  ├─ Word ──→ python-docx 转换 ──→ Markdown
  │
  └─ TXT  ──→ 编码检测 + 格式转换 ──→ Markdown
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

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| PDF 解析 | PyMuPDF (fitz) |
| Word 解析 | python-docx |
| OCR 服务 | MinerU 云端 API |
| AI 增强 | DeepSeek API (OpenAI 兼容) |
| 编码检测 | chardet |
| 前端 | 原生 HTML/CSS/JS + ECharts |
| 进度推送 | Server-Sent Events (SSE) |

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装依赖

```bash
pip install -r requirements.txt
```

> `requirements.txt` 包含核心依赖。完整运行还需要 `python-docx` 和 `chardet`：
> ```bash
> pip install python-docx chardet
> ```

### 配置 API 密钥

```bash
cp .env.example .env
# 编辑 .env 填入你的 API 密钥
```

或在启动后通过浏览器界面的「设置」面板配置：

| 密钥 | 用途 | 是否必须 |
|------|------|----------|
| **DeepSeek API Key** | 文本结构化 + AI 增强 | 可选（无则跳过 AI 功能） |
| **MinerU Token** | PDF 云端 OCR | 可选（无则使用本地提取） |

### 启动服务

**Windows：**
```bash
# 方式一：双击 start.bat
# 方式二：
python start.py
```

**macOS / Linux：**
```bash
python start.py
# 或手动启动
uvicorn knowledge-compiler.server:app --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000`。

## 环境变量

在 `.env` 文件或系统环境变量中设置：

```dotenv
# ── API 配置 ──
OPENAI_API_KEY=sk-your_key_here          # DeepSeek API 密钥
OPENAI_BASE_URL=https://api.deepseek.com/chat/completions
MODEL=deepseek-v4-flash                  # 模型名称
API_TIMEOUT=30                           # API 超时（秒）

# ── 服务器配置 ──
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=info

# ── 文件处理 ──
MAX_TEXT_CHUNK_CHARS=8000                # 文本单次切分最大字符数
NO_SPLIT_SIZE_MB=200                     # PDF 不拆分的大小阈值（MB）

# ── 并发控制 ──
MAX_CONCURRENT=5                         # 最大并发 LLM 请求数
```

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/api/upload` | POST | 上传文件，返回 `task_id` |
| `/api/status/{task_id}` | GET | 查询任务状态与进度 |
| `/api/status/{task_id}/stream` | GET | SSE 实时进度推送 |
| `/api/result/{task_id}` | GET | 获取知识树 JSON |
| `/api/export/{task_id}` | GET | 导出 ZIP 包 |
| `/api/update-node` | POST | 更新节点标题/内容 |
| `/api/health` | GET | 健康检查 |

### 上传示例

```bash
curl -X POST http://localhost:8000/api/upload \
  -F "files=@document.pdf" \
  -F "files=@notes.docx"
```

### 查询进度

```bash
# 普通轮询
curl http://localhost:8000/api/status/{task_id}

# SSE 实时推送
curl http://localhost:8000/api/status/{task_id}/stream
```

## 项目结构

```
KnowledgeTree/
├── start.py                        # 启动入口
├── start.bat                       # Windows 启动脚本
├── start.command                   # macOS 启动脚本
├── requirements.txt                # Python 依赖
├── .env.example                    # 环境变量模板
├── nginx.conf                      # Nginx 反向代理配置
│
└── knowledge-compiler/             # 主应用包
    ├── server.py                   # FastAPI 服务器（全流程编排）
    ├── tree_builder.py             # Markdown → 树 JSON 构建器
    ├── node_enhancer.py            # AI 增强（摘要/关键词/考点/图表）
    ├── fix_tree_hierarchy.py       # 层级结构修复
    ├── rebuild_and_merge.py        # 重建与合并
    ├── main.py                     # CLI：PDF 拆分
    │
    ├── parser/
    │   ├── pdf_splitter.py         # PDF 按目录/内容/页数拆分
    │   ├── text_extractor.py       # Word/TXT → Markdown 转换
    │   └── llm_structurer.py       # LLM 文本结构化
    │
    ├── mineru_adapter/
    │   ├── client.py               # MinerU 云端 API 客户端
    │   └── convert.py              # 批量 PDF OCR 转换
    │
    └── ui/
        ├── upload_mindmap.html     # 主界面（上传 + 思维导图 + 搜索）
        ├── tree_mindmap.html       # 独立思维导图查看器
        ├── tree_display.html       # 树状浏览器
        └── theme.css               # 主题样式
```

## 降级策略

项目在每个外部依赖环节都设计了本地回退：

| 环节 | 完整功能 | 无 API 时的回退 |
|------|----------|-----------------|
| PDF OCR | MinerU 云端识别 | PyMuPDF 本地文本提取 |
| 文本结构化 | DeepSeek LLM 识别层级 | 基于正则的标题模式匹配 |
| 节点 AI 增强 | 摘要/关键词/考点/流程图 | 跳过增强，保留原始树 |

**没有配置任何 API 密钥也可以运行** — 上传带有标题结构的 PDF/Word/TXT 文件，系统会基于标题自动构建知识树。

## 测试

```bash
# 单元测试
python test_document_processing.py

# 集成测试
python test_advanced_processing.py

# 端到端演示
python demo_complete_pipeline.py

# 部署检查
python check_deployment.py
```

## License

MIT
