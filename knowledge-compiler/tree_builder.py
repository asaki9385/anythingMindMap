"""
333教育综合 - MD → 知识树 Builder (并行版)
每个MD文件独立处理，产出单独的tree JSON
"""

import re
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# 只过滤真正的页面噪音（封面残留、水印）。曾经把「丹丹攻略 / 丹丹导学 /
# 考情分析 / 本章自测表 / 精华提要 / 知识导图」等**内容型框标题**也当噪音整条丢弃，
# 后果是框内正文静默并进上一节（例如把「颜之推家庭教育思想的启示」挂进韩愈的
# 「论尊师重道」下），夹在框标题与下一个标题之间的段落还会直接丢失。
# 这些标题一律保留成节点，框内容（攻略/自测/考情）是备考正文本身。
NOISE_EXACT = {
    "333", "教育综合", "考研大纲知识清单", "前言",
    "参考答案", "【参考答案】",
}
NOISE_CONTAINS = [
    "公众号", "微信",
]

def is_noise(title):
    t = title.strip()
    if t in NOISE_EXACT:
        return True
    return any(kw in t for kw in NOISE_CONTAINS)


# =============================================================================
# 目录页检测和章节标题合并
# =============================================================================

# 目录页特征：连续的章节标题 + 页码格式
# 支持带 # 前缀和不带 # 前缀的格式
_TOC_CHAPTER_RE = re.compile(r'^(?:#\s+)?第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+章\s+.+/\s*\d+$')
_TOC_SECTION_RE = re.compile(r'^(?:#\s+)?第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+节\s+.+/\s*\d+$')
_TOC_ITEM_RE = re.compile(r'^(?:本章自测表|参考文献|思考题|习题)\s*/\s*\d+$')

# 章节标题模式（不含页码）
_CHAPTER_TITLE_RE = re.compile(r'^第[一二三四五六七八九十百千\d①②③④⑤⑥⑦⑧⑨⑩●○◎◉●]+章$')

# 裸容器标题（"知识点"/"考点" 单独一行，名称被拆到下一行，OCR 常见）
_BARE_CONTAINER_RE = re.compile(r'^(知识点|考点)$')


def detect_toc_section(lines, start_idx):
    """检测从 start_idx 开始是否是目录页内容。

    目录页特征：
    1. 以 # 第X章 标题 / 页码 开头
    2. 后面跟着 第X节 标题 / 页码 格式的子章节
    3. 可能包含 "本章自测表 / 页码" 等

    返回：(is_toc, end_idx)
      - is_toc: 是否是目录页
      - end_idx: 目录页结束的索引（下一行）
    """
    if start_idx >= len(lines):
        return False, start_idx

    line = lines[start_idx].strip()

    # 检查是否是目录页的章节标题（带页码）
    if not _TOC_CHAPTER_RE.match(line):
        return False, start_idx

    # 向后扫描，寻找目录页的结束
    idx = start_idx + 1
    while idx < len(lines):
        line = lines[idx].strip()

        # 空行跳过
        if not line:
            idx += 1
            continue

        # 检查是否是目录页的子章节（带页码）
        if _TOC_SECTION_RE.match(line) or _TOC_ITEM_RE.match(line):
            idx += 1
            continue

        # 检查是否是下一个目录页的章节标题（带页码）
        if _TOC_CHAPTER_RE.match(line):
            # 继续扫描下一个章节
            idx += 1
            continue

        # 遇到其他内容，目录页结束
        break

    return True, idx


def _is_box_title(title: str) -> bool:
    """内容框标题（知识导图/丹丹攻略/自测表…）：是容器，不参与章名合并。"""
    try:
        from hierarchy_repair import is_box_title
        return is_box_title(title)
    except Exception:
        return False


def _src_index(node: dict) -> int:
    """节点在原文中的顺序序号；缺省给一个大值排在最后。

    src_index 是"无编号兄弟组"恢复文档序的唯一依据（编号缺失或 OCR 丢了编号时）。
    """
    try:
        return int(node.get("src_index"))
    except (TypeError, ValueError):
        return 1 << 30


