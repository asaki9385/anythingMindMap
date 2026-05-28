# Knowledge Tree 阅读工具优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化文档解析结构识别、AI分析质量（总结/关键词），并新增AI自动标注重点文本功能。

**Architecture:** 在现有 pipeline 基础上增量改进：(1) tree_builder.py 新增 LLM 辅助层级验证；(2) node_enhancer.py 优化 prompt 并增加 highlights 输出；(3) common.js 新增高亮渲染函数；(4) 三个 UI 页面的信息面板增加重点标注区域和内容高亮。

**Tech Stack:** Python (FastAPI, httpx), Vanilla JS, ECharts, CSS custom properties

---

### Task 1: Add highlight styles to theme.css

**Files:**
- Modify: `knowledge-compiler/ui/theme.css:495-510` (before Responsive section)

- [ ] **Step 1: Add highlight-card, highlight-mark, and keyword-tooltip styles**

Open `knowledge-compiler/ui/theme.css` and insert the following block before the `/* ═══ Responsive ═══ */` section (before line 495):

```css
/* ════════════════════════════════════════════════════════════════
   Highlight Cards & Content Marks
   ════════════════════════════════════════════════════════════════ */

.highlight-list {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.highlight-card {
  padding: var(--sp-2) var(--sp-3);
  border-left: 3px solid var(--accent);
  background: var(--surface-raised);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.highlight-card:hover {
  background: var(--surface);
}

.highlight-card[data-importance="medium"] {
  border-left-color: var(--accent-secondary);
}

.highlight-card .hl-type {
  display: inline-block;
  padding: 1px 8px;
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  background: var(--tag-accent-bg);
  color: var(--accent);
  margin-bottom: var(--sp-1);
}

.highlight-card[data-importance="medium"] .hl-type {
  background: var(--tag-teal-bg);
  color: var(--accent-secondary);
}

.highlight-card .hl-text {
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
  color: var(--ink-secondary);
}

.highlight-mark,
.content-para .highlight-mark {
  background: rgba(232,168,76,0.15);
  padding: 1px 3px;
  border-radius: 2px;
  font-weight: var(--weight-medium);
}

[data-theme="light"] .highlight-mark,
[data-theme="light"] .content-para .highlight-mark {
  background: rgba(192,122,46,0.1);
}

.keyword-tag-wrapper {
  position: relative;
  display: inline-block;
}

.keyword-tag-wrapper .keyword-tooltip {
  display: none;
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  padding: var(--sp-2) var(--sp-3);
  background: var(--tooltip-bg);
  color: var(--tooltip-body);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
  white-space: nowrap;
  max-width: 260px;
  white-space: normal;
  box-shadow: var(--shadow-md);
  z-index: 10;
  pointer-events: none;
}

.keyword-tag-wrapper:hover .keyword-tooltip {
  display: block;
}
```

- [ ] **Step 2: Commit**

```bash
git add knowledge-compiler/ui/theme.css
git commit -m "feat: add highlight card, content mark, and keyword tooltip styles"
```

---

### Task 2: Add renderHighlights and update formatContent in common.js

**Files:**
- Modify: `knowledge-compiler/ui/common.js` — add `renderHighlights()` function, update `formatContent()` signature and logic, update `highlightKeywords()` to support new keyword format

- [ ] **Step 1: Add renderHighlights function**

In `knowledge-compiler/ui/common.js`, add this function after the `renderNodeTables` function (around line 211, before `var _mermaidCounter = 0;`):

```javascript
function renderHighlights(highlights) {
  if (!highlights || highlights.length === 0) return '';
  var typeLabels = {
    'definition': 'Definition',
    'theory': 'Theory',
    'argument': 'Argument',
    'example': 'Example',
    'formula': 'Formula',
    'method': 'Method'
  };
  var html = '<div class="field-label">Highlights</div>';
  html += '<div class="highlight-list">';
  highlights.forEach(function(hl, idx) {
    var importance = hl.importance || 'medium';
    var typeLabel = typeLabels[hl.type] || hl.type || '';
    html += '<div class="highlight-card" data-importance="' + escapeHtml(importance) + '" data-hl-idx="' + idx + '">';
    if (typeLabel) {
      html += '<span class="hl-type">' + escapeHtml(typeLabel) + '</span><br>';
    }
    html += '<span class="hl-text">' + escapeHtml(hl.text) + '</span>';
    html += '</div>';
  });
  html += '</div>';
  return html;
}
```

