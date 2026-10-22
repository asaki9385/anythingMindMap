"""
通用 Knowledge Node Enhancer (并行版 + 全局上下文)
每个tree JSON文件独立处理，但增强时注入前后章节上下文保证衔接
最终合并输出一个JSON
"""

import json
import os
import re
import asyncio
import httpx
from app_settings import chat_body_extras, chat_headers
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL           = "deepseek-v4-flash"

SUBJECT_CONFIG = {
    "name": "教育学",
    "exam": "333教育综合考研",
}

ENHANCE_LEVELS = {1: True, 2: True, 3: True, 4: True, 5: False}
MAX_CONCURRENT  = 5
REQUEST_DELAY   = 0.3

PROFILE_RULES = {
    "exam_outline": ["考纲", "大纲", "提纲", "考点", "真题", "考试", "背诵", "重点"],
    "book_interpretation": ["导读", "解读", "评析", "精读", "书评", "作者", "著作", "文本"],
    "textbook": ["原理", "教程", "教材", "理论", "概论", "基础", "导论", "方法论"],
}

ROLE_PATTERNS = [
    (r'衔接|过渡', "bridge"),
    (r'^第[一二三四五六七八九十百\d]+章', "chapter"),
    (r'^第[一二三四五六七八九十百\d]+节', "section"),
    (r'^知识点', "knowledge_point"),
    (r'案例|例题|例证', "case"),
    (r'方法|步骤|路径|策略', "method"),
    (r'比较|对比|区别|联系', "comparison"),
    (r'定义|概念|内涵', "concept"),
]

# ────────────────────────────────────────────
# 全局上下文构建
# ────────────────────────────────────────────

def load_all_trees(tree_dir: str) -> list:
    """Load all tree JSONs in order, return list of (filename, tree_dict)."""
    files = sorted(f for f in os.listdir(tree_dir) if f.endswith(".json"))
    trees = []
    for f in files:
        path = os.path.join(tree_dir, f)
        with open(path, encoding='utf-8') as fh:
            trees.append((f, json.load(fh)))
    return trees

def build_global_index(trees: list) -> list:
    """Build ordered list of top-level chapter info for context."""
    index = []
    for filename, tree in trees:
        for child in tree.get('children', []):
            title = child.get('title', '')
            content_preview = (child.get('content', '') or '')[:200]
            index.append({
                "file": filename,
                "title": title,
                "level": child.get('level', 0),
                "preview": content_preview,
            })
    return index

def get_surrounding_context(global_index: list, current_file: str, current_title: str) -> str:
    """Get previous and next chapter context for a given node."""
    # Find current position in index
    pos = -1
    for i, item in enumerate(global_index):
        if item['file'] == current_file and item['title'] == current_title:
            pos = i
            break

    if pos == -1:
        return ""

    parts = []

    # Previous chapter
    if pos > 0:
        prev = global_index[pos - 1]
        parts.append(f"【前文衔接】上一节: {prev['title']} (来自{prev['file']})")
        if prev['preview']:
            parts.append(f"  摘要: {prev['preview'][:150]}")

    # Next chapter
    if pos < len(global_index) - 1:
        nxt = global_index[pos + 1]
        parts.append(f"【后文衔接】下一节: {nxt['title']} (来自{nxt['file']})")
        if nxt['preview']:
            parts.append(f"  摘要: {nxt['preview'][:150]}")

    return '\n'.join(parts)


def _sample_tree_text(tree: dict, max_chars: int = 2500) -> str:
    parts = [tree.get("title", "")]

    def _walk(node: dict):
        if len(" ".join(parts)) >= max_chars:
            return
        title = node.get("title", "")
        content = (node.get("content", "") or "")[:180]
        if title:
            parts.append(title)
        if content:
            parts.append(content)
        for child in node.get("children", [])[:4]:
            _walk(child)

    for child in tree.get("children", [])[:6]:
        _walk(child)
    return "\n".join(parts)[:max_chars]


def detect_document_profile(tree: dict) -> dict:
    """Detect the most likely content style so prompts can adapt automatically."""
    text = _sample_tree_text(tree)
    scores = {name: 0 for name in PROFILE_RULES}
    for profile_name, keywords in PROFILE_RULES.items():
        for keyword in keywords:
            scores[profile_name] += text.count(keyword)

    profile_name = max(scores, key=scores.get)
    if scores[profile_name] == 0:
        profile_name = "generic"

    profiles = {
        "exam_outline": {
            "name": "exam_outline",
            "label": "考试提纲",
            "summary_focus": "提炼考纲重点、命题倾向、记忆抓手和复习顺序",
            "keyword_focus": "优先输出高频考点词、易混概念、答题术语",
            "exam_focus": "考点要贴近考试题型、频率和答题方式",
        },
        "book_interpretation": {
            "name": "book_interpretation",
            "label": "书籍解读",
            "summary_focus": "概括作者论证主线、章节意图、观点递进与前后呼应",
            "keyword_focus": "优先输出核心概念、作者观点、章节主旨词",
            "exam_focus": "考点可转化为阅读理解、论述题、观点辨析题",
        },
        "textbook": {
            "name": "textbook",
            "label": "理论教材",
            "summary_focus": "突出概念定义、逻辑结构、知识框架和应用边界",
            "keyword_focus": "优先输出概念、理论、方法、模型",
            "exam_focus": "考点聚焦概念辨析、框架记忆和应用分析",
        },
        "generic": {
            "name": "generic",
            "label": "通用资料",
            "summary_focus": "提炼主题、结构、要点及其逻辑关系",
            "keyword_focus": "输出最能代表内容主题的稳定关键词",
            "exam_focus": "如果适合考试化表达再给考点，否则保持通用学习导向",
        },
    }
    return profiles[profile_name]