def merge_split_chapter_titles(nodes):
    """合并被拆分的章节标题。

    例如：
      "# 第一章"
      "# 心理发展与教育"

    应该合并为：
      "# 第一章 心理发展与教育"
    """
    if not nodes:
        return nodes

    merged = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        title = node.get('title', '')

        # 检查是否是单独的章节标题（如 "第一章"），或裸容器标题（"知识点"）
        if (_CHAPTER_TITLE_RE.match(title) or _BARE_CONTAINER_RE.match(title)) and i + 1 < len(nodes):
            next_node = nodes[i + 1]
            next_title = next_node.get('title', '')

            # 检查下一个节点是否是章节标题的一部分
            # 且下一个节点没有自己的编号体系
            if (next_title and
                not _BARE_CONTAINER_RE.match(next_title) and
                not re.match(r'^第[一二三四五六七八九十百千\d]+[章节部分]', next_title) and
                not re.match(r'^[一二三四五六七八九十]+[、，,]', next_title) and
                not re.match(r'^[（(【〔][一二三四五六七八九十\d]+[）)】〕]', next_title) and
                not re.match(r'^\d+(?:\.\d+)*', next_title) and
                not re.match(r'^[①-⑳]', next_title) and
                not _is_box_title(next_title) and
                len(next_title) < 20):  # 标题长度限制

                # 合并标题
                merged_title = f"{title} {next_title}"
                merged_node = dict(node)
                merged_node['title'] = merged_title
                # 保留更靠前的源序号：合并节点的位置应以被吸收部分之前的那一行为准
                merged_node['src_index'] = min(
                    _src_index(node), _src_index(next_node))
                merged.append(merged_node)
                i += 2  # 跳过下一个节点
                continue

        merged.append(node)
        i += 1

    return merged

def get_level(title, hashes):
    t = title.strip()
    if re.match(r'^第[一二三四五六七八九十百\d]+章', t): return 1
    if re.match(r'^第[一二三四五六七八九十百\d]+节', t): return 2
    if re.match(r'^知识点[一二三四五六七八九十\d]+', t):  return 3
    if re.match(r'^[（(]\d+[）)]', t): return 5
    if re.match(r'^[（(][一二三四五六七八九十]+[）)]', t): return 4
    # English numbered sections: depth by number-segment count
    if re.match(r'^\d+\.\d+\.\d+\s', t): return 3
    if re.match(r'^\d+\.\d+\s', t): return 2
    # 单独的 "数字. " 格式（如 "1. 弗洛伊德关于自我发展的理论"）
    # 这种格式通常是知识点的子节点，应该使用更高的层级
    if re.match(r'^\d+\.\s', t): return 5
    # Chinese comma-separated sub-points (e.g. "1、要点")
    if re.match(r'^\d+[、]', t): return 5
    # Other dotted numbered items without trailing space (e.g. "1.text")
    if re.match(r'^\d+\.\s*\S', t): return 5
    return min(hashes, 5)

_EXPLICIT_LEVEL_RE = re.compile(
    r'^(第[一二三四五六七八九十百\d]+[章节]|知识点[一二三四五六七八九十\d]+|[（(][\d一二三四五六七八九十]+[）)]|\d+[\.、])'
)
_CHAPTER_RE = re.compile(r'^第[一二三四五六七八九十百\d]+章')
_SECTION_RE = re.compile(r'^第[一二三四五六七八九十百\d]+节')
# English numbered section patterns
_EN_CHAPTER_RE = re.compile(r'^\d+\.\s')
_EN_SECTION_RE = re.compile(r'^\d+\.\d+\s')


def _is_chapter(t):
    return bool(_CHAPTER_RE.match(t) or _EN_CHAPTER_RE.match(t))


def _is_section(t):
    return bool(_SECTION_RE.match(t) or _EN_SECTION_RE.match(t))