- [ ] **Step 2: Update highlightKeywords to support new keyword format**

Replace the existing `highlightKeywords` function (lines 78-89) with:

```javascript
function highlightKeywords(text, keywords) {
  if (!keywords || keywords.length === 0) return text;
  var result = text;
  keywords.forEach(function(kw) {
    var term = (typeof kw === 'object' && kw.term) ? kw.term : kw;
    if (term.length >= 2) {
      var escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      var regex = new RegExp('(' + escaped + ')', 'g');
      result = result.replace(regex, '<span class="sum-hl">$1</span>');
    }
  });
  return result;
}
```

- [ ] **Step 3: Update formatContent to accept and apply highlights**

Replace the `formatContent` function (lines 213-290) with:

```javascript
var _mermaidCounter = 0;
function formatContent(text, keywords, maxLen, highlights) {
  if (!text) return '';
  var t = text;
  if (maxLen > 0 && t.length > maxLen) {
    t = t.substring(0, maxLen) + '…';
  }
  var lines = t.split('\n');
  var html = '';
  var i = 0;
  while (i < lines.length) {
    var line = lines[i].trim();
    if (!line) { i++; continue; }
    if (line.startsWith('```mermaid')) {
      var mermaidLines = [];
      i++;
      while (i < lines.length && lines[i].trim() !== '```') {
        mermaidLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++;
      var mermaidCode = mermaidLines.join('\n').trim();
      if (mermaidCode) {
        _mermaidCounter++;
        var mermaidId = 'mermaid-inline-' + _mermaidCounter + '-' + Date.now();
        html += '<div class="mermaid-block" data-mermaid-id="' + mermaidId + '">'
             + '<pre class="mermaid-source" style="display:none;">' + escapeHtml(mermaidCode) + '</pre>'
             + '<div class="mermaid-render" id="' + mermaidId + '"></div></div>';
      }
      continue;
    }
    if (line.startsWith('|') && line.endsWith('|')) {
      var tableLines = [];
      while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
        tableLines.push(lines[i].trim());
        i++;
      }
      html += renderMarkdownTable(tableLines, keywords);
      continue;
    }
    if (line.toLowerCase().startsWith('<table')) {
      var tableHtml = line;
      if (!line.toLowerCase().endsWith('</table>')) {
        i++;
        while (i < lines.length) {
          tableHtml += '\n' + lines[i];
          if (lines[i].trim().toLowerCase().endsWith('</table>')) { i++; break; }
          i++;
        }
      } else { i++; }
      html += renderHtmlTable(tableHtml, keywords);
      continue;
    }
    if (line.startsWith('$$')) {
      var formulaLines = [line.substring(2)];
      if (line.endsWith('$$') && line.length > 4) {
        formulaLines = [line.substring(2, line.length - 2)];
      } else {
        i++;
        while (i < lines.length && !lines[i].trim().endsWith('$$')) {
          formulaLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) {
          formulaLines.push(lines[i].trim().replace(/\$\$/g, ''));
          i++;
        }
      }
      var formula = formulaLines.join('\n').trim();
      html += '<div class="block-formula">$$' + escapeHtml(formula) + '$$</div>';
      continue;
    }
    var processed = highlightKeywords(escapeHtml(line), keywords);
    processed = applyHighlights(processed, highlights);
    processed = renderInlineFormulas(processed);
    html += '<p class="content-para">' + processed + '</p>';
    i++;
  }
  return html;
}

function applyHighlights(text, highlights) {
  if (!highlights || highlights.length === 0) return text;
  var result = text;
  highlights.forEach(function(hl) {
    if (!hl.text || hl.text.length < 6) return;
    var escaped = hl.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var regex = new RegExp('(' + escaped + ')', 'g');
    result = result.replace(regex, '<span class="highlight-mark" data-hl-text="' + escapeHtml(hl.text) + '">$1</span>');
  });
  return result;
}
```

- [ ] **Step 4: Export new functions**

In the `global.KT` object (around line 482), add these entries:

After `renderNodeTables: renderNodeTables,` add:
```javascript
  renderHighlights: renderHighlights,
  applyHighlights: applyHighlights,