def infer_node_role(node: dict) -> str:
    title = node.get("title", "").strip()
    for pattern, role in ROLE_PATTERNS:
        if re.search(pattern, title):
            return role

    children = node.get("children", [])
    content = node.get("content", "").strip()
    if children and not content:
        return "outline"
    if content and len(content) > 220:
        return "explanation"
    return "concept"


def describe_children_structure(node: dict) -> str:
    children = node.get("children", [])
    if not children:
        return "当前节点暂无子节点，按单点内容概括即可。"

    child_titles = [c.get("title", "") for c in children[:8]]
    order_hint = "并列展开"
    if any(re.match(r'^第[一二三四五六七八九十百\d]+', title) for title in child_titles):
        order_hint = "章节递进"
    elif any(re.match(r'^\d+[\.、]', title) for title in child_titles):
        order_hint = "编号分点"
    elif any("比较" in title or "对比" in title for title in child_titles):
        order_hint = "对比分析"

    return f"子节点结构倾向: {order_hint}；子节点示例: {' / '.join(child_titles[:5])}"

# ────────────────────────────────────────────
# Prompt
# ────────────────────────────────────────────

def build_prompt(node: dict, subject_config: dict, context_text: str,
                 surrounding_ctx: str = "", document_profile: dict | None = None) -> str:
    document_profile = document_profile or detect_document_profile({"title": node.get("title", ""), "children": [node]})
    node_role = infer_node_role(node)
    structure_guidance = describe_children_structure(node)
    ctx_block = ""
    if surrounding_ctx:
        ctx_block = f"\n## 上下文衔接\n{surrounding_ctx}\n"

    content_len = len(context_text)
    if content_len < 500:
        summary_guide = "50-80字，提炼核心论点和关键概念名称"
    elif content_len > 1000:
        summary_guide = "150-200字，涵盖核心论点、关键细节和概念名称"
    else:
        summary_guide = "100-150字，抓住核心论点和关键细节"

    return f"""你是{subject_config['name']}学科专家，专为{subject_config['exam']}备考服务。

## 知识节点
标题: {node['title']}
层级: L{node['level']}
资料类型: {document_profile['label']}
节点角色: {node_role}
结构提示: {structure_guidance}
{ctx_block}
## 内容
{context_text[:2000]}

## 输出要求
严格输出JSON，不要输出任何其他内容:
{{
  "summary": "{summary_guide}。避免'本章介绍了...'这类空话，必须包含具体概念名称",
  "keywords": [
    {{"term": "核心术语", "context": "该术语在本文中的含义或作用，一句话说明"}}
  ],
  "highlights": [
    {{"text": "从原文中提取的关键片段（20-60字）", "importance": "high", "type": "definition/theory/argument/example/formula/method"}}
  ],
  "exam_points": [
    {{"point": "考点描述", "type": "选择题/材料分析题/论述题/阅读理解题", "frequency": "高频/中频/低频"}}
  ],
  "mermaid": "graph TD\\n    A[概念] --> B[特征]\\n    A --> C[分类] (可选，适合用流程图表达时生成，否则留空字符串)",
  "tables": ["| 分类 | 特点 | 示例 |\\n|------|------|------|\\n| ... | ... | ... |" ],
  "node_role": "chapter/section/knowledge_point/concept/method/case/comparison/bridge/outline/explanation",
  "structure_hint": "总分/并列/递进/对比/因果/时间线/桥接"
}}
- summary重点: {document_profile['summary_focus']}
- keywords重点: {document_profile['keyword_focus']}。每个关键词必须是文中的核心术语，附带一句话context解释。输出3-6个对象
- highlights: 从原文中提取3-8个关键片段，每个20-60字。importance: high=必须掌握，medium=重要。type标注片段类型
- exam_points重点: {document_profile['exam_focus']}
- exam_points: 0-3个；如果内容明显不是考试资料，也允许输出空数组
- mermaid: 如果该知识点适合用流程图/思维导图表达（如分类关系、因果链、发展脉络），生成mermaid flowchart代码（使用graph TD或graph LR），否则输出空字符串""。中文节点文本不要加引号，直接写 A[教育的定义]
- tables: 如果该知识点适合用表格呈现（如多维对比、分类汇总、属性列举），生成markdown表格字符串数组，否则输出空数组[]。表格使用markdown管道符格式
- mermaid和tables的内容必须是该节点本身的知识，不要重复summary
- structure_hint要反映本节点更适合如何组织呈现
- summary中如果该节点位于章节开头或结尾，应体现与前后内容的逻辑过渡"""

# ────────────────────────────────────────────
# 节点内容提取
# ────────────────────────────────────────────

def get_context_text(node: dict) -> str:
    children = node.get('children', [])
    if not children:
        return node.get('content', '') or node.get('title', '')

    parts = ["本节包含以下内容:"]
    own = node.get('content', '')
    if own:
        parts.insert(0, f"节点内容: {own[:300]}\n")
    for child in children:
        summary = child.get('summary', '')
        content = child.get('content', '')[:150]
        line = f"- {child['title']}"
        if summary:
            line += f": {summary[:80]}"
        elif content:
            line += f": {content}"
        parts.append(line)
    return '\n'.join(parts)

def should_enhance(node: dict) -> bool:
    if not ENHANCE_LEVELS.get(node.get('level', 99), False):
        return False
    if node.get('summary'):
        return False
    if not node.get('children') and not node.get('content', '').strip():
        return False
    return True

def collect_postorder(node: dict, result: list):
    for child in node.get('children', []):
        collect_postorder(child, result)
    if should_enhance(node):
        result.append(node)

# ────────────────────────────────────────────
# 失败分类 (Failure Taxonomy)
# 借鉴 living-book-builder：重试决策机械化，管线永不卡死
# ────────────────────────────────────────────