def adjust_standalone_levels(nodes):
    """Demote headers without explicit level patterns that appear inside
    a chapter or section, so they nest correctly instead of capturing
    sibling sections.

    E.g. "Background" (L1 by fallback) inside "2. Methods" (L1)
    is demoted to L2, allowing "2.1 Overview" (L2) to pop it off the
    stack and become a sibling.
    """
    if not nodes:
        return nodes

    # Pre-scan: if the file starts inside a section (no chapter header
    # before the first section), seed the section context so continuation
    # files (e.g. Part_11 continuing Part_10) don't lose hierarchy.
    active_chapter_level = 0
    active_section_level = 0
    first_section = None
    for node in nodes:
        t = node['title'].strip()
        if _is_chapter(t):
            break
        if _is_section(t):
            first_section = node
            break
    if first_section is not None:
        active_section_level = 2

    for node in nodes:
        t = node['title'].strip()
        if _is_chapter(t):
            active_chapter_level = 1
            active_section_level = 0
        elif _is_section(t):
            active_section_level = 2
        elif not _EXPLICIT_LEVEL_RE.match(t):
            if active_section_level > 0 and node['level'] <= active_section_level:
                node['level'] = active_section_level + 1
            elif active_chapter_level > 0 and node['level'] <= active_chapter_level:
                node['level'] = active_chapter_level + 1
    return nodes

def merge_chapter_titles(nodes):
    result = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        t = node['title'].strip()
        is_ch = re.match(r'^第[一二三四五六七八九十百\d]+章$', t)
        is_sec = re.match(r'^第[一二三四五六七八九十百\d]+节$', t)
        is_container = _BARE_CONTAINER_RE.match(t)
        if (is_ch or is_sec or is_container) and i + 1 < len(nodes):
            nxt = nodes[i+1]
            nxt_t = nxt['title'].strip()
            if (not re.match(r'^第[一二三四五六七八九十百\d]+[章节]', nxt_t)
                    and not _BARE_CONTAINER_RE.match(nxt_t)
                    and not _is_box_title(nxt_t) and nxt['level'] <= 2):
                merged = dict(node)
                merged['title'] = t + ' ' + nxt_t
                merged['content'] = nxt.get('content','') or node.get('content','')
                merged['children'] = []
                # 合并节点取更靠前的源序号，避免被排到后一个标题的位置之后
                merged['src_index'] = min(_src_index(node), _src_index(nxt))
                result.append(merged)
                i += 2
                continue
        result.append(node)
        i += 1
    return result

CAPTION_RE = re.compile(
    r'^(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|图\s*\d+|表\s*\d+)[：:．.]?\s*(.+)',
    re.IGNORECASE,
)