```

- [ ] **Step 5: Commit**

```bash
git add knowledge-compiler/ui/common.js
git commit -m "feat: add renderHighlights, applyHighlights, and update formatContent for highlight support"
```

---

### Task 3: Optimize node_enhancer.py prompts

**Files:**
- Modify: `knowledge-compiler/node_enhancer.py:209-252` (build_prompt function) and `knowledge-compiler/node_enhancer.py:326-333` (result parsing in enhance_one)

- [ ] **Step 1: Update the build_prompt function**

Replace the `build_prompt` function (lines 209-252) with:

```python
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
```

- [ ] **Step 2: Update enhance_one to parse new fields**

In the `enhance_one` function, replace the result parsing block (lines 328-333):

```python
            node['summary']     = result.get('summary', '')
            node['keywords']    = result.get('keywords', [])
            node['exam_points'] = result.get('exam_points', [])
            node['mermaid']     = result.get('mermaid', '') or ''
            node['tables']      = result.get('tables', []) or []
            node['highlights']  = result.get('highlights', []) or []
```

Also increase `max_tokens` from 1000 to 1500 in the payload (line 306) to accommodate the new fields:

```python
            "max_tokens": 1500,
```

- [ ] **Step 3: Commit**

```bash
git add knowledge-compiler/node_enhancer.py
git commit -m "feat: optimize AI prompts for better summaries, contextual keywords, and highlight extraction"
```

---

### Task 4: Add LLM-assisted structure validation to tree_builder.py

**Files:**
- Modify: `knowledge-compiler/tree_builder.py` — add `validate_and_repair_hierarchy()` function after `build_tree()`

- [ ] **Step 1: Add anomaly detection and LLM repair function**

Add the following after the `build_tree` function (after line 374):

```python
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
    try:
        resp = httpx.post(
            "https://api.deepseek.com/chat/completions",
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 500,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=20
        )
        if resp.status_code != 200:
            print(f"  Hierarchy validation HTTP {resp.status_code}, keeping original")
            return roots

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r'^```json\s*|\s*```$', '', raw)
        result = json.loads(raw)

        # Handle both direct array and {"corrections": [...]} format
        corrections = result if isinstance(result, list) else result.get("corrections", result.get("repairs", []))

        # Apply corrections
        node_map = {(a['title'], a['level']): a['node'] for a in unique}
        fixed = 0
        for c in corrections:
            idx = c.get('idx')
            new_level = c.get('correct_level')
            if idx is not None and new_level and 1 <= new_level <= 5 and idx < len(unique):
                a = unique[idx]
                if new_level != a['level']:
                    a['node']['level'] = new_level
                    fixed += 1

        if fixed > 0:
            print(f"  Hierarchy: fixed {fixed}/{len(unique)} anomalies")
            # Rebuild tree with corrected levels
            flat = []
            for root in roots:
                flat.extend(_flatten_for_rebuild(root))
            roots = build_tree(flat)

    except Exception as e:
        print(f"  Hierarchy validation failed: {e}, keeping original")

    return roots


def _flatten_for_rebuild(node, parent_level=0):
    """Flatten tree back to node list for rebuild_tree."""
    result = [{'title': node['title'], 'level': node['level'],
               'content': node.get('content', ''), 'children': [], 'captions': node.get('captions', [])}]
    for child in node.get('children', []):
        result.extend(_flatten_for_rebuild(child, node['level']))
    return result
```

- [ ] **Step 2: Commit**

```bash
git add knowledge-compiler/tree_builder.py
git commit -m "feat: add LLM-assisted hierarchy validation and repair"
```

---

### Task 5: Wire up hierarchy validation in server.py

**Files:**
- Modify: `knowledge-compiler/server.py:549-570` (`_build_trees_from_md` function)

- [ ] **Step 1: Add hierarchy validation call**

In `server.py`, find the `_build_trees_from_md` function. After the line that calls `build_tree(nodes)` (line 568) and before saving the JSON, add the validation call.

The current code around line 568 looks like:
```python
tree_children = build_tree(nodes)
tree = {"title": filename, "children": tree_children}
```

Change it to:
```python
tree_children = build_tree(nodes)
tree = {"title": filename, "children": tree_children}