class EnhanceError(Exception):
    """Enhancement failure with a mechanical classification."""
    def __init__(self, kind: str, message: str = ""):
        self.kind = kind
        super().__init__(message or kind)

RETRYABLE_KINDS = {
    'json_parse', 'empty_response', 'timeout', 'rate_limit', 'provider_error'
}

def classify_http_failure(status_code: int) -> str:
    if status_code == 429:
        return 'rate_limit'
    return 'provider_error'

def classify_exception(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return 'timeout'
    return 'provider_error'

# ────────────────────────────────────────────
# 静态兜底 (Deterministic Fallback)
# 节点多次失败后用确定性内容填充，标记 fallback: true
# ────────────────────────────────────────────

def apply_node_fallback(node: dict, kind: str = '') -> None:
    """Fill a node with deterministic degraded content. Never raises."""
    children = node.get('children', [])
    if not node.get('summary'):
        if children:
            child_titles = '、'.join(c.get('title', '')[:20] for c in children[:5])
            node['summary'] = f"本部分包含：{child_titles}。"
        else:
            content = (node.get('content') or '').strip()
            node['summary'] = (content[:100] + '…') if len(content) > 100 else (content or '暂无摘要。')
    if not node.get('keywords'):
        node['keywords'] = []
    if not node.get('exam_points'):
        node['exam_points'] = []
    if 'mermaid' not in node:
        node['mermaid'] = ''
    if 'tables' not in node:
        node['tables'] = []
    node['fallback'] = True
    node['fallback_reason'] = kind
    print(f"  FALLBACK [{kind}] {node.get('title', '')[:30]}")

# ────────────────────────────────────────────
# 单节点 API 调用
# ────────────────────────────────────────────

def parse_enhance_response(raw: str) -> dict:
    """Parse the LLM JSON response. Raises EnhanceError on classified failure."""
    raw = raw.strip()
    if not raw:
        raise EnhanceError('empty_response')
    # prompt_leak: model echoed the prompt instead of answering
    head = raw[:300].lower()
    if '你是' in raw[:80] or 'system' in head[:40] or '<think' in head:
        raise EnhanceError('prompt_leak')
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    # Tolerate reasoning preamble: find the first JSON object
    start = raw.find('{')
    if start > 0:
        raw = raw[start:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise EnhanceError('json_parse')


async def enhance_one(client: httpx.AsyncClient, node: dict,
                      semaphore: asyncio.Semaphore,
                      surrounding_ctx: str = "",
                      max_retries: int = 2,
                      base_url: str = None,
                      api_key: str = None,
                      model: str = None,
                      usage=None) -> None:
    """Enhance one node with retry + failure classification + static fallback."""
    url = base_url or OPENAI_BASE_URL
    key = api_key or OPENAI_API_KEY
    mdl = model or MODEL
    last_kind = ''
    for attempt in range(1, max_retries + 1):
        async with semaphore:
            await asyncio.sleep(REQUEST_DELAY)
            prompt = build_prompt(node, SUBJECT_CONFIG, get_context_text(node), surrounding_ctx)
            payload = {
                "model": mdl,
                "max_tokens": 1500,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
                **chat_body_extras(url),
            }
            try:
                resp = await client.post(
                    url,
                    json=payload,
                    headers=chat_headers(url, key),
                    timeout=30
                )
                if resp.status_code != 200:
                    raise EnhanceError(classify_http_failure(resp.status_code),
                                       f"HTTP {resp.status_code}")
                if usage is not None:
                    usage.from_response(resp, mdl)

                result = parse_enhance_response(
                    resp.json()["choices"][0]["message"]["content"])

                node['summary']     = result.get('summary', '')
                node['keywords']    = result.get('keywords', [])
                node['exam_points'] = result.get('exam_points', [])
                node['mermaid']     = result.get('mermaid', '') or ''
                node['tables']      = result.get('tables', []) or []
                node['highlights']  = result.get('highlights', []) or []
                print(f"  OK [L{node['level']}] {node['title'][:40]}")
                return

            except EnhanceError as e:
                last_kind = e.kind
                print(f"  WARN [{e.kind}] {node['title'][:30]} (attempt {attempt})")
                if e.kind not in RETRYABLE_KINDS:
                    break
                if e.kind == 'rate_limit':
                    await asyncio.sleep(5 * attempt)
            except Exception as e:
                last_kind = classify_exception(e)
                print(f"  WARN [{last_kind}] {node['title'][:30]}: {e} (attempt {attempt})")

    apply_node_fallback(node, last_kind)

# ────────────────────────────────────────────
# 单文件增强 (带全局上下文)
# ────────────────────────────────────────────

# ────────────────────────────────────────────
# 断点续跑 (Checkpoint / Resume)
# 每个节点增强完成后立即落盘；重跑时跳过已成功节点
# ────────────────────────────────────────────

class _EnhanceCheckpointer:
    """Persists enhanced nodes incrementally so a crashed run can resume.

    Progress file: {progress_path} = {path_key: {summary, keywords, ...}}
    Nodes with a successful (non-fallback) stored entry are skipped on rerun.
    """

    def __init__(self, progress_path: str):
        self.path = progress_path
        self.done = {}
        if progress_path and os.path.exists(progress_path):
            try:
                with open(progress_path, encoding='utf-8') as f:
                    self.done = json.load(f)
            except Exception:
                self.done = {}

    @staticmethod
    def path_key(node: dict, idx: int) -> str:
        return f"{node.get('level', 0)}:{node.get('title', '')[:80]}#{idx}"

    def is_done(self, key: str) -> bool:
        entry = self.done.get(key)
        return bool(entry) and not entry.get('fallback')

    def restore(self, node: dict, key: str) -> None:
        node.update(self.done[key])

    def save(self, node: dict, key: str) -> None:
        if not self.path:
            return
        self.done[key] = {
            'summary': node.get('summary', ''),
            'keywords': node.get('keywords', []),
            'exam_points': node.get('exam_points', []),
            'mermaid': node.get('mermaid', ''),
            'tables': node.get('tables', []),
            'highlights': node.get('highlights', []),
            'fallback': node.get('fallback', False),
        }
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.done, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"  WARN checkpoint save failed: {e}")


async def enhance_tree_async(tree: dict, global_index: list,
                              current_file: str,
                              progress_path: str = '') -> dict:
    to_enhance = []
    for ch in tree.get('children', []):
        collect_postorder(ch, to_enhance)

    total = len(to_enhance)
    if total == 0:
        return tree

    ckpt = _EnhanceCheckpointer(progress_path) if progress_path else None

    by_level = defaultdict(list)
    for node in to_enhance:
        by_level[node['level']].append(node)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        for level in sorted(by_level.keys(), reverse=True):
            batch = by_level[level]
            print(f"  L{level} ({len(batch)} nodes)...")

            tasks = []
            for idx, node in enumerate(batch):
                key = _EnhanceCheckpointer.path_key(node, idx)
                if ckpt and ckpt.is_done(key):
                    ckpt.restore(node, key)
                    continue
                # Find root-level title for context lookup
                root_title = _find_root_title(tree, node)
                ctx = get_surrounding_context(global_index, current_file, root_title)

                async def _run(n=node, c=ctx, k=key):
                    await enhance_one(client, n, semaphore, c)
                    if ckpt:
                        ckpt.save(n, k)

                tasks.append(_run())

            if tasks:
                await asyncio.gather(*tasks)

    return tree

def _find_root_title(tree: dict, target_node: dict) -> str:
    """Find the root-level title that contains target_node."""
    for ch in tree.get('children', []):
        if ch is target_node:
            return ch.get('title', '')
        if _contains_node(ch, target_node):
            return ch.get('title', '')
    return ''

def _contains_node(parent: dict, target: dict) -> bool:
    if parent is target:
        return True
    for c in parent.get('children', []):
        if _contains_node(c, target):
            return True
    return False

def count_enhanced(node):
    e = 1 if node.get('summary') else 0
    t = 1
    for c in node.get('children', []):
        ce, ct = count_enhanced(c)
        e += ce; t += ct
    return e, t

def enhance_single_file(input_path: str, output_dir: str,
                         global_index: list) -> dict:
    """Enhance a single tree JSON file with global context."""
    filename = os.path.basename(input_path)
    name_noext = os.path.splitext(filename)[0]

    with open(input_path, encoding='utf-8') as f:
        tree = json.load(f)

    total = sum(count_enhanced(c)[1] for c in tree.get('children', []))
    print(f"[{filename}] {total} nodes")

    progress_path = os.path.join(output_dir, f"{name_noext}_progress.json")
    asyncio.run(enhance_tree_async(tree, global_index, filename, progress_path))

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{name_noext}_enhanced.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    enhanced = sum(count_enhanced(c)[0] for c in tree.get('children', []))
    return {"filename": filename, "path": out_path, "enhanced": enhanced, "total": total}

async def enhance_book_nodes(tree: dict, api_key: str,
                             allowed_levels: set = None,
                             max_nodes: int = None,
                             concurrency: int = MAX_CONCURRENT,
                             request_delay: float = REQUEST_DELAY,
                             base_url: str = None,
                             model: str = None,
                             on_progress=None,
                             document_profile: dict = None,
                             progress_path: str = '',
                             subject_config: dict = None,
                             usage=None) -> dict:
    """Enhance a whole merged book tree with retry + fallback per node.

    Used by server._enhance_tree. Guarantees: every targeted node ends the
    run with summary/keywords/exam_points filled — by the model when it
    succeeds, by a deterministic fallback (fallback=True) otherwise.

    P2 断点续跑：传入 progress_path 时，每个节点成功后立即落盘；重跑时已成功
    （非 fallback）的节点直接恢复并跳过，只有失败/兜底节点重试。
    """
    from node_enhancer import (build_prompt, get_context_text,
                               should_enhance, collect_postorder,
                               detect_document_profile,
                               EnhanceError, RETRYABLE_KINDS,
                               classify_http_failure, classify_exception,
                               parse_enhance_response, apply_node_fallback)

    if document_profile is None:
        document_profile = detect_document_profile(tree)
    url = base_url or OPENAI_BASE_URL
    mdl = model or MODEL
    subj = subject_config or SUBJECT_CONFIG

    to_enhance = []
    for ch in tree.get("children", []):
        collect_postorder(ch, to_enhance)
    if allowed_levels:
        to_enhance = [n for n in to_enhance if n.get("level", 0) in allowed_levels]
    if max_nodes and len(to_enhance) > max_nodes:
        to_enhance.sort(key=lambda n: n.get("level", 0))
        to_enhance = to_enhance[:max_nodes]

    total = len(to_enhance)
    if total == 0:
        return tree

    stats = {"done": 0, "ok": 0, "fallback": 0, "consecutive_fail": 0}
    CONSECUTIVE_FAIL_LIMIT = 5
    semaphore = asyncio.Semaphore(concurrency)

    # P2 断点续跑：按 (level,title,序号) 定位节点，已成功者直接恢复跳过
    ckpt = _EnhanceCheckpointer(progress_path) if progress_path else None
    pending: list = []
    for i, n in enumerate(to_enhance):
        key = f"{n.get('level', 0)}:{n.get('title', '')[:80]}#{i}"
        if ckpt and ckpt.is_done(key):
            ckpt.restore(n, key)
            stats["done"] += 1
            stats["ok"] += 1
            if on_progress:
                on_progress(stats, total)
            continue
        pending.append((n, key))
    if ckpt and stats["ok"]:
        print(f"  断点续跑: 跳过 {stats['ok']} 个已成功节点，剩余 {len(pending)} 个待处理")

    async def _one(client: httpx.AsyncClient, node: dict, key: str) -> None:
        async with semaphore:
            if stats["consecutive_fail"] >= CONSECUTIVE_FAIL_LIMIT:
                apply_node_fallback(node, 'circuit_open')
                stats["done"] += 1
                stats["fallback"] += 1
                if on_progress:
                    on_progress(stats, total)
                return
            await asyncio.sleep(request_delay)
            last_kind = ''
            succeeded = False
            for attempt in range(1, 3):
                prompt = build_prompt(node, subj,
                                      get_context_text(node), "", document_profile)
                payload = {
                    "model": mdl,
                    "max_tokens": 1000,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                    **chat_body_extras(url),
                }
                try:
                    resp = await client.post(
                        url, json=payload,
                        headers=chat_headers(url, api_key),
                        timeout=20)
                    if resp.status_code != 200:
                        raise EnhanceError(classify_http_failure(resp.status_code))
                    if usage is not None:
                        usage.from_response(resp, mdl)
                    result = parse_enhance_response(
                        resp.json()["choices"][0]["message"]["content"])
                    node["summary"] = result.get("summary", "")
                    node["keywords"] = result.get("keywords", [])
                    node["exam_points"] = result.get("exam_points", [])
                    node["mermaid"] = result.get("mermaid", "") or ""
                    node["tables"] = result.get("tables", []) or []
                    succeeded = True
                    break
                except EnhanceError as e:
                    last_kind = e.kind
                    if e.kind not in RETRYABLE_KINDS:
                        break
                    if e.kind == 'rate_limit':
                        await asyncio.sleep(5 * attempt)
                except Exception as e:
                    last_kind = classify_exception(e)

            stats["done"] += 1
            if succeeded:
                stats["ok"] += 1
                stats["consecutive_fail"] = 0
                if ckpt:
                    ckpt.save(node, key)
            else:
                stats["fallback"] += 1
                stats["consecutive_fail"] += 1
                apply_node_fallback(node, last_kind)
            if on_progress:
                on_progress(stats, total)

    async with httpx.AsyncClient(timeout=20) as client:
        await asyncio.gather(*[_one(client, n, k) for n, k in pending])

    return tree


def tree_fingerprint(tree: dict) -> str:
    """Stable short hash of a tree's structure — used to locate its resume file."""
    import hashlib
    h = hashlib.md5()

    def _walk(n: dict, d: int):
        h.update(((n.get('title') or '') + '|' + str(d) + '\n').encode('utf-8'))
        for c in n.get('children', []):
            _walk(c, d + 1)

    for c in tree.get('children', []):
        _walk(c, 1)
    return h.hexdigest()[:12]


# ────────────────────────────────────────────
# 导学生成 (Chapter Guide + Prerequisites)
# P3: 为每个一级模块生成 3-5 句导学 + 前置章标注；一次 LLM 调用同时产出，
# 结果写入 node.guide / node.prerequisites，随 knowledge_tree.json 缓存。
# 失败时静默跳过（前端已有静态兜底文案），绝不阻断管线。
# ────────────────────────────────────────────

GUIDE_MAX_CHARS = 1200   # 每章摘要喂给 LLM 的子节点文本上限

def _collect_guide_context(node: dict, max_chars: int = GUIDE_MAX_CHARS) -> str:
    """Deterministically sample a module's own text for the guide prompt."""
    parts = []

    def _walk(n: dict, depth: int):
        if len(''.join(parts)) >= max_chars:
            return
        title = (n.get('title') or '').strip()
        summary = (n.get('summary') or '').strip()
        if title:
            parts.append(('  ' * depth) + '· ' + title + (('：' + summary[:60]) if summary else ''))
        for c in n.get('children', [])[:12]:
            _walk(c, depth + 1)

    for c in node.get('children', [])[:15]:
        _walk(c, 1)
    text = '\n'.join(parts)
    return text[:max_chars * 2]

def build_guide_prompt(module_titles: list, module_title: str,
                      module_index: int, context_text: str) -> str:
    others = [t for i, t in enumerate(module_titles) if i != module_index]
    others_preview = '；'.join(others[:20])
    return f"""你是教育类资料的导学编写者。请为以下章节写一段导学，并标注它的前置章节。

本书共 {len(module_titles)} 章：{others_preview}等。
【当前章节】{module_title}

该章节的知识结构（标题层级，部分带摘要）：
{context_text}

请只输出一个 JSON 对象（不要 markdown 代码块，不要多余文字）：
{{
  "guide": "3-5句话的导学：本章讲什么、内部结构如何、重点难点在哪、考点分布、建议的学习切入点。用换行分隔句子。不要空话，必须提到本章的具体概念名称",
  "prerequisites": ["本章学习前应先掌握的其他章节标题，从本书章节列表中选；无前置则返回空数组"]
}}

原则：guide 中提到的概念必须来自上面的知识结构，不得编造；prerequisites 只能从本书章节列表中选取，不要发明不存在的章节名。"""


def parse_guide_response(raw: str) -> dict:
    """Parse guide JSON; returns {} on any failure (caller skips silently)."""
    raw = (raw or '').strip()
    if not raw:
        return {}
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    start = raw.find('{')
    if start < 0:
        return {}
    try:
        data = json.loads(raw[start:])
    except json.JSONDecodeError:
        return {}
    guide = data.get('guide') or ''
    prereqs = data.get('prerequisites') or []
    if not isinstance(prereqs, list):
        prereqs = []
    prereqs = [str(p).strip() for p in prereqs if str(p).strip()][:6]
    if not isinstance(guide, str) or not guide.strip():
        return {}
    return {'guide': guide.strip()[:1500], 'prerequisites': prereqs}


async def generate_module_guide(client: httpx.AsyncClient, node: dict,
                                module_titles: list, module_index: int,
                                api_key: str, semaphore: asyncio.Semaphore,
                                base_url: str = None, model: str = None,
                                usage=None) -> None:
    """Fill node.guide / node.prerequisites for one top-level module. Never raises."""
    url = base_url or OPENAI_BASE_URL
    mdl = model or MODEL
    context = _collect_guide_context(node)
    prompt = build_guide_prompt(module_titles, node.get('title', ''),
                                module_index, context)
    last_kind = ''
    for attempt in range(1, 3):
        async with semaphore:
            await asyncio.sleep(REQUEST_DELAY)
            payload = {
                "model": mdl,
                "max_tokens": 600,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
                **chat_body_extras(url),
            }
            try:
                resp = await client.post(
                    url, json=payload,
                    headers=chat_headers(url, api_key),
                    timeout=30)
                if resp.status_code != 200:
                    raise EnhanceError(classify_http_failure(resp.status_code))
                if usage is not None:
                    usage.from_response(resp, mdl)
                result = parse_guide_response(
                    resp.json()["choices"][0]["message"]["content"])
                if not result:
                    raise EnhanceError('json_parse')
                node['guide'] = result['guide']
                node['prerequisites'] = result['prerequisites']
                print(f"  GUIDE OK {node.get('title', '')[:40]} (前置: {len(result['prerequisites'])})")
                return
            except EnhanceError as e:
                last_kind = e.kind
                if e.kind not in RETRYABLE_KINDS:
                    break
                if e.kind == 'rate_limit':
                    await asyncio.sleep(5 * attempt)
            except Exception as e:
                last_kind = classify_exception(e)
    print(f"  GUIDE SKIP [{last_kind}] {node.get('title', '')[:40]} (前端将使用静态兜底导学)")


def guide_modules(tree: dict) -> list:
    """取“实际模块”列表，口径与前端 KT.bookModules / parse_report.book_modules 一致。

    单册上传时树通常是 root -> 一本书 -> 章，直接遍历 tree.children 只会拿到
    包装节点，导学和先修都会挂在错误层级。这里统一解包，避免后端/前端/统计
    各写一套结构判断。
    """
    from parse_report import book_modules
    return book_modules(tree)


async def generate_guides(tree: dict, api_key: str,
                          concurrency: int = MAX_CONCURRENT,
                          base_url: str = None, model: str = None,
                          on_progress=None, usage=None) -> dict:
    """Generate guide + prerequisites for every top-level module and the book root.

    Deterministic guarantees (code, not prompt):
    - prerequisites are filtered to valid sibling titles, self-refs and cycles dropped
    - per-module failure → static fallback guide (children titles enumeration)
    """
    modules = guide_modules(tree)
    if not modules:
        return tree

    titles = [(m.get('title') or '').strip() for m in modules]
    title_set = set(titles)
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=30) as client:
        # Book-level guide (root): synthesised from module titles + first guide each
        module_texts = '\n'.join(
            f"{i + 1}. {t}" for i, t in enumerate(titles))
        root_prompt = f"""你是教育类资料的导学编写者。这本书包含以下章节：
{module_texts}

请只输出一个 JSON 对象（不要 markdown 代码块，不要多余文字）：
{{
  "guide": "4-6句话的全书导学：本书整体讲什么、各部分之间是什么关系、建议的学习顺序和理由、考研复习的重点分布。不要空话，必须提到具体章节名",
  "prerequisites": []
}}"""
        try:
            async with semaphore:
                await asyncio.sleep(REQUEST_DELAY)
                payload = {
                    "model": model or MODEL,
                    "max_tokens": 600,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": root_prompt}],
                    **chat_body_extras(base_url or OPENAI_BASE_URL),
                }
                resp = await client.post(
                    base_url or OPENAI_BASE_URL, json=payload,
                    headers=chat_headers(base_url or OPENAI_BASE_URL, api_key),
                    timeout=30)
                if resp.status_code == 200:
                    if usage is not None:
                        usage.from_response(resp, model or MODEL)
                    result = parse_guide_response(
                        resp.json()["choices"][0]["message"]["content"])
                    if result.get('guide'):
                        tree['guide'] = result['guide']
                        print("  GUIDE OK <book root>")
        except Exception as e:
            print(f"  GUIDE SKIP <book root> ({e}) — 前端将使用静态兜底导学")

        # Per-module guides
        done = [0]

        async def _one(n: dict, i: int):
            await generate_module_guide(client, n, titles, i, api_key,
                                        semaphore, base_url, model, usage)
            done[0] += 1
            if on_progress:
                on_progress(done[0], len(modules))

        await asyncio.gather(*[_one(m, i) for i, m in enumerate(modules)])

    # ── Deterministic post-processing (code, never the model) ──
    # 1. filter prerequisites to valid siblings; drop self / forward-only refs
    idx_of = {t: i for i, t in enumerate(titles) if t}
    for i, m in enumerate(modules):
        raw_prereqs = m.get('prerequisites') or []
        cleaned = []
        for p in raw_prereqs:
            p = (p or '').strip()
            # fuzzy: model may echo a slightly different title; accept containment
            matched = p if p in idx_of else next(
                (t for t in idx_of if p and (p in t or t in p) and len(t) > 1), None)
            if matched is None or matched == titles[i]:
                continue
            j = idx_of[matched]
            if j < i and matched not in cleaned:
                cleaned.append(matched)
        if cleaned or raw_prereqs:
            m['prerequisites'] = cleaned
        else:
            m.setdefault('prerequisites', [])

    # 2. cycles are impossible after "prerequisite index < own index" filter,
    #    but guard anyway: DFS drop back-edges
    def _break_cycles():
        color = {}

        def _visit(i: int) -> bool:
            color[i] = 1
            for p in modules[i].get('prerequisites', []):
                j = idx_of.get(p)
                if j is None:
                    continue
                if color.get(j) == 1:  # back-edge → drop
                    modules[i]['prerequisites'].remove(p)
                elif color.get(j, 0) == 0:
                    _visit(j)
            color[i] = 2
            return True

        for i in range(len(modules)):
            if color.get(i, 0) == 0:
                _visit(i)

    _break_cycles()

    # 3. static fallback guide for modules without one
    for m in modules:
        if not (m.get('guide') or '').strip():
            child_titles = [c.get('title', '') for c in (m.get('children') or [])[:4]]
            child_titles = [t for t in child_titles if t.strip()]
            if child_titles:
                m['guide'] = f"本章围绕 {(m.get('title') or '').strip()} 展开，涵盖 {'、'.join(child_titles)} 等内容。建议先通读各小节摘要建立框架，再用考点模式定位高频考点。"
                m['_guide_fallback'] = True
            m.setdefault('prerequisites', [])

    return tree