def parse_md_to_nodes(md_text):
    """Markdown → flat nodes，尽量保留所有原文块。

    这里仍然做 Markdown 结构解析，但不把图片、HTML、代码块或首个标题前的
    导言静默丢掉。真正的版面/坐标信息由 Document IR 层负责；本函数是
    现有 regex 路径的保守兜底层，必须先保证正文不丢。
    """
    lines = md_text.split('\n')
    nodes = []
    current = None
    buf = []
    preamble_buf = []
    preamble_start = None

    in_code = False
    in_mermaid = False
    in_table = False
    in_html_table = False
    in_formula = False

    def append_raw(raw_line):
        """把无法结构化的行挂到当前节点；尚无节点时归入文档导言。"""
        if current is None:
            preamble_buf.append(raw_line)
        else:
            buf.append(raw_line)

    def flush_current():
        nonlocal current, buf, in_code, in_mermaid, in_table, in_html_table, in_formula
        if current is not None:
            current['content'] = '\n'.join(buf).strip()
            nodes.append(current)
        buf = []
        in_code = False
        in_mermaid = False
        in_table = False
        in_html_table = False
        in_formula = False
        current = None

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()

        # 代码围栏必须先于标题判断消费；否则代码里的 "# comment" 会被当成标题。
        if in_code or in_mermaid:
            append_raw(line)
            if s == '```':
                in_code = False
                in_mermaid = False
            i += 1
            continue
        if s.startswith('```'):
            append_raw(line)
            if s.lower().startswith('```mermaid'):
                in_mermaid = True
            else:
                in_code = True
            i += 1
            continue

        # 目录页判断放在代码围栏之后，避免代码里的“第X章/页码”被误跳过。
        is_toc, end_idx = detect_toc_section(lines, i)
        if is_toc:
            i = end_idx
            continue

        # 处理正常内容
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            flush_current()
            hashes = len(m.group(1))
            title = m.group(2).strip()
            current = {
                'title': title,
                'level': get_level(title, hashes),
                'content': '',
                'children': [],
                'captions': [],
                'src_index': i,
                'src_hash': hashes,
                # 噪音标题先保留，若最终没有正文/子节点再丢弃。
                '_is_noise': is_noise(title),
            }
            i += 1
            continue

        if current is None:
            if s:
                if preamble_start is None:
                    preamble_start = i
                preamble_buf.append(line)
            i += 1
            continue

        # Preserve HTML <table> blocks (MinerU table output)
        if s.lower().startswith('<table'):
            in_html_table = True
            buf.append(line)
            if s.lower().endswith('</table>'):
                in_html_table = False
            i += 1
            continue
        if in_html_table:
            buf.append(line)
            if s.lower().endswith('</table>'):
                in_html_table = False
            i += 1
            continue
        # Preserve pipe tables
        if s.startswith('|'):
            in_table = True
            buf.append(line)
            i += 1
            continue
        if in_table and s == '':
            in_table = False
            i += 1
            continue
        in_table = False
        # Preserve LaTeX formula blocks ($$...$$)
        if s.startswith('$$'):
            in_formula = not in_formula
            buf.append(line)
            i += 1
            continue
        if in_formula:
            buf.append(line)
            i += 1
            continue
        # Caption detection (Figure/Table/chart labels)
        cap_m = CAPTION_RE.match(s)
        if cap_m:
            label = cap_m.group(1).strip()
            caption_text = cap_m.group(2).strip()
            buf.append(f"[图表] {label}: {caption_text}")
            current.setdefault('captions', []).append({"label": label, "text": caption_text})
            i += 1
            continue
        # 图片、非表格 HTML、普通正文/无围栏文本全部保留。
        if s:
            buf.append(line)
        i += 1

    flush_current()

    # 只丢弃“确实没有正文、也没有子节点”的噪音标题。
    kept = []
    for node in nodes:
        noise = node.pop('_is_noise', False)
        if noise and not (node.get('content') or '').strip() and not node.get('children'):
            continue
        kept.append(node)
    nodes = kept

    # 首个标题之前的内容不再静默丢弃；挂到一个明确的导言节点。
    preamble_text = '\n'.join(preamble_buf).strip()
    if preamble_text:
        nodes.insert(0, {
            'title': '文档导言',
            'level': 1,
            'content': preamble_text,
            'children': [],
            'captions': [],
            'src_index': preamble_start if preamble_start is not None else 0,
            'src_hash': 1,
        })

    # 合并被拆分的章节标题
    nodes = merge_split_chapter_titles(nodes)

    return nodes


# =============================================================================
# 图片引用清理
#
# MinerU 的 Markdown 会把插图写成 `![](images/<hash>.jpg)`。这些图片文件
# 当前没有进入任务资产目录，前端也没有图片渲染器；如果原样保留，用户会在
# 正文里看到一长串 hash 路径。这里把本地图片引用从可读正文中移出，转存到
# 节点的 `images` 元数据字段：正文干净，引用本身也没有被静默丢弃。
# =============================================================================

_IMAGE_MD_RE = re.compile(
    r'!\[[^\]]*\]\(\s*(?:<)?([^)>\s]+)(?:>)?(?:\s+["\'][^"\']*["\'])?\s*\)'
)
_IMAGE_HTML_RE = re.compile(
    r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1[^>]*>",
    re.IGNORECASE,
)
_LOCAL_IMAGE_PATH_RE = re.compile(
    r'^(?:\./|/)?images/[A-Za-z0-9._%+-]+'
    r'(?:\.(?:jpe?g|png|gif|webp|bmp|svg))?(?:[?#].*)?$',
    re.IGNORECASE,
)
_BARE_IMAGE_LINE_RE = re.compile(
    r'^(?:\./|/)?images/[A-Za-z0-9._%+-]+'
    r'(?:\.(?:jpe?g|png|gif|webp|bmp|svg))?(?:[?#].*)?$',
    re.IGNORECASE,
)


def _is_local_image_path(value: str) -> bool:
    return bool(_LOCAL_IMAGE_PATH_RE.match((value or '').strip()))


