"""AI-assisted hierarchy validation with source Markdown context.

Takes the flat node list from apply_hierarchy_repair() and the original
Markdown source, asks the AI to identify level errors and misplaced nodes,
then applies corrections before build_tree().
"""
import json
import re
import asyncio
import httpx

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
API_TIMEOUT = 40

# Token 估算阈值（中文约2字=1token）
THRESHOLD_FULL = 4000      # 低于此值：整体发送
THRESHOLD_WINDOW = 8000    # 低于此值：原文只发节点窗口
# 超过 THRESHOLD_WINDOW：按二级标题切分后分批校验


async def ai_validate_with_source(
    nodes: list[dict],
    source_md: str,
    api_key: str,
    model: str = MODEL,
    api_base_url: str = '',
) -> list[dict]:
    """
    主入口。自适应粒度：根据内容体量决定发送策略。
    失败时静默降级，返回原始 nodes。
    """
    if not nodes or not api_key:
        return nodes

    token_estimate = _estimate_tokens(nodes, source_md)
    print(f"  [ai_validate] 估算 token={token_estimate}，节点数={len(nodes)}")

    try:
        if token_estimate < THRESHOLD_FULL:
            nodes = await _validate_batch(nodes, source_md, api_key, model, api_base_url)
        elif token_estimate < THRESHOLD_WINDOW:
            windowed_md = _build_windowed_source(nodes, source_md, window_chars=100)
            nodes = await _validate_batch(nodes, windowed_md, api_key, model, api_base_url)
        else:
            nodes = await _validate_in_segments(nodes, source_md, api_key, model, api_base_url)
    except Exception as e:
        print(f"  [ai_validate] 校验失败，跳过：{e}")

    return nodes


# ─────────────────────────────────────────
# 内部：单批校验
# ─────────────────────────────────────────