# ────────────────────────────────────────────
# 结构整理
# ────────────────────────────────────────────

def cleanup_tree_structure(tree: dict) -> dict:
    tree = _remove_empty_leaf_nodes(tree)
    tree = _deduplicate_sibling_titles(tree)
    tree = _normalize_children_order(tree)
    tree = _trim_noise_nodes(tree)
    return tree


def _remove_empty_leaf_nodes(node: dict) -> dict:
    children = node.get("children", [])
    if not children:
        return node
    filtered = []
    for child in children:
        child = _remove_empty_leaf_nodes(child)
        g_children = child.get("children", [])
        has_content = bool((child.get("content") or "").strip())
        has_summary = bool(child.get("summary", ""))
        title = (child.get("title") or "").strip()
        if not title and not has_content and not has_summary and not g_children:
            continue
        filtered.append(child)
    node["children"] = filtered
    return node


def _deduplicate_sibling_titles(node: dict) -> dict:
    children = node.get("children", [])
    if not children:
        return node
    seen = set()
    deduped = []
    for child in children:
        child = _deduplicate_sibling_titles(child)
        title = (child.get("title") or "").strip()
        if title and title in seen:
            if not child.get("children") and not (child.get("content") or "").strip():
                continue
        if title:
            seen.add(title)
        deduped.append(child)
    node["children"] = deduped
    return node