def sanitize_text_images(text: str) -> tuple[str, list[str]]:
    """把本地图片引用从正文中移出，返回 (干净正文, 图片路径列表)。

    - 支持 Markdown 图片、HTML <img>、以及单独一行的 images/<hash>。
    - 代码围栏内的内容原样保留（代码里的路径不是图片引用）。
    - 只处理本地 `images/...` 路径；远程图片 URL 不动。
    """
    if not isinstance(text, str) or not text:
        return text, []
    images: list[str] = []
    out_lines: list[str] = []
    in_code = False

    def take_local(url: str) -> bool:
        url = (url or '').strip()
        if _is_local_image_path(url):
            images.append(url)
            return True
        return False

    for raw_line in text.split('\n'):
        stripped = raw_line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            out_lines.append(raw_line)
            continue
        if in_code:
            out_lines.append(raw_line)
            continue

        line = _IMAGE_HTML_RE.sub(
            lambda m: '' if take_local(m.group(2)) else m.group(0), raw_line)
        line = _IMAGE_MD_RE.sub(
            lambda m: '' if take_local(m.group(1)) else m.group(0), line)

        if line.strip() and _BARE_IMAGE_LINE_RE.match(line.strip()):
            images.append(line.strip())
            continue

        line = re.sub(r'[ \t]{2,}', ' ', line).rstrip()
        if line.strip():
            out_lines.append(line)
        elif not stripped:
            # 保留原文中的空行，维持段落边界。
            out_lines.append('')

    clean = '\n'.join(out_lines).strip()
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    # 去重但保持首次出现顺序。
    deduped = list(dict.fromkeys(images))
    return clean, deduped


def sanitize_tree_images(tree: dict) -> dict:
    """递归清理知识树里的本地图片引用，并收集到 node['images']。"""
    if not isinstance(tree, dict):
        return tree

    string_fields = ('content', 'summary', 'guide', 'analysis', 'raw_content')
    list_fields = ('exam_points', 'keywords', 'prerequisites', 'tables')

    def walk(node: dict) -> None:
        collected: list[str] = []

        for field in string_fields:
            value = node.get(field)
            if isinstance(value, str):
                clean, found = sanitize_text_images(value)
                node[field] = clean
                collected.extend(found)

        for field in list_fields:
            value = node.get(field)
            if not isinstance(value, list):
                continue
            cleaned_items = []
            for item in value:
                if isinstance(item, str):
                    clean, found = sanitize_text_images(item)
                    collected.extend(found)
                    if clean:
                        cleaned_items.append(clean)
                else:
                    cleaned_items.append(item)
            node[field] = cleaned_items

        existing = node.get('images')
        if isinstance(existing, list):
            collected = list(existing) + collected
        if collected:
            node['images'] = list(dict.fromkeys(
                str(x).strip() for x in collected if str(x).strip()))
        elif 'images' in node and not node.get('images'):
            node.pop('images', None)

        for child in node.get('children', []):
            if isinstance(child, dict):
                walk(child)

    walk(tree)
    return tree

def build_tree(nodes):
    if not nodes: return []
    nodes = merge_chapter_titles(nodes)
    roots = []
    stack = []
    for node in nodes:
        level = node['level']
        node = dict(node)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]['children'].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots

def _detect_hierarchy_anomalies(nodes, parent_level=0):
    """Scan tree for hierarchy anomalies. Returns list of {node, path, reason}."""
    anomalies = []
    for i, node in enumerate(nodes):
        level = node.get('level', 0)
        title = node.get('title', '')

        # Level gap > 1
        if parent_level > 0 and level > parent_level + 1:
            anomalies.append({
                "node": node, "title": title, "level": level,
                "expected_max": parent_level + 1,
                "reason": f"level jump {parent_level} -> {level}"
            })

        # 5+ consecutive same-level siblings
        if i >= 4:
            prev_levels = [nodes[j].get('level', 0) for j in range(i-4, i)]
            if all(l == level for l in prev_levels) and level == nodes[i-1].get('level', 0):
                anomalies.append({
                    "node": node, "title": title, "level": level,
                    "reason": f"5+ consecutive L{level} siblings"
                })

        # Recurse into children
        children = node.get('children', [])
        if children:
            anomalies.extend(_detect_hierarchy_anomalies(children, level))

    return anomalies