# Validate hierarchy with LLM if API key is available
api_key = getattr(progress, 'deepseek_api_key', '') if progress else ''
if api_key:
    try:
        from tree_builder import validate_and_repair_hierarchy
        tree_children = validate_and_repair_hierarchy(tree_children, api_key)
        tree = {"title": filename, "children": tree_children}
    except Exception as e:
        print(f"  Hierarchy validation skipped: {e}")
```

Note: The `progress` object has `deepseek_api_key` set from the upload form. If `_build_trees_from_md` doesn't already receive `progress` or `api_key`, check the function signature and pass it through from the caller.

- [ ] **Step 2: Commit**

```bash
git add knowledge-compiler/server.py
git commit -m "feat: wire up hierarchy validation in build pipeline"
```

---

### Task 6: Update info panel in upload_mindmap.html

**Files:**
- Modify: `knowledge-compiler/ui/upload_mindmap.html` — update `showNodeInfo()` function to render highlights and new keyword format

- [ ] **Step 1: Update showNodeInfo to render highlights**

In the `showNodeInfo` function (line 1954), add the highlights section after the summary block and before keywords. Also update keywords to support the new object format.

Replace lines 1964-1974 (the summary and keywords blocks) with:

```javascript
  if (d.summary) {
    html += '<div class="field-label">Summary</div>';
    html += '<div class="summary">' + KT.formatSummary(d.summary, d.keywords, 0) + '</div>';
  }

  if (d.highlights && d.highlights.length > 0) {
    html += KT.renderHighlights(d.highlights);
  }

  if (d.keywords && d.keywords.length > 0) {
    html += '<div class="field-label">Keywords</div>';
    html += '<div class="keywords">';
    html += d.keywords.map(function(k) {
      if (typeof k === 'object' && k.term) {
        return '<span class="keyword-tag-wrapper"><span class="keyword-tag">' + KT.escapeHtml(k.term) + '</span>'
          + (k.context ? '<span class="keyword-tooltip">' + KT.escapeHtml(k.context) + '</span>' : '')
          + '</span>';
      }
      return '<span class="keyword-tag">' + KT.escapeHtml(k) + '</span>';
    }).join('');
    html += '</div>';
  }
```

- [ ] **Step 2: Update content display to pass highlights**

Find the content display line (around line 1995):
```javascript
html += '<div class="content-display" id="contentDisplay">' + KT.formatContent(d.content, d.keywords, 0) + '</div>';
```

Replace with:
```javascript
html += '<div class="content-display" id="contentDisplay">' + KT.formatContent(d.content, d.keywords, 0, d.highlights) + '</div>';
```

- [ ] **Step 3: Add highlight card click-to-scroll handler**

After the `showNodeInfo` function closes (after `KT.postRenderMathAndMermaid(content);`), add:

```javascript
  // Highlight card click to scroll
  var hlCards = content.querySelectorAll('.highlight-card');
  hlCards.forEach(function(card) {
    card.addEventListener('click', function() {
      var idx = card.getAttribute('data-hl-idx');
      var contentDisplay = document.getElementById('contentDisplay');
      if (!contentDisplay) return;
      var mark = contentDisplay.querySelector('.highlight-mark[data-hl-idx="' + idx + '"]');
      if (!mark) {
        // Try matching by text
        var hlText = card.querySelector('.hl-text');
        if (hlText) {
          var marks = contentDisplay.querySelectorAll('.highlight-mark');
          for (var m of marks) {
            if (m.getAttribute('data-hl-text') === hlText.textContent) {
              mark = m;
              break;
            }
          }
        }
      }
      if (mark) {
        mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
        mark.style.transition = 'box-shadow 0.3s';
        mark.style.boxShadow = '0 0 0 3px var(--accent)';
        setTimeout(function() { mark.style.boxShadow = ''; }, 1500);
      }
    });
  });