def _cn_num_to_int(text: str) -> int:
    """中文编号 → 整数，直接复用 hierarchy_repair 的实现。

    历史上这里有一份自己的转换表：只到"十"、且 `cn_map.get(s[1:])` 拿字符串
    查单字表，导致 第二十章→30、一十二→10、第一百章→1。已改为调用权威实现，
    二十→20、一百二十三→123 均正确。
    """
    from hierarchy_repair import chinese_to_int
    return chinese_to_int(text or "")


def _normalize_children_order(node: dict) -> dict:
    """按编号恢复兄弟节点的正确顺序；无编号时退回文档源序。

    原实现只从 title 现抓正则，且把匹配不上的标题按 Unicode 码点排到队尾：
    （一）三、二 类的中文编号因此被打乱，未编号节点被字母序重排，第20章之后
    也因中文数字转换错误而错位。现在优先使用 hierarchy_repair 已经算好的
    number_path / raw_prefix（挂在节点上，随 JSON 落盘），拿不到时用
    parse_md_to_nodes 记录的 src_index 恢复文档序——两者都没有才保持原序。
    """
    children = node.get("children", [])
    if not children:
        return node

    for child in children:
        _normalize_children_order(child)

    family_groups = defaultdict(list)
    for idx, child in enumerate(children):
        fam = _numbering_family(child)
        family_groups[fam].append(idx)

    target = list(children)

    # 无编号的节点各自成组（fam 为 None），只有 2 个及以上时才值得排：
    # 单个未编号节点无从判断，保持原位最安全。
    reordered = False
    for fam, idxs in family_groups.items():
        if fam is None:
            if len(idxs) < 2:
                continue
            # 未编号：按文档源序排。没有 src_index 的（老数据/合成节点）
            # 会被 _src_order 给同一个哨兵值，稳定排序保持原顺序。
            group = [children[i] for i in idxs]
            if [ _src_order(c) for c in group ] == sorted(_src_order(c) for c in group):
                continue
            group.sort(key=_src_order)
            reordered = True
        else:
            if len(idxs) < 2:
                continue
            group = [children[i] for i in idxs]
            keyed = [(_order_key(c, fam), j) for j, c in enumerate(group)]
            if [k for k, _ in keyed] == sorted(k for k, _ in keyed):
                continue
            group = [group[j] for _, j in sorted(keyed, key=lambda t: t[0])]
            reordered = True
        for pos, node_item in zip(idxs, group):
            target[pos] = node_item

    if reordered:
        node["children"] = target
    return node


