/* ════════════════════════════════════════════════════════════════
   Knowledge Tree — Common Utilities
   Shared across upload_mindmap.html, tree_mindmap.html, tree_display.html
   ════════════════════════════════════════════════════════════════ */
(function(global) {
'use strict';

// ── Constants ──

var LEVEL_COLORS_LIGHT = ['#c07a2e', '#2a7d6e', '#5a7db8', '#8b6bb0', '#c44040'];
var LEVEL_COLORS_DARK  = ['#e8a84c', '#3aa88f', '#7a9dd0', '#a888cc', '#e85454'];

// ── Theme ──

function getTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function toggleTheme() {
  var html = document.documentElement;
  var isDark = html.getAttribute('data-theme') === 'dark';
  if (isDark) {
    html.removeAttribute('data-theme');
    localStorage.setItem('kt-theme', 'light');
  } else {
    html.setAttribute('data-theme', 'dark');
    localStorage.setItem('kt-theme', 'dark');
  }
  updateThemeUI();
  return getTheme();
}

function initTheme() {
  var saved = localStorage.getItem('kt-theme');
  if (saved === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
  updateThemeUI();
}

function updateThemeUI() {
  var isDark = getTheme() === 'dark';
  var icon = document.getElementById('themeIcon');
  var label = document.getElementById('themeLabel');
  if (icon) icon.textContent = isDark ? '🌙' : '☀️';
  if (label) label.textContent = isDark ? 'Dark' : 'Light';
}

// ── Color Helpers ──

function getNodeColor(level) {
  var colors = getTheme() === 'light' ? LEVEL_COLORS_LIGHT : LEVEL_COLORS_DARK;
  return colors[Math.min(level, colors.length - 1)];
}

function getTooltipColors() {
  var isLight = getTheme() === 'light';
  return isLight
    ? { bg: 'rgba(255,255,255,0.98)', text: '#2c1810', meta: '#9a8b7e', title: '#c07a2e', body: '#6b5b4e', divider: 'rgba(192,122,46,0.15)', keywordBg: 'rgba(192,122,46,0.1)', keywordColor: '#c07a2e', label: '#2c1810', leafLabel: '#9a8b7e' }
    : { bg: 'rgba(36,32,24,0.98)', text: '#e8e0d8', meta: '#706050', title: '#e8a84c', body: '#a89888', divider: 'rgba(232,168,76,0.15)', keywordBg: 'rgba(232,168,76,0.12)', keywordColor: '#e8a84c', label: '#e8e0d8', leafLabel: '#a89888' };
}

function getChartLabelColor() {
  return getTheme() === 'light' ? '#2c1810' : '#e8e0d8';
}

function getChartLeafLabelColor() {
  return getTheme() === 'light' ? '#9a8b7e' : '#a89888';
}

// ── HTML Helpers ──

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function htmlToMarkdown(html) {
  if (!html) return '';
  var md = html;
  md = md.replace(/<br\s*\/?>/gi, '\n');
  md = md.replace(/<b>([\s\S]*?)<\/b>/gi, '**$1**');
  md = md.replace(/<strong>([\s\S]*?)<\/strong>/gi, '**$1**');
  md = md.replace(/<i>([\s\S]*?)<\/i>/gi, '*$1*');
  md = md.replace(/<em>([\s\S]*?)<\/em>/gi, '*$1*');
  md = md.replace(/<s>([\s\S]*?)<\/s>/gi, '~~$1~~');
  md = md.replace(/<strike>([\s\S]*?)<\/strike>/gi, '~~$1~~');
  md = md.replace(/<del>([\s\S]*?)<\/del>/gi, '~~$1~~');
  md = md.replace(/<div>/gi, '\n');
  md = md.replace(/<\/div>/gi, '');
  md = md.replace(/<[^>]+>/g, '');
  md = md.replace(/&amp;/g, '&');
  md = md.replace(/&lt;/g, '<');
  md = md.replace(/&gt;/g, '>');
  md = md.replace(/&quot;/g, '"');
  md = md.replace(/&#39;/g, "'");
  md = md.replace(/\n{3,}/g, '\n\n');
  return md;
}

function markdownToHtml(md) {
  if (!md) return '';
  var html = md;
  html = html.replace(/\*\*([\s\S]+?)\*\*/g, '<b>$1</b>');
  html = html.replace(/\*([\s\S]+?)\*/g, '<i>$1</i>');
  html = html.replace(/~~([\s\S]+?)~~/g, '<s>$1</s>');
  return html;
}

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

// ── Content Formatting ──

function formatSummary(summary, keywords, maxLen) {
  if (!summary) return '';
  var text = summary;
  if (maxLen > 0 && text.length > maxLen) {
    text = text.substring(0, maxLen) + '…';
  }
  var sentences = text.split(/(?<=[。；])/);
  var groupSize = 3;
  var html = '';
  for (var i = 0; i < sentences.length; i += groupSize) {
    var chunk = sentences.slice(i, i + groupSize).join('');
    if (keywords && keywords.length > 0) {
      keywords.forEach(function(kw) {
        if (kw.length >= 2) {
          var escaped = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          var regex = new RegExp('(' + escaped + ')', 'g');
          chunk = chunk.replace(regex, '<span class="sum-hl">$1</span>');
        }
      });
    }
    html += '<p class="sum-para">' + chunk + '</p>';
  }
  return html;
}

function renderInlineFormulas(text) {
  text = text.replace(/\$\$(.+?)\$\$/g, '<span class="inline-formula" data-formula="$$$1$$">$$$$1$$</span>');
  text = text.replace(/(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)/g,
    '<span class="inline-formula" data-formula="$$$1$">$$$1$</span>');
  return text;
}

function renderMarkdownTable(tableLines, keywords) {
  if (tableLines.length < 2) return '';
  var headerCells = tableLines[0].split('|').filter(function(c) { return c.trim() !== ''; });
  var dataStart = 1;
  if (tableLines.length > 1 && /^[\s|:-]+$/.test(tableLines[1])) {
    dataStart = 2;
  }
  var html = '<div class="table-wrapper"><table class="md-table"><thead><tr>';
  headerCells.forEach(function(cell) {
    html += '<th>' + highlightKeywords(escapeHtml(cell.trim()), keywords) + '</th>';
  });
  html += '</tr></thead><tbody>';
  for (var r = dataStart; r < tableLines.length; r++) {
    var cells = tableLines[r].split('|').filter(function(c) { return c.trim() !== ''; });
    html += '<tr>';
    cells.forEach(function(cell) {
      html += '<td>' + highlightKeywords(escapeHtml(cell.trim()), keywords) + '</td>';
    });
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  return html;
}

function renderHtmlTable(htmlString, keywords) {
  try {
    var parser = new DOMParser();
    var doc = parser.parseFromString(htmlString, 'text/html');
    var table = doc.querySelector('table');
    if (!table) return escapeHtml(htmlString);
    var rows = table.querySelectorAll('tr');
    if (rows.length === 0) return '';
    var html = '<div class="table-wrapper"><table class="md-table">';
    var firstRowCells = rows[0].querySelectorAll('td, th');
    var hasHead = table.querySelector('thead') || (firstRowCells.length > 0 && firstRowCells[0].tagName === 'TH');
    var startRow = 0;
    if (hasHead) {
      html += '<thead><tr>';
      firstRowCells.forEach(function(cell) {
        var colspan = cell.getAttribute('colspan') || 1;
        var rowspan = cell.getAttribute('rowspan') || 1;
        html += '<th' + (colspan > 1 ? ' colspan="' + colspan + '"' : '')
             + (rowspan > 1 ? ' rowspan="' + rowspan + '"' : '')
             + '>' + highlightKeywords(escapeHtml(cell.textContent.trim()), keywords) + '</th>';
      });
      html += '</tr></thead><tbody>';
      startRow = 1;
    } else {
      html += '<tbody>';
    }
    for (var r = startRow; r < rows.length; r++) {
      html += '<tr>';
      var cells = rows[r].querySelectorAll('td, th');
      cells.forEach(function(cell) {
        var colspan = cell.getAttribute('colspan') || 1;
        var rowspan = cell.getAttribute('rowspan') || 1;
        html += '<td' + (colspan > 1 ? ' colspan="' + colspan + '"' : '')
             + (rowspan > 1 ? ' rowspan="' + rowspan + '"' : '')
             + '>' + highlightKeywords(escapeHtml(cell.textContent.trim()), keywords) + '</td>';
      });
      html += '</tr>';
    }
    html += '</tbody></table></div>';
    return html;
  } catch(e) {
    return escapeHtml(htmlString);
  }
}

function renderNodeMermaid(mermaidCode, containerId) {
  if (!mermaidCode) return '';
  return '<div class="field-label">Flowchart</div>'
    + '<div class="mermaid-block" data-mermaid-id="' + containerId + '">'
    + '<pre class="mermaid-source" style="display:none;">' + escapeHtml(mermaidCode) + '</pre>'
    + '<div class="mermaid-render" id="' + containerId + '"></div></div>';
}

function renderNodeTables(tables) {
  if (!tables || tables.length === 0) return '';
  var html = '<div class="field-label">Tables</div>';
  tables.forEach(function(tableStr) {
    var tableLines = tableStr.split('\n').filter(function(l) { return l.trim(); });
    html += renderMarkdownTable(tableLines, []);
  });
  return html;
}

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
    processed = markdownToHtml(processed);
    processed = applyHighlights(processed, highlights);
    processed = renderInlineFormulas(processed);
    html += '<p class="content-para">' + processed + '</p>';
    i++;
  }
  return html;
}

// Note: `text` must be HTML-escaped before calling (formatContent does this)
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

async function postRenderMathAndMermaid(container) {
  if (typeof renderMathInElement === 'function') {
    renderMathInElement(container, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
        { left: '\\[', right: '\\]', display: true }
      ],
      throwOnError: false
    });
  }
  var mermaidBlocks = container.querySelectorAll('.mermaid-block');
  for (var block of mermaidBlocks) {
    var sourceEl = block.querySelector('.mermaid-source');
    var renderEl = block.querySelector('.mermaid-render');
    if (sourceEl && renderEl && !renderEl.getAttribute('data-processed')) {
      var code = sourceEl.textContent.trim();
      if (code) {
        try {
          var id = renderEl.id || ('mermaid-' + Math.random().toString(36).substr(2, 9));
          var result = await mermaid.render(id + '-svg', code);
          renderEl.innerHTML = result.svg;
          renderEl.setAttribute('data-processed', 'true');
        } catch (e) {
          renderEl.innerHTML = '<pre style="color:var(--danger);font-size:11px;">Mermaid error: '
            + escapeHtml(e.message || String(e)) + '</pre>';
          renderEl.setAttribute('data-processed', 'true');
        }
      }
    }
  }
}

// ── Tree Data Transform ──

function transformNode(node, depth) {
  depth = depth || 0;
  var name = node.title || node.name || 'Untitled';
  var transformed = {
    name: name,
    title: node.title || node.name,
    level: node.level != null ? node.level : depth,
    depth: depth,
    summary: node.summary || '',
    keywords: node.keywords || [],
    exam_points: node.exam_points || [],
    content: node.content || '',
    mermaid: node.mermaid || '',
    tables: node.tables || [],
    captions: node.captions || [],
    children: []
  };
  if (node.children && Array.isArray(node.children) && node.children.length > 0) {
    transformed.children = node.children.map(function(child) {
      return transformNode(child, depth + 1);
    });
  }
  return transformed;
}

function transformNodeWithPath(node, targetPath, depth) {
  depth = depth || 0;
  var name = node.title || node.name || 'Untitled';
  var isTarget = (depth === targetPath.length - 1 && name === targetPath[depth]);
  var isAncestor = (depth < targetPath.length - 1 && name === targetPath[depth]);
  var hasChildren = node.children && Array.isArray(node.children) && node.children.length > 0;

  var transformed = {
    name: name,
    title: node.title || node.name,
    level: node.level != null ? node.level : depth,
    depth: depth,
    summary: node.summary || '',
    keywords: node.keywords || [],
    exam_points: node.exam_points || [],
    content: node.content || '',
    mermaid: node.mermaid || '',
    tables: node.tables || [],
    captions: node.captions || [],
    children: []
  };

  if (hasChildren) {
    transformed.collapsed = !isAncestor;
    transformed.children = node.children.map(function(child) {
      return transformNodeWithPath(child, targetPath, depth + 1);
    });
  }

  if (isTarget) {
    transformed.itemStyle = {
      color: '#e8a84c',
      borderColor: '#e8a84c',
      borderWidth: 3,
      shadowBlur: 16,
      shadowColor: 'rgba(232,168,76,0.5)'
    };
    transformed.label = { color: '#e8a84c', fontWeight: 'bold', fontSize: 13 };
    transformed.symbolSize = 18;
  }

  return transformed;
}

function flattenNodes(node, path, depth) {
  depth = depth || 0;
  path = path || [];
  var nodeName = node.title || node.name || 'Untitled';
  var currentPath = path.concat([nodeName]);
  if (!global._allNodes) global._allNodes = [];
  global._allNodes.push({ node: node, path: currentPath, depth: depth });
  if (node.children) {
    for (var i = 0; i < node.children.length; i++) {
      flattenNodes(node.children[i], currentPath, depth + 1);
    }
  }
}

// ── ECharts Helpers ──

function getLabelPosition(layout) {
  if (layout === 'LR') return 'right';
  if (layout === 'RL') return 'left';
  if (layout === 'TB') return 'bottom';
  return 'top';
}

function getLeafLabelPosition(layout) {
  return getLabelPosition(layout);
}

// ── Search ──

function searchNodes(allNodes, query) {
  if (!query || !allNodes) return [];
  var queryLower = query.toLowerCase();
  var matches = [];
  for (var i = 0; i < allNodes.length; i++) {
    var nodeData = allNodes[i];
    var title = (nodeData.node.title || nodeData.node.name || '').toLowerCase();
    if (title.indexOf(queryLower) !== -1) {
      matches.push({ index: i, node: nodeData.node, path: nodeData.path });
    }
  }
  return matches;
}

function highlightSearchMatch(title, query) {
  if (!query) return escapeHtml(title);
  var titleLower = title.toLowerCase();
  var queryLower = query.toLowerCase();
  var queryLen = query.length;
  var html = '';
  var lastIdx = 0;
  var idx = titleLower.indexOf(queryLower);
  while (idx !== -1) {
    html += escapeHtml(title.substring(lastIdx, idx));
    html += '<span class="search-highlight">' + escapeHtml(title.substring(idx, idx + queryLen)) + '</span>';
    lastIdx = idx + queryLen;
    idx = titleLower.indexOf(queryLower, lastIdx);
  }
  html += escapeHtml(title.substring(lastIdx));
  return html;
}

// ── Toast ──

function showToast(msg) {
  var toast = document.getElementById('toast');
  if (!toast) return;
  toast.innerHTML = escapeHtml(msg).replace(/\n/g, '<br>');
  toast.classList.add('show');
  setTimeout(function() { toast.classList.remove('show'); }, 5000);
}

// ── Helpers ──

function sleep(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

// ── Exports ──

global.KT = {
  // Theme
  getTheme: getTheme,
  toggleTheme: toggleTheme,
  initTheme: initTheme,
  updateThemeUI: updateThemeUI,
  // Colors
  getNodeColor: getNodeColor,
  getTooltipColors: getTooltipColors,
  getChartLabelColor: getChartLabelColor,
  getChartLeafLabelColor: getChartLeafLabelColor,
  // HTML
  escapeHtml: escapeHtml,
  htmlToMarkdown: htmlToMarkdown,
  markdownToHtml: markdownToHtml,
  highlightKeywords: highlightKeywords,
  // Content
  formatSummary: formatSummary,
  formatContent: formatContent,
  renderInlineFormulas: renderInlineFormulas,
  renderMarkdownTable: renderMarkdownTable,
  renderHtmlTable: renderHtmlTable,
  renderNodeMermaid: renderNodeMermaid,
  renderNodeTables: renderNodeTables,
  renderHighlights: renderHighlights,
  applyHighlights: applyHighlights,
  postRenderMathAndMermaid: postRenderMathAndMermaid,
  // Tree
  transformNode: transformNode,
  transformNodeWithPath: transformNodeWithPath,
  flattenNodes: flattenNodes,
  // ECharts
  getLabelPosition: getLabelPosition,
  getLeafLabelPosition: getLeafLabelPosition,
  // Search
  searchNodes: searchNodes,
  highlightSearchMatch: highlightSearchMatch,
  // Toast
  showToast: showToast,
  // Helpers
  sleep: sleep,
  formatSize: formatSize
};

})(window);
