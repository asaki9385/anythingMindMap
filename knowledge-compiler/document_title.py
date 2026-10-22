"""资料标题：为一次解析生成可读标题，并支持用户覆盖。

标题是“资料级元数据”，与知识树根节点 title 同步：
- 新任务：解析后先给确定性兜底，再用一次短 LLM 调用润色；
- 旧任务/本地知识树：根节点 title 即资料标题；
- 用户重命名通过 /api/rename 写回 tree + task_state + cache。
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterable

import httpx

import app_settings

MAX_TITLE_LEN = 60

_GENERIC_TITLE_RE = re.compile(
    r"^(?:knowledge\s*tree|知识树|资料|文档|未命名(?:资料)?|"
    r"task(?:[_\-\s]*[0-9a-z]+)?|part[_\-\s]*\d+|"
    r"\d+\s*个文档|\d+)$",
    re.IGNORECASE,
)
_HEX_ID_RE = re.compile(r"^[0-9a-f]{6,}$", re.IGNORECASE)


def clean_source_title(name: str) -> str:
    """把 `01_02.333大纲.pdf` / `Part_1_tree_enhanced.json` 清洗成可读标题。"""
    text = os.path.basename(str(name or "").strip())
    text = re.sub(r"\.(?:pdf|docx|txt|md|json)$", "", text, flags=re.IGNORECASE)
    if _GENERIC_TITLE_RE.match(text):
        return ""
    # 上传时的序号前缀：01_ / 02- / 003_
    text = re.sub(r"^\d{1,3}[_\-\s]+", "", text)
    # 文件名自身的零填充序号：02.333… → 333…
    text = re.sub(r"^0\d[.\-、]\s*", "", text)
    # 老本地树的分片前缀：Part_1_ / Part 1 -
    text = re.sub(r"^part[_\-\s]*\d+[_\-\s]*", "", text, flags=re.IGNORECASE)
    # 老文件的内部后缀：_tree_enhanced / _enhanced / _tree
    text = re.sub(r"[_\-\s]*(?:tree[_\-\s]*)?enhanced[_\-\s]*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_\-\s]*tree[_\-\s]*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    if not text or _HEX_ID_RE.match(text) or _GENERIC_TITLE_RE.match(text):
        return ""
    return text[:MAX_TITLE_LEN]


def module_titles(tree: dict, limit: int = 12) -> list[str]:
    """取前端会展示的模块标题（单册包装会解包）。"""
    if not isinstance(tree, dict):
        return []
    top = tree.get("children") or []
    modules = top
    if len(top) == 1 and isinstance(top[0], dict) and (top[0].get("children") or []):
        modules = top[0].get("children") or []
    titles: list[str] = []
    for node in modules:
        if not isinstance(node, dict):
            continue
        title = (node.get("title") or node.get("name") or "").strip()
        if title and not _GENERIC_TITLE_RE.match(title):
            titles.append(title[:MAX_TITLE_LEN])
        if len(titles) >= limit:
            break
    return titles


def _chapter_range_title(modules: list[str]) -> str:
    """多章资料的确定性标题：主题（第一章–第N章）。"""
    if not modules:
        return ""
    first = modules[0]
    if len(modules) == 1:
        return first[:MAX_TITLE_LEN]
    label_re = re.compile(r"^(第[一二三四五六七八九十百千\d]+章)")
    m1 = label_re.match(first)
    m2 = label_re.match(modules[-1])
    if m1 and m2 and m1.group(1) != m2.group(1):
        theme = first[m1.end():].strip(" ：:、.-")
        if theme:
            return f"{theme}（{m1.group(1)}–{m2.group(1)}）"[:MAX_TITLE_LEN]
    return first[:MAX_TITLE_LEN]


def fallback_document_title(source_names: Iterable[str] | None, tree: dict) -> str:
    """无 LLM 时的确定性标题。"""
    names = [str(x).strip() for x in (source_names or []) if str(x).strip()]
    cleaned = [c for c in (clean_source_title(n) for n in names) if c]
    if cleaned:
        if len(names) > 1:
            title = f"{cleaned[0]} 等 {len(names)} 份资料"
        else:
            title = cleaned[0]
        return title[:MAX_TITLE_LEN]

    root_title = clean_source_title((tree or {}).get("title") or "")
    if root_title:
        return root_title[:MAX_TITLE_LEN]

    modules = module_titles(tree or {}, limit=12)
    if modules:
        return _chapter_range_title(modules)
    return "未命名资料"


def build_title_prompt(source_names: Iterable[str] | None, tree: dict) -> str:
    names = "、".join(str(x).strip() for x in (source_names or []) if str(x).strip())
    names = names[:500] or "未提供"
    chapters = "；".join(module_titles(tree or {}, limit=10)) or "未识别"
    return (
        "你是学习资料命名助手。请根据文件名和章节结构，为这份资料生成一个简洁、"
        "具体、可读的中文标题。\n"
        "要求：\n"
        "- 6–24 个汉字（英文资料可用英文）\n"
        "- 标题要概括整份资料，不要只照抄第一章标题\n"
        "- 如果文件名只是编号，请依据章节主题命名\n"
        "- 多章资料可用「主题（第X–Y章）」的形式\n"
        "- 不要书名号、引号、句号或解释\n"
        "- 不要使用“知识树”“资料”“文档”“解析”等泛词\n"
        "- 只输出标题本身\n"
        f"文件名：{names}\n"
        f"章节：{chapters}\n"
    )


def parse_title_response(raw: str, fallback: str) -> str:
    """严格解析 LLM 输出；不合格就退回确定性标题。"""
    text = (raw or "").strip()
    if not text:
        return fallback
    text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                text = str(obj.get("title") or obj.get("name") or "").strip()
        except Exception:
            pass
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return fallback
    title = re.sub(r"^(?:标题|title)\s*[:：]\s*", "", lines[0], flags=re.IGNORECASE)
    title = title.strip().strip("\"'`“”‘’《》")
    title = re.sub(r"[。；;，,]+$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    if not title or len(title) < 2 or len(title) > MAX_TITLE_LEN:
        return fallback
    if any(token in title for token in ("无法", "抱歉", "不能生成", "生成的标题", "标题为")):
        return fallback
    return title


def apply_document_title(tree: dict, title: str) -> dict:
    """把资料标题写到根节点；单册包装同时同步包装节点。"""
    if not isinstance(tree, dict):
        return tree
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    title = title[:MAX_TITLE_LEN] or "未命名资料"
    tree["title"] = title
    children = tree.get("children") or []
    if len(children) == 1 and isinstance(children[0], dict) and (children[0].get("children") or []):
        children[0]["title"] = title
    return tree


async def generate_document_title(tree: dict, source_names: Iterable[str] | None,
                                  chat: dict | None, usage=None) -> str:
    """用一次短请求生成标题；任何失败都静默退回兜底标题。"""
    fallback = fallback_document_title(source_names, tree)
    if not chat or not (chat.get("api_key") or "").strip():
        return fallback
    base_url = chat.get("base_url") or app_settings.DEFAULT_BASE_URL
    model = (chat.get("model") or app_settings.DEFAULT_MODEL).strip()
    payload = {
        "model": model,
        "max_tokens": 60,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": build_title_prompt(source_names, tree)}],
        **app_settings.chat_body_extras(base_url),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                base_url,
                json=payload,
                headers=app_settings.chat_headers(base_url, chat.get("api_key", "")),
            )
        if resp.status_code != 200:
            return fallback
        if usage is not None:
            try:
                usage.from_response(resp, model)
            except Exception:
                pass
        raw = resp.json()["choices"][0]["message"]["content"]
        title = parse_title_response(raw, fallback)
        modules = module_titles(tree, limit=12)
        if len(modules) > 1 and title in modules:
            return fallback
        return title
    except Exception:
        return fallback
