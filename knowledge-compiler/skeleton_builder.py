#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 骨架生成 —— 只输出标题层级与原文段落归属，**绝不改写正文**。

与旧 llm_structurer 的根本区别
----------------------------
旧做法是"一步到位"：让模型同时产出 title + content。实测后果：
  · 每片只喂 6000/8000 字 → 尾部约 25% 原文模型根本没看到；
  · max_tokens 2000，输出 JSON 一旦被截断就解析失败；
  · 失败后退化成"仅留前 10 段 × 500 字"的兜底树，再次丢字；
  · content 是模型改写过的，不再是原文（对要背诵的备考资料是硬伤）。

本模块只问模型两件事：**怎么分层**、**哪几段属于谁**。原文由
content_anchor 按段落序号原样挂载，模型没有机会碰正文。

输出契约
--------
{
  "title": "本片段主旨",
  "children": [
    {"title": "小节名", "src_range": [0, 3], "children": [
        {"title": "知识点名", "src_range": [3, 5], "children": []}
    ]}
  ]
}
src_range 是**半开区间** [start, end)，下标对应提示词里编号的段落。
只有真正持有原文的节点才写 src_range；纯结构节点不写。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from app_settings import chat_body_extras, chat_headers
except ImportError:      # 以脚本方式直接运行本文件时
    def chat_headers(base_url, api_key):
        return {"Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"}

    def chat_body_extras(base_url):
        return {}

from content_anchor import (  # noqa: E402
    anchor_skeleton, assert_no_content_loss, paragraphs_to_tree,
    paragraphs_text, split_paragraphs,
)

API_TIMEOUT = 60
REQUEST_DELAY = 0.3
MAX_RETRY = 2

# 单次请求最多带多少段（超过则截断到前面，但绝不改变正文内容本身）
MAX_PARAS_PER_CALL = 120


# ────────────────────────────────────────────
# 提示词
# ────────────────────────────────────────────

def build_skeleton_prompt(paragraphs: list[str], doc_hint: str = "") -> str:
    """把原文段落编号后交给模型，只要结构与归属。"""
    lines = []
    for i, p in enumerate(paragraphs):
        head = p.replace('\n', ' ').strip()
        if len(head) > 160:
            head = head[:160] + "…"
        lines.append(f"[{i}] {head}")
    body = "\n".join(lines)
    hint = f"\n资料类型提示：{doc_hint}\n" if doc_hint else ""

    return f"""你是教育类教材的结构分析员。下面是同一段文本被切成的 {len(paragraphs)} 个段落（已编号）。
你的任务**只是搭骨架**：划出知识层级，并说清每一段属于哪个节点。

{hint}
## 原文段落
{body}

## 你要做什么
1. 识别主题 → 子主题 → 知识点的层级（建议 2–4 层，最多 5 层）。
2. 为**真正持有原文的节点**标注它占的段落区间 `src_range: [起, 止)`（左闭右开，止不含）。
   只有持有正文的节点写 `src_range`；纯结构的父节点不要写。
3. 标题要简洁、能反映该节点下的内容主旨。

## 你不许做的事
- 不许改写、概括或重新组织段落文字。原文会由程序按你给的区间原样挂载。
- 不许遗漏、合并或省略任何段落：所有段落都必须被某个 `src_range` 覆盖到。
- 不许写 content 字段。

## 输出格式
严格输出 JSON，不要输出任何其他内容：
{{
  "title": "本片段主旨标题",
  "children": [
    {{
      "title": "小节标题",
      "src_range": [0, 3],
      "children": [
        {{"title": "知识点标题", "src_range": [3, 5], "children": []}}
      ]
    }},
    {{"title": "另一个小节", "src_range": [5, 8], "children": []}}
  ]
}}

## 约束
- 所有 src_range 合起来必须恰好覆盖 [0, {len(paragraphs)})，且互不重叠
- 每个 src_range 满足 0 ≤ start < end ≤ {len(paragraphs)}
- children 是数组；没有下级就写 []
- 标题不要带 markdown 井号"""


# ────────────────────────────────────────────
# 解析与校验
# ────────────────────────────────────────────

_MAX_TITLE_LEN = 60


def _clean_title(t, fallback: str) -> str:
    s = (t or "").strip()
    s = re.sub(r'^\s*#{1,6}\s+', '', s)
    s = re.sub(r'\s+', ' ', s).strip(' -—:：,，、')
    if not s:
        return fallback
    return s[:_MAX_TITLE_LEN].rstrip()