def validate_and_repair_hierarchy(roots, api_key):
    """Use LLM to validate and repair hierarchy anomalies.

    Falls back to original structure on failure.
    """
    anomalies = _detect_hierarchy_anomalies(roots)
    if not anomalies:
        return roots

    # Deduplicate by title
    seen = set()
    unique = []
    for a in anomalies:
        key = (a['title'], a['level'])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    if len(unique) > 30:
        unique = unique[:30]

    # Build prompt
    items = []
    for i, a in enumerate(unique):
        items.append(f"{i+1}. \"{a['title']}\" (当前level={a['level']}, 问题: {a['reason']})")

    prompt = f"""以下是文档标题层级识别结果中存在问题的条目。请判断每个标题的正确层级（1-5）。

标题列表:
{chr(10).join(items)}

规则:
- level 1 = 章/Chapter
- level 2 = 节/Section
- level 3 = 知识点/子节
- level 4 = 子知识点
- level 5 = 细节/条目

严格输出JSON数组，每个元素包含 "idx"（序号从0开始）和 "correct_level"（1-5的整数）:
[{{"idx": 0, "correct_level": 2}}, ...]

只输出确实需要修正的条目。如果某个条目当前level已经是合理的，不要包含它。"""

    import httpx
    # 端点/模型跟随统一配置或环境变量；没有任何可用凭证时跳过校验，保持原层级。
    try:
        import app_settings
        cfg = app_settings.resolve_chat(api_key=api_key or "") or {}
    except Exception:
        cfg = {}
    api_key = api_key or cfg.get("api_key", "")
    url = cfg.get("base_url") or "https://api.deepseek.com/chat/completions"
    model = cfg.get("model") or "deepseek-v4-flash"
    if not api_key:
        return roots
    try:
        resp = httpx.post(
            url,
            json={
                "model": model,
                "max_tokens": 500,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
                **app_settings.chat_body_extras(url),
            },
            headers=app_settings.chat_headers(url, api_key),
            timeout=20
        )
        if resp.status_code != 200:
            print(f"  Hierarchy validation HTTP {resp.status_code}, keeping original")
            return roots

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)

        # Handle both direct array and {"corrections": [...]} format
        corrections = result if isinstance(result, list) else result.get("corrections", result.get("repairs", []))

        # Apply corrections to a deep copy to preserve original on failure
        import copy
        roots_copy = copy.deepcopy(roots)
        # Re-detect anomalies on the copy to get mutable references
        unique_copy = []
        seen_copy = set()
        for a in _detect_hierarchy_anomalies(roots_copy):
            key = (a['title'], a['level'])
            if key not in seen_copy:
                seen_copy.add(key)
                unique_copy.append(a)
        unique_copy = unique_copy[:30]

        fixed = 0
        for c in corrections:
            idx = c.get('idx')
            new_level = c.get('correct_level')
            if idx is not None and new_level and 1 <= new_level <= 5 and idx < len(unique_copy):
                a = unique_copy[idx]
                if new_level != a['level']:
                    a['node']['level'] = new_level
                    fixed += 1

        if fixed > 0:
            print(f"  Hierarchy: fixed {fixed}/{len(unique_copy)} anomalies")
            flat = []
            for root in roots_copy:
                flat.extend(_flatten_for_rebuild(root))
            candidate = build_tree(flat)
            # 骨架守卫：改动层级会重建整棵树，重建不得增加骨架违规
            # （历史上出现过整节被弹成顶层独立根节点的情况）
            try:
                from outline_skeleton import check_skeleton
                before_n = len(check_skeleton(roots))
                after_n = len(check_skeleton(candidate))
            except Exception:
                before_n = after_n = 0
            if after_n <= before_n:
                roots = candidate
            else:
                print(f"  Hierarchy: rebuild would add skeleton violations "
                      f"({before_n} -> {after_n}), keeping original structure")

    except Exception as e:
        print(f"  Hierarchy validation failed: {e}, keeping original")

    return roots


