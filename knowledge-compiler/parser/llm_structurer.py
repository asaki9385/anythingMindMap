"""
LLM-based text structuring: convert raw text chunks into structured tree JSON.
Used for TXT files and Word files without heading styles.
"""

import json
import os
import asyncio
import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
API_TIMEOUT = 30
REQUEST_DELAY = 0.5


def build_structure_prompt(chunk_text: str, chunk_meta: dict, prev_tail_context: str = "") -> str:
    """Build prompt for LLM to structure raw text into a tree."""
    meta_info = ""
    if chunk_meta.get("total", 1) > 1:
        meta_info = f"\n这是第 {chunk_meta['index'] + 1}/{chunk_meta['total']} 个文本片段。"
        if chunk_meta.get("is_first"):
            meta_info += " 这是文档的开头部分。"
        if chunk_meta.get("is_last"):
            meta_info += " 这是文档的结尾部分。"

    context_block = ""
    if prev_tail_context:
        context_block = f"""
## 上文衔接信息
{prev_tail_context}

请判断：当前文本是上文内容的【延续】还是【新主题的开始】？
- 如果是延续：当前片段的根节点 title 应与上文末尾知识点形成自然承接
- 如果是新主题：根节点 title 应体现转折，children 第一个节点可以包含"承接上文"的过渡说明
"""


    return f"""你是一位教育学学科专家，擅长从原始文本中提取知识结构。

## 任务
分析以下文本内容，将其组织成清晰的知识树结构。识别主题、子主题和关键知识点。

{meta_info}
{context_block}
## 原始文本
{chunk_text[:6000]}

## 输出要求
严格输出JSON，不要输出任何其他内容:
{{
  "title": "本片段的核心主题标题",
  "children": [
    {{
      "title": "子主题1的标题",
      "content": "该子主题的核心内容（100-300字）",
      "children": [
        {{
          "title": "更细的知识点标题",
          "content": "具体知识点内容",
          "children": []
        }}
      ]
    }},
    {{
      "title": "子主题2的标题",
      "content": "该子主题的核心内容",
      "children": []
    }}
  ]
}}

## 规则
- title: 简洁明确，反映内容主旨
- content: 保留原文的关键信息，适当精简但不丢失要点
- children: 按逻辑关系组织，体现层级结构（主题→子主题→知识点）
- 层级深度建议2-4层，不要太深
- 如果文本内容较简单，可以只用1-2层
- 如果有并列关系的概念，放在同一层级
- 如果有因果、递进关系，按顺序排列
- 空children用空数组[]"""


async def structure_text_chunk(client: httpx.AsyncClient, chunk: dict,
                                prev_tail_context: str = "", api_key: str | None = None) -> tuple[dict, str]:
    """Call LLM to structure one text chunk. Returns (tree_dict, tail_context)."""
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("DeepSeek API key is required. Set OPENAI_API_KEY env var or pass the api_key parameter.")
    prompt = build_structure_prompt(chunk["text"], chunk, prev_tail_context)
    payload = {
        "model": MODEL,
        "max_tokens": 2000,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}]
    }

    resp = await client.post(
        OPENAI_BASE_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tree = json.loads(content)

    # Generate tail context for cross-chunk bridging
    tail_context = _extract_tail_context(tree)

    return tree, tail_context


def _extract_tail_context(tree: dict) -> str:
    """Extract trailing leaf nodes from a tree for cross-chunk context bridging.

    Collects all leaf nodes (no children), takes the last 3, and formats them
    so the next chunk's LLM can see what immediately preceded it.
    """
    leaves = []

    def _walk(node):
        children = node.get("children", [])
        if not children:
            leaves.append({
                "title": node.get("title", ""),
                "content": node.get("content", ""),
            })
        else:
            for child in children:
                _walk(child)

    _walk(tree)

    if not leaves:
        return ""

    tail = leaves[-3:]
    lines = ["上文末尾知识点："]
    for i, leaf in enumerate(tail, 1):
        text = (leaf["content"] or "")[:150]
        lines.append(f"{i}. {leaf['title']}: {text}" if text else f"{i}. {leaf['title']}")
    return "\n".join(lines)


async def structure_all_chunks(chunks: list[dict], api_key: str | None = None) -> list[dict]:
    """Sequentially process chunks with context bridging.

    Each chunk's output includes the tree + a summary that feeds into the next chunk.
    Returns list of tree dicts.
    """
    trees = []
    prev_tail_context = ""

    async with httpx.AsyncClient() as client:
        for i, chunk in enumerate(chunks):
            try:
                tree, tail_context = await structure_text_chunk(
                    client, chunk, prev_tail_context, api_key=api_key,
                )
                trees.append(tree)
                prev_tail_context = tail_context
            except Exception as e:
                print(f"  WARNING: LLM structuring failed for chunk {i}: {e}")
                # Create a fallback tree from raw text
                fallback = _fallback_tree(chunk)
                trees.append(fallback)
                prev_tail_context = _extract_tail_context(fallback)

            if i < len(chunks) - 1:
                await asyncio.sleep(REQUEST_DELAY)

    return trees


def _fallback_tree(chunk: dict) -> dict:
    """Create a simple tree from raw text when LLM fails."""
    text = chunk["text"]
    # Split by paragraphs and create flat structure
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    children = []
    for j, para in enumerate(paragraphs[:10]):
        title = para[:30].replace('\n', ' ')
        if len(para) > 30:
            title += "..."
        children.append({
            "title": title,
            "content": para[:500],
            "children": [],
        })

    return {
        "title": f"文本片段 {chunk['index'] + 1}",
        "children": children,
    }