def _src_order(node: dict) -> int:
    """节点的"原始顺序"键；缺失时返回同一哨兵值，让稳定排序保持原有相对顺序。

    优先用 seq_index（structure_repair 审校后写下的最终序），它已经包含审校结论；
    退化为 src_index（文档原始顺序）时适用于未审校的老数据。两者都没有时共用
    同一个哨兵值，稳定排序便不会打乱这些节点。
    """
    for key in ("seq_index", "src_index"):
        try:
            return int(node.get(key))
        except (TypeError, ValueError):
            continue
    return 1 << 30


def _numbering_family(node: dict):
    """识别兄弟节点属于哪一套编号体系（章/节/（一）/1./①…）。

    优先用 hierarchy_repair 写入的 numbering_type；老数据没有该字段时，
    退回从 raw_prefix / title 现识别，口径与 hierarchy_repair 的注册表一致。
    返回 numbering_type 或 None（无编号）。
    """
    from hierarchy_repair import _TYPE_CATEGORY
    ntype = node.get("numbering_type")
    if ntype in _TYPE_CATEGORY:
        return ntype
    for key in ("raw_prefix", "title"):
        cand = (node.get(key) or "").strip()
        if not cand:
            continue
        detected = _detect_numbering_family(cand)
        if detected:
            return detected
    return None