def _flatten_for_rebuild(node, parent_level=0):
    """Flatten tree back to node list for rebuild_tree."""
    flat_node = {k: v for k, v in node.items() if k != 'children'}
    flat_node['children'] = []
    result = [flat_node]
    for child in node.get('children', []):
        result.extend(_flatten_for_rebuild(child, node['level']))
    return result


def build_single_tree(md_path: str, output_dir: str) -> dict:
    """Build tree from a single MD file, save to output_dir, return tree dict."""
    filename = os.path.splitext(os.path.basename(md_path))[0]
    with open(md_path, encoding='utf-8') as f:
        md_text = f.read().strip()

    # 结构前置层：恢复丢失的大纲标题（纯文本"第X节/知识点X"按序列连续性提升）
    try:
        from outline_skeleton import normalize_markdown_outline
        md_text, _skel = normalize_markdown_outline(md_text, doc_hint=filename)
    except Exception as e:
        print(f"  Outline normalization skipped for {filename}: {e}")

    nodes = parse_md_to_nodes(md_text)
    nodes = adjust_standalone_levels(nodes)
    tree_children = build_tree(nodes)
    # 结构前置层：同级序列校验 + 缺失边界占位修复
    try:
        from outline_skeleton import audit_tree_skeleton
        audit_tree_skeleton(tree_children)
    except Exception as e:
        print(f"  Skeleton audit skipped for {filename}: {e}")
    try:
        from placement_audit import fix_topic_placement
        fix_topic_placement(tree_children)
    except Exception as e:
        print(f"  Placement audit skipped for {filename}: {e}")
    tree = {"title": filename, "children": tree_children}
    tree = sanitize_tree_images(tree)

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{filename}_tree.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    node_count = sum(1 for _ in _iter_nodes(tree))
    return {"filename": filename, "path": out_path, "nodes": node_count, "top": len(tree_children)}

def _iter_nodes(node):
    yield node
    for c in node.get('children', []):
        yield from _iter_nodes(c)

def count_all(node):
    return 1 + sum(count_all(c) for c in node.get('children', []))

def print_tree(node, indent=0, max_depth=3):
    if indent > max_depth * 2: return
    cc = len(node.get('children', []))
    preview = node.get('content','')[:35].replace('\n',' ')
    suffix = f" ({cc}子)" if cc else ""
    suffix += f'  <- "{preview}..."' if preview else ""
    print("  "*indent + f"{'|--' if indent else '*'} [L{node['level']}] {node['title']}{suffix}")
    for c in node.get('children', []):
        print_tree(c, indent+1, max_depth)

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(__file__)
    MD_DIR = os.path.join(BASE_DIR, "data", "教原_markdown")
    TREE_DIR = os.path.join(BASE_DIR, "data", "tree_parts")
    MERGED_OUTPUT = os.path.join(BASE_DIR, "data", "knowledge_tree.json")

    md_files = sorted([
        os.path.join(MD_DIR, f) for f in os.listdir(MD_DIR) if f.endswith(".md")
    ]) if os.path.isdir(MD_DIR) else []

    print(f"Scanning: {MD_DIR}")
    print(f"Found {len(md_files)} MD files\n")

    # Parallel build
    print("Building trees in parallel...")
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(build_single_tree, p, TREE_DIR): p for p in md_files}
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            print(f"  [OK] {r['filename']} ({r['nodes']} nodes)")

    results.sort(key=lambda x: x['filename'])

    # Merge all trees into one
    print(f"\nMerging into {MERGED_OUTPUT}...")
    merged_children = []
    for r in results:
        with open(r['path'], encoding='utf-8') as f:
            tree = json.load(f)
        merged_children.extend(tree.get('children', []))

    merged = {"title": "333教育综合", "children": merged_children}
    os.makedirs(os.path.dirname(MERGED_OUTPUT), exist_ok=True)
    with open(MERGED_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    total = sum(r['nodes'] for r in results)
    print(f"\nDone: {len(results)} files, {total} total nodes")
    print(f"  Individual trees: {TREE_DIR}/")
    print(f"  Merged tree: {MERGED_OUTPUT}")
