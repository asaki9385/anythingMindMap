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
# 单节点 API 调用
# ────────────────────────────────────────────

async def enhance_one(client: httpx.AsyncClient, node: dict,
                      semaphore: asyncio.Semaphore,
                      surrounding_ctx: str = "") -> None:
    async with semaphore:
        await asyncio.sleep(REQUEST_DELAY)
        prompt = build_prompt(node, SUBJECT_CONFIG, get_context_text(node), surrounding_ctx)
        payload = {
            "model": MODEL,
            "max_tokens": 1500,
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
                timeout=30
            )
            if resp.status_code != 200:
                print(f"  FAIL [{resp.status_code}] {node['title'][:30]}")
                return

            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            result = json.loads(raw)

            node['summary']     = result.get('summary', '')
            node['keywords']    = result.get('keywords', [])
            node['exam_points'] = result.get('exam_points', [])
            node['mermaid']     = result.get('mermaid', '') or ''
            node['tables']      = result.get('tables', []) or []
            node['highlights']  = result.get('highlights', []) or []
            print(f"  OK [L{node['level']}] {node['title'][:40]}")

        except json.JSONDecodeError:
            print(f"  WARN JSON parse fail: {node['title'][:30]}")
        except Exception as e:
            print(f"  FAIL {node['title'][:30]}: {e}")

# ────────────────────────────────────────────
# 单文件增强 (带全局上下文)
# ────────────────────────────────────────────

async def enhance_tree_async(tree: dict, global_index: list,
                              current_file: str) -> dict:
    to_enhance = []
    for ch in tree.get('children', []):
        collect_postorder(ch, to_enhance)

    total = len(to_enhance)
    if total == 0:
        return tree

    by_level = defaultdict(list)
    for node in to_enhance:
        by_level[node['level']].append(node)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        for level in sorted(by_level.keys(), reverse=True):
            batch = by_level[level]
            print(f"  L{level} ({len(batch)} nodes)...")

            tasks = []
            for node in batch:
                # Find root-level title for context lookup
                root_title = _find_root_title(tree, node)
                ctx = get_surrounding_context(global_index, current_file, root_title)
                tasks.append(enhance_one(client, node, semaphore, ctx))

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

    asyncio.run(enhance_tree_async(tree, global_index, filename))

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{name_noext}_enhanced.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    enhanced = sum(count_enhanced(c)[0] for c in tree.get('children', []))
    return {"filename": filename, "path": out_path, "enhanced": enhanced, "total": total}

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


def _normalize_children_order(node: dict) -> dict:
    children = node.get("children", [])
    if not children:
        return node

    for child in children:
        _normalize_children_order(child)

    def sort_key(c):
        title = c.get("title", "")
        m_chapter = re.match(r'^第[一二三四五六七八九十百\d]+章', title)
        m_section = re.match(r'^第[一二三四五六七八九十百\d]+节', title)
        m_kp = re.match(r'^知识点[一二三四五六七八九十\d]+', title)
        m_num = re.match(r'^[（(]?(\d+)[）).、]', title)
        if m_chapter:
            cn_num = _cn_num_to_int(m_chapter.group(0))
            return (0, cn_num, "")
        if m_section:
            cn_num = _cn_num_to_int(m_section.group(0))
            return (1, cn_num, "")
        if m_kp:
            cn_num = _cn_num_to_int(m_kp.group(0))
            return (2, cn_num, "")
        if m_num:
            return (3, int(m_num.group(1)), title)
        return (4, 0, title)

    node["children"].sort(key=sort_key)
    return node


def _cn_num_to_int(text: str) -> int:
    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    nums = re.findall(r'[一二三四五六七八九十\d]+', text)
    if not nums:
        return 9999
    s = nums[0]
    if s.isdigit():
        return int(s)
    if s in cn_map:
        return cn_map[s]
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + cn_map.get(s[1], 0)
    return cn_map.get(s[0], 0) * 10 + cn_map.get(s[1:], 0) if len(s) > 1 else cn_map.get(s, 9999)


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