```

Also update `applyHighlights` in common.js to add `data-hl-idx` to each mark. In the `applyHighlights` function, change the replace line to include the index:

```javascript
function applyHighlights(text, highlights) {
  if (!highlights || highlights.length === 0) return text;
  var result = text;
  highlights.forEach(function(hl, idx) {
    if (!hl.text || hl.text.length < 6) return;
    var escaped = hl.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var regex = new RegExp('(' + escaped + ')', 'g');
    result = result.replace(regex, '<span class="highlight-mark" data-hl-idx="' + idx + '" data-hl-text="' + escapeHtml(hl.text) + '">$1</span>');
  });
  return result;
}
```

- [ ] **Step 4: Commit**

```bash
git add knowledge-compiler/ui/upload_mindmap.html knowledge-compiler/ui/common.js
git commit -m "feat: add highlights rendering and keyword tooltips to upload page info panel"
```

---

### Task 7: Update info panel in tree_mindmap.html

**Files:**
- Modify: `knowledge-compiler/ui/tree_mindmap.html` — update `showNodeInfo()` function (line 1335)

- [ ] **Step 1: Apply the same changes as Task 6**

The `showNodeInfo` function in `tree_mindmap.html` uses template literals (backticks) instead of string concatenation. Apply the equivalent changes:

After the summary block (line 1347), add:
```javascript
    if (d.highlights && d.highlights.length > 0) {
      html += KT.renderHighlights(d.highlights);
    }
```

Replace the keywords block (line 1353) with:
```javascript
    if (d.keywords && d.keywords.length > 0) {
      html += '<div class="field-label">Keywords</div>';
      html += '<div class="keywords">';
      html += d.keywords.map(function(k) {
        if (typeof k === 'object' && k.term) {
          return '<span class="keyword-tag-wrapper"><span class="keyword-tag">' + KT.escapeHtml(k.term) + '</span>'
            + (k.context ? '<span class="keyword-tooltip">' + KT.escapeHtml(k.context) + '</span>' : '')
            + '</span>';
        }
        return '<span class="keyword-tag">' + KT.escapeHtml(k) + '</span>';
      }).join('');
      html += '</div>';
    }
```

Update the content display line to pass highlights:
```javascript
html += '<div class="content-display">' + KT.formatContent(d.content, d.keywords, 0, d.highlights) + '</div>';
```

- [ ] **Step 2: Commit**

```bash
git add knowledge-compiler/ui/tree_mindmap.html
git commit -m "feat: add highlights rendering and keyword tooltips to mindmap page info panel"
```

---

### Task 8: Update detail panel in tree_display.html

**Files:**
- Modify: `knowledge-compiler/ui/tree_display.html` — update `showDetail()` function (line 380)

- [ ] **Step 1: Read the current showDetail function**

Read `tree_display.html` around line 380 to understand the current rendering structure.

- [ ] **Step 2: Add highlights section and update keywords**

In the `showDetail` function, after the summary rendering, add:
```javascript
  if (node.highlights && node.highlights.length > 0) {
    html += KT.renderHighlights(node.highlights);
  }
```

Update the keywords rendering to support both formats (same pattern as Task 6).

Update the content rendering to pass highlights:
```javascript
html += KT.formatContent(node.content, node.keywords, 0, node.highlights);
```

- [ ] **Step 3: Commit**

```bash
git add knowledge-compiler/ui/tree_display.html
git commit -m "feat: add highlights rendering and keyword tooltips to tree display page"
```

---

### Task 9: End-to-end verification

- [ ] **Step 1: Verify CSS loads correctly**

Start the server with `start.bat`, open browser, check:
- No 404 errors for theme.css or common.js
- Dark/light mode toggle works
- Highlight card styles render correctly

- [ ] **Step 2: Verify prompt changes produce correct output**

Upload a test document, check the enhanced JSON output:
- `keywords` is an array of objects with `term` and `context`
- `highlights` is an array with `text`, `importance`, `type` fields
- `summary` is more detailed than before

- [ ] **Step 3: Verify UI rendering**

Click on a node in the mind map:
- Highlights section appears between Summary and Keywords
- Highlight cards show type labels and importance-based colors
- Keywords with new format show tooltip on hover
- Content text has highlight marks with warm background
- Clicking a highlight card scrolls to the corresponding mark

- [ ] **Step 4: Verify backward compatibility**

Open an existing project (old JSON without highlights):
- No errors or missing sections
- Keywords (old string format) still render correctly
- Highlights section simply doesn't appear