def _detect_numbering_family(text: str):
    """从一段文本识别编号体系类型，返回 numbering_type 或 None。"""
    try:
        from hierarchy_repair import NUMBER_PATTERNS, normalize_numbering
        t = normalize_numbering((text or "").strip())
        for name, pat in NUMBER_PATTERNS.items():
            if pat.match(t):
                return name
    except Exception:
        return None
    return None


def _order_key(node: dict, fam: str):
    """同一编号族内的排序键。

    优先用 number_path（hierarchy_repair 已解析好的整数序列），既支持
    一、二、三 也支持 1.2.3 多级编号；解析不出来时从编号文本现取数值。
    """
    path = node.get("number_path")
    if isinstance(path, (list, tuple)) and path:
        try:
            return (0, tuple(int(x) for x in path), "")
        except (TypeError, ValueError):
            pass
    text = (node.get("raw_prefix") or node.get("title") or "").strip()
    return (1, (_extract_number(text, fam),), "")


_CIRCLE_CHARS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'


def _extract_number(text: str, fam: str) -> int:
    """从编号文本取出可比较的整数值（阿拉伯/中文/圆圈/多级编号都支持）。

    多级编号（1.2.3）按最后一段取序号：同一父节点下的兄弟同族时，末段正是
    它们相互区分的那一位。整串解析成 int 比逐段更稳，因各族分隔符不一。
    """
    from hierarchy_repair import (NUMBER_PATTERNS, normalize_numbering,
                                   chinese_to_int, circle_to_int)
    raw = ""
    for cand in (text, normalize_numbering(text)):
        pat = NUMBER_PATTERNS.get(fam)
        m = pat.match(cand.strip()) if pat else None
        if m:
            raw = (m.group(1) or "").strip()
            if raw:
                break
    if not raw:
        m2 = re.search(r'[一二三四五六七八九十百千\d]+', text)
        raw = m2.group(0) if m2 else ""
    if not raw:
        return 0
    # 多级阿拉伯编号取末段："1.2" → 2
    if "." in raw:
        tail = raw.rsplit(".", 1)[-1]
        if tail.isdigit():
            return int(tail)
    # 注意：str.isdigit() 对 ① 这类 Unicode 数字也返回 True，必须先判圆圈字符
    if len(raw) == 1 and raw in _CIRCLE_CHARS:
        return circle_to_int(raw)
    if raw.isdigit():
        return int(raw)
    return chinese_to_int(raw)