def parse_skeleton_response(raw: str, n_paras: int | None = None) -> dict | None:
    """解析模型输出，返回骨架 dict；任何不合法都返回 None（交给降级路径）。

    n_paras 提供时会一并校验区间不越界——这样能在重试循环里就判失败并重试，
    而不是产出一个注定要被锚定层丢弃的骨架。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    data = None
    try:
        data = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except Exception:
                return None
    if not isinstance(data, dict):
        return None

    counter = [0]
    overflow = [False]

    def _norm(node, depth):
        if not isinstance(node, dict):
            return None
        if depth > 6:
            overflow[0] = True
            return None
        kids_in = node.get("children")
        children = []
        if isinstance(kids_in, list):
            for c in kids_in:
                nc = _norm(c, depth + 1)
                if nc is not None:
                    children.append(nc)
        elif kids_in is not None:
            return None

        rng = None
        r = node.get("src_range")
        if isinstance(r, (list, tuple)) and len(r) == 2:
            try:
                a, b = int(r[0]), int(r[1])
                if not (0 <= a < b):
                    return None
                if n_paras is not None and b > n_paras:
                    return None
                rng = [a, b]
            except (TypeError, ValueError, OverflowError):
                return None

        title = _clean_title(node.get("title") or node.get("name"),
                             f"小节 {counter[0] + 1}")
        counter[0] += 1
        out = {"title": title, "children": children}
        if rng is not None:
            out["src_range"] = rng
        return out

    root = _norm(data, 0)
    if root is None or overflow[0]:
        # 深度超限属契约违反：整棵拒绝，交给重试/降级，
        # 而不是悄悄截断子树（那会丢掉带 src_range 的节点）。
        return None
    if not root.get("children"):
        return None          # 只有一个孤立标题，没有骨架价值
    return root


# ────────────────────────────────────────────
# 调用
# ────────────────────────────────────────────

async def generate_skeleton(client, paragraphs: list[str], url: str, api_key: str,
                            model: str, doc_hint: str = "") -> dict | None:
    """让模型为段落序列生成骨架。失败返回 None。"""
    paras = paragraphs[:MAX_PARAS_PER_CALL]
    prompt = build_skeleton_prompt(paras, doc_hint)
    payload = {
        "model": model,
        "max_tokens": 4000,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
        **chat_body_extras(url),
    }
    last = ""
    for attempt in (1, MAX_RETRY + 1):
        try:
            resp = await client.post(
                url, json=payload,
                headers=chat_headers(url, api_key),
                timeout=API_TIMEOUT)
            if resp.status_code != 200:
                last = f"HTTP {resp.status_code}"
                if resp.status_code == 429:
                    await asyncio.sleep(3 * attempt)
                continue
            content = resp.json()["choices"][0]["message"]["content"]
            sk = parse_skeleton_response(content, n_paras=len(paras))
            if sk is not None:
                return sk
            last = "骨架解析失败/不合法"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            break
    if last:
        print(f"  [骨架生成跳过] {last}")
    return None


# ────────────────────────────────────────────
# 高层入口：片段 → 节点树
# ────────────────────────────────────────────

def fragment_to_tree(text: str, skeleton: dict | None,
                     fallback_title: str = "未命名") -> tuple[dict, dict]:
    """把片段变成节点树：有骨架→按区间锚定原文；无骨架→段落保底分块。

    两种情况下原文都**绝不丢失**（见 assert_no_content_loss）。
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        node = {"title": fallback_title, "level": 1, "content": (text or "").strip(),
                "children": [], "captions": [], "src_index": 0}
        return node, {"route": "empty", "paragraphs": 0, "lost": 0}

    if skeleton is not None:
        root, rep = anchor_skeleton(skeleton, paragraphs)
        root["title"] = _clean_title(root.get("title"), fallback_title) or fallback_title
        chk = assert_no_content_loss(root, paragraphs)
        lost = list(chk.get("lost") or [])

        # generate_skeleton 只把前 MAX_PARAS_PER_CALL 段送模型（长片段保护）。
        # 如果骨架没有覆盖全部段落，优先把尾部未覆盖的原文接成一个“续”节点；
        # 若丢失不是连续尾部（可能是模型区间有洞），退回段落保底，绝不静默丢字。
        if lost:
            is_suffix = lost == list(range(lost[0], len(paragraphs)))
            if is_suffix:
                cont = {
                    "title": "（后续内容）",
                    "level": 2,
                    "content": paragraphs_text(paragraphs[lost[0]:]),
                    "children": [],
                    "captions": [],
                    "src_index": lost[0],
                }
                root.setdefault("children", []).append(cont)
                rep["anchored_by"] = "range+continuation"
                rep["continued_paragraphs"] = len(lost)
                rep["skeleton_covered_paragraphs"] = lost[0]
                rep["lost_paragraphs"] = 0
                rep["lost"] = 0
                rep["nodes"] = int(rep.get("nodes") or 0) + 1
                rep["route"] = "skeleton_continuation"
                rep["detail"] = (
                    f"骨架只覆盖前 {lost[0]} 段，后续 {len(lost)} 段已原样接入"
                )
                return root, rep

            root = paragraphs_to_tree(paragraphs, fallback_title)
            chk = assert_no_content_loss(root, paragraphs)
            return root, {"route": "prose_fallback", "paragraphs": len(paragraphs),
                          "lost": len(chk["lost"]),
                          "anchored_by": "chunk", "nodes": 1 + len(root["children"]),
                          "conflicts": 0,
                          "detail": "骨架未覆盖全部段落，已改为段落保底"}

        rep["lost"] = 0
        rep["route"] = "skeleton"
        return root, rep

    root = paragraphs_to_tree(paragraphs, fallback_title)
    chk = assert_no_content_loss(root, paragraphs)
    return root, {"route": "prose_fallback", "paragraphs": len(paragraphs),
                  "lost": len(chk["lost"]),
                  "anchored_by": "chunk", "nodes": 1 + len(root["children"]),
                  "conflicts": 0, "detail": "无骨架，按段落分块保底"}