async def _validate_batch(
    nodes: list[dict],
    source_md: str,
    api_key: str,
    model: str,
    api_base_url: str = '',
) -> list[dict]:
    prompt = _build_prompt(nodes, source_md)

    async with httpx.AsyncClient() as client:
        _url = api_base_url or DEEPSEEK_URL
        resp = await client.post(
            _url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1000,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

    result = json.loads(raw)
    corrections = result.get("corrections", [])

    if not corrections:
        print(f"  [ai_validate] AI 确认层级无需修正")
        return nodes

    return _apply_corrections(nodes, corrections)


# ─────────────────────────────────────────
# 内部：分段校验（大文档）
# ─────────────────────────────────────────

async def _validate_in_segments(
    nodes: list[dict],
    source_md: str,
    api_key: str,
    model: str,
    api_base_url: str = '',
) -> list[dict]:
    segments = _split_by_h2(source_md)
    if len(segments) <= 1:
        windowed_md = _build_windowed_source(nodes, source_md, window_chars=100)
        return await _validate_batch(nodes, windowed_md, api_key, model, api_base_url)

    seg_node_groups = _assign_nodes_to_segments(nodes, segments)

    all_corrections = []
    for seg_idx, (seg_md, seg_node_indices) in enumerate(seg_node_groups):
        if not seg_node_indices:
            continue
        seg_nodes = [nodes[i] for i in seg_node_indices]

        indexed_seg_nodes = [dict(n, _seg_idx=j) for j, n in enumerate(seg_nodes)]
        prompt = _build_prompt(indexed_seg_nodes, seg_md, index_field="_seg_idx")

        try:
            async with httpx.AsyncClient() as client:
                _url = api_base_url or DEEPSEEK_URL
                resp = await client.post(
                    _url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 800,
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=API_TIMEOUT,
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                result = json.loads(raw)
                seg_corrections = result.get("corrections", [])

                for c in seg_corrections:
                    seg_local_idx = c.get("index")
                    if seg_local_idx is not None and seg_local_idx < len(seg_node_indices):
                        c["index"] = seg_node_indices[seg_local_idx]
                        if "new_parent_index" in c:
                            local_parent = c["new_parent_index"]
                            if local_parent < len(seg_node_indices):
                                c["new_parent_index"] = seg_node_indices[local_parent]
                            else:
                                del c["new_parent_index"]
                        all_corrections.append(c)

        except Exception as e:
            print(f"  [ai_validate] 第{seg_idx+1}段校验失败，跳过：{e}")
            continue

        await asyncio.sleep(0.3)

    if not all_corrections:
        return nodes

    return _apply_corrections(nodes, all_corrections)


# ─────────────────────────────────────────
# 内部：应用修正
# ─────────────────────────────────────────

def _apply_corrections(nodes: list[dict], corrections: list[dict]) -> list[dict]:
    move_corrections = []
    for c in corrections:
        idx = c.get("index")
        new_level = c.get("new_level")
        new_parent_idx = c.get("new_parent_index")
        reason = c.get("reason", "")

        if idx is None or not (0 <= idx < len(nodes)):
            continue
        if new_level is not None and not (1 <= new_level <= 6):
            continue

        if new_parent_idx is not None:
            move_corrections.append(c)
        elif new_level is not None:
            old_level = nodes[idx]["level"]
            nodes[idx]["level"] = new_level
            print(f"  [AI修正-level] #{idx} '{nodes[idx]['title'][:30]}' "
                  f"level {old_level}→{new_level}  {reason}")

    if move_corrections:
        nodes = _apply_move_corrections(nodes, move_corrections)

    return nodes


def _apply_move_corrections(nodes: list[dict], corrections: list[dict]) -> list[dict]:
    corrections_sorted = sorted(corrections, key=lambda c: c["index"], reverse=True)

    for c in corrections_sorted:
        idx = c["index"]
        new_parent_idx = c["new_parent_index"]
        new_level = c.get("new_level")
        reason = c.get("reason", "")

        if idx == new_parent_idx:
            continue
        if not (0 <= idx < len(nodes)) or not (0 <= new_parent_idx < len(nodes)):
            continue

        node = nodes.pop(idx)
        if new_level is not None and 1 <= new_level <= 6:
            node["level"] = new_level

        adjusted_parent_idx = new_parent_idx if new_parent_idx < idx else new_parent_idx - 1
        insert_pos = adjusted_parent_idx + 1
        nodes.insert(insert_pos, node)

        print(f"  [AI修正-move] '{node['title'][:30]}' "
              f"移至节点#{adjusted_parent_idx}之后，level→{node['level']}  {reason}")

    return nodes


# ─────────────────────────────────────────
# 内部：Prompt 构建
# ─────────────────────────────────────────

def _build_prompt(nodes: list[dict], source_md: str, index_field: str = None) -> str:
    nodes_for_ai = []
    for i, node in enumerate(nodes):
        idx = node.get(index_field, i) if index_field else i
        nodes_for_ai.append({
            "index": idx,
            "level": node["level"],
            "title": node["title"][:40],
        })
    nodes_json = json.dumps(nodes_for_ai, ensure_ascii=False, indent=2)

    source_snippet = source_md[:6000] if len(source_md) > 6000 else source_md

    return f"""你是一位文档结构专家，擅长判断知识体系的层级关系。

## 任务
对照原文 Markdown，检查节点列表的层级是否正确。
找出层级错误或位置错误的节点，给出最小化修正。

## 原文 Markdown
{source_snippet}

## 当前节点列表（规则引擎已处理）
{nodes_json}

## 判断标准
1. 以原文结构为准：节点的层级应忠实反映原文的行文结构，不要根据学科知识体系重组
2. 编号优先：title 中有明确编号（第X章/第X节/一、/（一）/1.1）的，层级必须符合编号体系
3. 位置错误：节点出现在原文中明显不属于当前父节点的段落里
4. 跳层可疑：连续节点 level 差超过 2 时需要核查原文
5. 宁可少改：不确定时保持原样，只修正有原文依据的错误

## 输出格式
严格返回 JSON，不输出任何其他内容：
{{
  "corrections": [
    {{
      "index": <节点序号>,
      "new_level": <修正后层级，1-6>,
      "new_parent_index": <移动到此节点之后，可选，仅位置错误时填写>,
      "reason": "<15字以内>"
    }}
  ]
}}

无需修正时返回：{{"corrections": []}}"""


# ─────────────────────────────────────────
# 内部：工具函数
# ─────────────────────────────────────────

def _estimate_tokens(nodes: list[dict], source_md: str) -> int:
    source_tokens = len(source_md) // 2
    node_tokens = len(nodes) * 20
    return source_tokens + node_tokens


def _build_windowed_source(nodes: list[dict], source_md: str, window_chars: int = 100) -> str:
    snippets = []
    for node in nodes:
        title = node["title"][:20]
        pos = source_md.find(title)
        if pos == -1:
            continue
        start = max(0, pos - window_chars)
        end = min(len(source_md), pos + len(title) + window_chars)
        snippets.append(source_md[start:end])

    if not snippets:
        return source_md[:3000]

    seen = set()
    unique = []
    for s in snippets:
        key = s[:30]
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return "\n---\n".join(unique)


def _split_by_h2(source_md: str) -> list[str]:
    parts = re.split(r'(?=^## )', source_md, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


def _assign_nodes_to_segments(
    nodes: list[dict],
    segments: list[str],
) -> list[tuple[str, list[int]]]:
    result = [(seg, []) for seg in segments]

    for node_idx, node in enumerate(nodes):
        title = node["title"][:20]
        assigned = False
        for seg_idx, seg in enumerate(segments):
            if title in seg:
                result[seg_idx][1].append(node_idx)
                assigned = True
                break
        if not assigned:
            result[-1][1].append(node_idx)

    return result