def _trim_noise_nodes(node: dict) -> dict:
    NOISE_TITLES = {
        "前言", "序言", "导读", "后记", "参考文献", "附录",
        "目录", "版权页", "封面", "封底", "致谢", "出版信息",
    }
    children = node.get("children", [])
    if not children:
        return node
    filtered = []
    for child in children:
        title = (child.get("title") or "").strip()
        if title in NOISE_TITLES and not child.get("children"):
            continue
        child = _trim_noise_nodes(child)
        filtered.append(child)
    node["children"] = filtered
    return node


# ────────────────────────────────────────────
# 入口
# ────────────────────────────────────────────

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(__file__)
    INPUT_DIR  = os.path.join(BASE_DIR, "data", "tree_parts")
    OUTPUT_DIR = os.path.join(BASE_DIR, "data", "tree_parts_enhanced")
    MERGED_OUTPUT = os.path.join(BASE_DIR, "data", "knowledge_tree_enhanced.json")

    # Step 1: Load all trees and build global context
    print(f"Loading trees from {INPUT_DIR}...")
    all_trees = load_all_trees(INPUT_DIR)
    print(f"Loaded {len(all_trees)} tree files")

    global_index = build_global_index(all_trees)
    print(f"Global index: {len(global_index)} top-level chapters\n")

    # Step 2: Parallel enhance with context
    print("Enhancing trees in parallel (with cross-file context)...\n")
    input_files = sorted([
        os.path.join(INPUT_DIR, f) for f in os.listdir(INPUT_DIR) if f.endswith(".json")
    ]) if os.path.isdir(INPUT_DIR) else []

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(enhance_single_file, p, OUTPUT_DIR, global_index): p
            for p in input_files
        }
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            print(f"  Done: {r['filename']} ({r['enhanced']}/{r['total']} enhanced)\n")

    results.sort(key=lambda x: x['filename'])

    # Step 3: Merge all enhanced trees into one JSON
    print(f"Merging into {MERGED_OUTPUT}...")
    merged_children = []
    for r in results:
        with open(r['path'], encoding='utf-8') as f:
            tree = json.load(f)
        merged_children.extend(tree.get('children', []))

    merged = {"title": "333教育综合", "children": merged_children}
    with open(MERGED_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    total_enhanced = sum(r['enhanced'] for r in results)
    total_nodes = sum(r['total'] for r in results)
    print(f"\nDone: {len(results)} files, {total_enhanced}/{total_nodes} nodes enhanced")
    print(f"  Merged output: {MERGED_OUTPUT}")
