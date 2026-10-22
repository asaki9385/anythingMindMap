/* ════════════════════════════════════════════════════════════════
   AnythingMindmap — Common Utilities
   Shared across client.html, overview.html, tree_mindmap.html, settings.html
   ════════════════════════════════════════════════════════════════ */
(function(global) {
'use strict';

// ── Constants ──

var LEVEL_COLORS_LIGHT = ['#d97757', '#6da38a', '#7d91b7', '#9a86b9', '#c77b5d'];
var LEVEL_COLORS_DARK  = ['#e08264', '#65b88f', '#8da5cb', '#b39bd1', '#e08a78'];

// ── Theme ──

function getTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function notifyThemeChange() {
  try {
    window.dispatchEvent(new CustomEvent('kt-theme-change', { detail: { theme: getTheme() } }));
  } catch (err) {}
}

function toggleTheme() {
  var html = document.documentElement;
  var isDark = html.getAttribute('data-theme') === 'dark';
  // 主题切换过渡：短暂挂过渡类，让整页配色平滑渐变（减弱动效偏好时跳过）
  if (!prefersReducedMotion()) {
    html.classList.add('kt-theme-transition');
    clearTimeout(toggleTheme._t);
    toggleTheme._t = setTimeout(function () {
      html.classList.remove('kt-theme-transition');
    }, 360);
  }
  if (isDark) {
    html.removeAttribute('data-theme');
    html.removeAttribute('data-mode');
    localStorage.setItem('kt-theme', 'light');
  } else {
    html.setAttribute('data-theme', 'dark');
    html.setAttribute('data-mode', 'dark');
    localStorage.setItem('kt-theme', 'dark');
  }
  updateThemeUI();
  notifyThemeChange();
  return getTheme();
}

function prefersReducedMotion() {
  try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch (err) { return false; }
}

// 动画统一入口判断：GSAP 已加载且用户未要求减弱动效时，才播 GSAP 动效
function gsapReady() {
  return typeof window.gsap !== 'undefined' && !prefersReducedMotion();
}

function initTheme() {
  var saved = localStorage.getItem('kt-theme');
  if (saved === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
    document.documentElement.setAttribute('data-mode', 'dark');
  }
  updateThemeUI();
}

function updateThemeUI() {
  var isDark = getTheme() === 'dark';
  var icon = document.getElementById('themeIcon');
  var label = document.getElementById('themeLabel');
  if (icon) icon.innerHTML = iconHtml(isDark ? 'moon' : 'sun');
  if (label) label.textContent = isDark ? '深色' : '浅色';
}

// 父级客户端切换主题时，embedded 页面同步主题（不刷新、不丢当前导图状态）。
if (typeof window !== 'undefined') {
  window.addEventListener('message', function(event) {
    if (event.origin !== window.location.origin || !event.data) return;
    if (event.data.type !== 'kt-theme') return;
    var next = event.data.theme === 'dark' ? 'dark' : 'light';
    var changed = getTheme() !== next;
    if (next === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
      document.documentElement.setAttribute('data-mode', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
      document.documentElement.removeAttribute('data-mode');
    }
    try { localStorage.setItem('kt-theme', next); } catch (err) {}
    updateThemeUI();
    if (changed) notifyThemeChange();
  });
}

// ── 图标（统一线性图标，界面里不用 emoji）──
// 24×24 网格、1.75 描边、圆头圆角、跟随文字颜色（currentColor）。
// 静态标记写 <span data-icon="menu"></span>，JS 生成内容用 iconHtml(name)。
var ICONS = {
  menu:      '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h16"/>',
  list:      '<path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><path d="M4.2 6h.01"/><path d="M4.2 12h.01"/><path d="M4.2 18h.01"/>',
  eye:       '<path d="M2.5 12S6.1 5.8 12 5.8 21.5 12 21.5 12 17.9 18.2 12 18.2 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff:    '<path d="M3 3l18 18"/><path d="M10.6 6.1A10 10 0 0 1 12 6c5.9 0 9.5 6 9.5 6a16.4 16.4 0 0 1-3.3 3.8"/><path d="M6.2 6.2C3.8 8 2.5 12 2.5 12S6.1 18 12 18c1.5 0 2.9-.4 4.1-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  export:    '<path d="M12 4v11"/><path d="M8 8l4-4 4 4"/><path d="M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3"/>',
  download:  '<path d="M12 4v11"/><path d="M8 11l4 4 4-4"/><path d="M5 20h14"/>',
  upload:    '<path d="M12 17V6"/><path d="M8 10l4-4 4 4"/><path d="M5 20h14"/>',
  trash:     '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 13h9l1-13"/><path d="M10 11v5"/><path d="M14 11v5"/>',
  message:   '<path d="M21 15a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/>',
  search:    '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-3.6-3.6"/>',
  close:     '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
  check:     '<path d="M4.5 12.5l5 5L19.5 6.5"/>',
  target:    '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.8"/><circle cx="12" cy="12" r="1.4"/>',
  alert:     '<path d="M12 4.6l8.4 14.8H3.6z"/><path d="M12 10.4v4"/><path d="M12 17.1h.01"/>',
  moon:      '<path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 7.6 7.6 0 1 0 20 14.2z"/>',
  sun:       '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2"/><path d="M12 19.3v2.2"/><path d="M2.5 12h2.2"/><path d="M19.3 12h2.2"/><path d="M5.3 5.3l1.6 1.6"/><path d="M17.1 17.1l1.6 1.6"/><path d="M18.7 5.3l-1.6 1.6"/><path d="M6.9 17.1l-1.6 1.6"/>',
  file:      '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/>',
  scan:      '<path d="M4 8.5V6a2 2 0 0 1 2-2h2.5"/><path d="M15.5 4H18a2 2 0 0 1 2 2v2.5"/><path d="M20 15.5V18a2 2 0 0 1-2 2h-2.5"/><path d="M8.5 20H6a2 2 0 0 1-2-2v-2.5"/><path d="M4 12h16"/>',
  folder:    '<path d="M4 7.5a2 2 0 0 1 2-2h3l2 2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
  tree:      '<circle cx="12" cy="5.2" r="2.4"/><circle cx="6" cy="18.5" r="2.4"/><circle cx="18" cy="18.5" r="2.4"/><path d="M12 7.6v4.2"/><path d="M12 11.8H6.2v4.3"/><path d="M12 11.8h5.8v4.3"/>',
  cpu:       '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2.5v2.5"/><path d="M15 2.5v2.5"/><path d="M9 19v2.5"/><path d="M15 19v2.5"/><path d="M2.5 9h2.5"/><path d="M2.5 15h2.5"/><path d="M19 9h2.5"/><path d="M19 15h2.5"/>',
  settings:  '<path d="M4 7h5"/><path d="M13 7h7"/><circle cx="11" cy="7" r="2"/><path d="M4 12h9"/><path d="M17 12h3"/><circle cx="15" cy="12" r="2"/><path d="M4 17h3"/><path d="M11 17h9"/><circle cx="9" cy="17" r="2"/>',
  pencil:    '<path d="M4 20l4.2-1 10.5-10.5a2.1 2.1 0 0 0-3-3L5.2 16z"/><path d="M14.5 6.5l3 3"/>',
  spacing:   '<path d="M3.5 12h17"/><path d="M6.8 8.7L3.5 12l3.3 3.3"/><path d="M17.2 8.7L20.5 12l-3.3 3.3"/><path d="M12 3.5v17"/><path d="M8.7 6.8L12 3.5l3.3 3.3"/><path d="M8.7 17.2L12 20.5l3.3-3.3"/>',
  external:  '<path d="M14 4.5h5.5V10"/><path d="M19.5 4.5l-8 8"/><path d="M18.5 14v4.5a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V7.5A1.5 1.5 0 0 1 6 6h4.5"/>',
  chevronDown:  '<path d="M6 9.5l6 6 6-6"/>',
  chevronLeft:  '<path d="M14.5 6l-6 6 6 6"/>',
  chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
  clock:       '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 2"/>',
  calendar:    '<rect x="4" y="5.5" width="16" height="14" rx="2"/><path d="M8 3.5v4"/><path d="M16 3.5v4"/><path d="M4 10h16"/>'
  ,user:       '<circle cx="12" cy="8" r="3.5"/><path d="M4.8 20c0-4.2 3.2-7.2 7.2-7.2s7.2 3 7.2 7.2"/>'
  ,plus:       '<path d="M12 5v14"/><path d="M5 12h14"/>'
  ,star:       '<path d="M12 3.8l2.5 5.1 5.6.8-4.1 4 1 5.6-5-2.7-5 2.7 1-5.6-4.1-4 5.6-.8z"/>'
  ,quote:      '<path d="M9.4 7.5H5.8A2.3 2.3 0 0 0 3.5 9.8v3.4a2.3 2.3 0 0 0 2.3 2.3h2.2c0 2-1 3.2-3.1 4"/><path d="M19.9 7.5h-3.6a2.3 2.3 0 0 0-2.3 2.3v3.4a2.3 2.3 0 0 0 2.3 2.3h2.2c0 2-1 3.2-3.1 4"/>'
  ,spark:      '<path d="M10.6 3.2c.5 4.7 3.7 7.9 8.4 8.4-4.7.5-7.9 3.7-8.4 8.4-.5-4.7-3.7-7.9-8.4-8.4 4.7-.5 7.9-3.7 8.4-8.4Z"/><path d="M18.6 3.5c.15 1.35 1.05 2.25 2.4 2.4-1.35.15-2.25 1.05-2.4 2.4-.15-1.35-1.05-2.25-2.4-2.4 1.35-.15 2.25-1.05 2.4-2.4Z"/>'
  ,more:       '<circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/>'
  ,panel:      '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M15 4.5v15"/><path d="M17.5 9h.01"/><path d="M17.5 12h.01"/>'
  ,arrowRight: '<path d="M4 12h15"/><path d="M14 7l5 5-5 5"/>'
};

function iconHtml(name, cls) {
  var body = ICONS[name] || '';
  return '<svg class="ic' + (cls ? ' ' + cls : '') + '" viewBox="0 0 24 24" width="1em" height="1em"'
    + ' fill="none" stroke="currentColor" stroke-width="1.75"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
    + body + '</svg>';
}

// 把 <span data-icon="xxx"> 占位符填成图标；重复调用安全。
function applyIconSlots(root) {
  var scope = root && root.querySelectorAll ? root : document;
  var slots = scope.querySelectorAll('[data-icon]');
  for (var i = 0; i < slots.length; i++) {
    var el = slots[i];
    if (el.getAttribute('data-icon-done')) continue;
    el.innerHTML = iconHtml(el.getAttribute('data-icon'), el.getAttribute('data-icon-cls') || '');
    el.setAttribute('data-icon-done', '1');
  }
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { applyIconSlots(document); });
  } else {
    applyIconSlots(document);
  }
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

function stripLocalImageRefs(text) {
  if (!text) return '';
  var lines = String(text).split('\n');
  var out = [];
  var inCode = false;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var s = line.trim();
    if (s.indexOf('```') === 0) {
      inCode = !inCode;
      out.push(line);
      continue;
    }
    if (inCode) {
      out.push(line);
      continue;
    }
    // MinerU 本地图片：当前没有资产服务/渲染器，不能把 hash 路径当正文展示。
    line = line.replace(/<img\b[^>]*?\bsrc\s*=\s*(["'])(?:\.?\/)?images\/.*?\1[^>]*>/gi, '');
    line = line.replace(/!\[[^\]]*\]\(\s*(?:\.?\/)?images\/[^)\s]+(?:\s+["'][^"']*["'])?\s*\)/gi, '');
    if (s && /^(?:\.?\/)?images\/[A-Za-z0-9._%+-]+(?:\.(?:jpe?g|png|gif|webp|bmp|svg))?(?:[?#].*)?$/i.test(s)) {
      continue;
    }
    line = line.replace(/[ \t]{2,}/g, ' ').replace(/\s+$/, '');
    if (line.trim()) out.push(line);
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
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
  var text = stripLocalImageRefs(summary);
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

// ── 长标题换行（导图节点标签用） ──
// 导图布局不会把文字宽度算进节点间距，超长标题会压到相邻节点上。
// 这里按字数折行（标题以中文为主），并避免一行的开头是收尾标点。
// 纯函数，导图页与测试都走它。
var _NO_LINE_START = '、。，．,.;：:？?！!）)】》」』〉>…·—’”\'';
function wrapLabel(name, perLine, maxLines) {
  perLine = perLine || 14;
  maxLines = maxLines || 3;
  var s = String(name == null ? '' : name).trim();
  if (!s) return '';
  var chars = Array.from(s);
  if (chars.length <= perLine) return s;
  var lines = [];
  var buf = '';
  for (var i = 0; i < chars.length; i++) {
    buf += chars[i];
    if (buf.length >= perLine) {
      // 收尾标点接到本行末尾，不留在下一行开头
      while (i + 1 < chars.length && _NO_LINE_START.indexOf(chars[i + 1]) >= 0
             && buf.length < perLine + 2) {
        buf += chars[++i];
      }
      lines.push(buf);
      buf = '';
    }
  }
  if (buf) lines.push(buf);
  if (lines.length > maxLines) {
    lines = lines.slice(0, maxLines);
    lines[maxLines - 1] = Array.from(lines[maxLines - 1]).slice(0, perLine).join('') + '…';
  }
  return lines.join('\n');
}

// ── 流程图：可缩放画布 + 全屏查看 ──
// 流程图不再按面板宽度整体压扁（图越大字越小），而是按图形原尺寸显示，
// 可拖拽平移、滚轮/按钮缩放，并支持放大到全屏看全图。
function _mmSvgSize(svg) {
  var w = 0, h = 0;
  try {
    var vb = svg.viewBox && svg.viewBox.baseVal;
    if (vb && vb.width > 0) { w = vb.width; h = vb.height; }
  } catch (e) {}
  if (!w) {
    w = parseFloat(svg.getAttribute('width')) || 0;
    h = parseFloat(svg.getAttribute('height')) || 0;
  }
  if (!w || !h) {
    try {
      var bb = svg.getBBox && svg.getBBox();
      if (bb && bb.width > 0) { w = bb.width; h = bb.height; }
    } catch (e) {}
  }
  return { w: Math.max(1, w || 720), h: Math.max(1, h || 360) };
}

// 平移/缩放引擎：canvas 用 transform，视口负责裁切。
// 比例与位移都做边界约束，图不会被拖出视口找不回来。
function _mmEngine(viewport, canvas, contentW, contentH, onScale) {
  var MIN = 0.2, MAX = 6;
  var scale = 1, tx = 0, ty = 0;
  function _vw() { return viewport.clientWidth || 320; }
  function _vh() { return viewport.clientHeight || 260; }
  function clampAll() {
    var sw = contentW * scale, sh = contentH * scale;
    if (sw <= _vw()) tx = (_vw() - sw) / 2;
    else tx = Math.min(0, Math.max(_vw() - sw, tx));
    if (sh <= _vh()) ty = (_vh() - sh) / 2;
    else ty = Math.min(0, Math.max(_vh() - sh, ty));
  }
  function apply() {
    clampAll();
    canvas.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    if (onScale) onScale(scale);
  }
  function setScale(next, ax, ay) {
    ax = ax == null ? _vw() / 2 : ax;
    ay = ay == null ? _vh() / 2 : ay;
    next = Math.max(MIN, Math.min(MAX, next));
    var k = next / scale;
    tx = ax - (ax - tx) * k;
    ty = ay - (ay - ty) * k;
    scale = next;
    apply();
  }
  function fit() {
    scale = Math.max(MIN, Math.min(MAX, Math.min(_vw() / contentW, _vh() / contentH)));
    tx = 0; ty = 0;
    apply();
  }
  function one() { setScale(1); }
  function zoomBy(f, ax, ay) { setScale(scale * f, ax, ay); }

  var dragging = false, sx = 0, sy = 0;
  viewport.addEventListener('pointerdown', function (e) {
    if (e.button != null && e.button !== 0) return;
    dragging = true; sx = e.clientX - tx; sy = e.clientY - ty;
    viewport.classList.add('mm-dragging');
    try { viewport.setPointerCapture(e.pointerId); } catch (err) {}
    e.preventDefault();
  });
  viewport.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    tx = e.clientX - sx; ty = e.clientY - sy;
    apply();
  });
  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    viewport.classList.remove('mm-dragging');
    try { viewport.releasePointerCapture(e.pointerId); } catch (err) {}
  }
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', endDrag);
  viewport.addEventListener('wheel', function (e) {
    e.preventDefault();
    var rect = viewport.getBoundingClientRect ? viewport.getBoundingClientRect() : { left: 0, top: 0 };
    zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });

  return {
    fit: fit, one: one, setScale: setScale, zoomBy: zoomBy, apply: apply,
    getScale: function () { return scale; }
  };
}

function _mmToolbarHTML() {
  return '<button type="button" class="mm-btn" data-mm="fit">适应</button>'
    + '<button type="button" class="mm-btn" data-mm="out" aria-label="缩小">−</button>'
    + '<span class="mm-scale">100%</span>'
    + '<button type="button" class="mm-btn" data-mm="in" aria-label="放大">＋</button>'
    + '<button type="button" class="mm-btn mm-expand" data-mm="expand">⤢ 全屏查看</button>';
}

// 把渲染出来的 SVG 装进可缩放画布，并挂上工具条。
// 只处理「内容里嵌的流程图块」（.mermaid-block），不影响其它页面自带的图。
function mountMermaidViewport(block, renderEl) {
  if (!block || !renderEl) return null;
  var svg = renderEl.querySelector('svg');
  if (!svg || svg.getAttribute('data-mm-mounted')) return null;
  svg.setAttribute('data-mm-mounted', '1');

  var size = _mmSvgSize(svg);
  // 固定成图形原尺寸：不再被容器宽度压扁，字号保持设计大小
  svg.setAttribute('width', size.w);
  svg.setAttribute('height', size.h);
  svg.style.maxWidth = 'none';
  svg.style.width = size.w + 'px';
  svg.style.height = 'auto';

  renderEl.classList.add('mm-viewport');
  var canvas = document.createElement('div');
  canvas.className = 'mm-canvas';
  renderEl.appendChild(canvas);
  canvas.appendChild(svg);

  var tools = document.createElement('div');
  tools.className = 'mm-toolbar';
  tools.innerHTML = _mmToolbarHTML();
  block.appendChild(tools);
  var scaleEl = tools.querySelector('.mm-scale');

  var engine = _mmEngine(renderEl, canvas, size.w, size.h, function (s) {
    if (scaleEl) scaleEl.textContent = Math.round(s * 100) + '%';
  });
  // 默认：优先保证字看得清（接近原尺寸），比面板略宽时才缩一点点
  engine.setScale(Math.max(0.7, Math.min(1, (renderEl.clientWidth || 320) / size.w)));
  if (scaleEl) scaleEl.textContent = Math.round(engine.getScale() * 100) + '%';

  tools.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('[data-mm]') : null;
    if (!btn) return;
    var act = btn.getAttribute('data-mm');
    if (act === 'fit') engine.fit();
    else if (act === 'in') engine.zoomBy(1.2);
    else if (act === 'out') engine.zoomBy(1 / 1.2);
    else if (act === 'expand') openMermaidLightbox({ svg: svg, w: size.w, h: size.h, canvas: canvas, engine: engine });
  });

  return { svg: svg, w: size.w, h: size.h, canvas: canvas, engine: engine };
}

var _mmOpenLightbox = null;
function closeMermaidLightbox() {
  if (!_mmOpenLightbox) return;
  var st = _mmOpenLightbox;
  _mmOpenLightbox = null;
  try { st.canvas.appendChild(st.svg); } catch (e) {}
  st.box.parentNode && st.box.parentNode.removeChild(st.box);
  document.removeEventListener('keydown', st.onKey, true);
  if (st.engine) st.engine.apply();
}

// 全屏查看：把同一张 SVG 移进浮层（不复制，避免图内 id 冲突），看完放回原位。
function openMermaidLightbox(mount) {
  if (!mount || !mount.svg) return;
  closeMermaidLightbox();
  var box = document.createElement('div');
  box.className = 'mm-lightbox';
  box.innerHTML = '<div class="mm-lightbox-stage"><div class="mm-canvas"></div></div>'
    + '<div class="mm-toolbar mm-lightbox-tools">'
    + '<button type="button" class="mm-btn" data-mmlb="fit">适应</button>'
    + '<button type="button" class="mm-btn" data-mmlb="out" aria-label="缩小">−</button>'
    + '<span class="mm-scale">100%</span>'
    + '<button type="button" class="mm-btn" data-mmlb="in" aria-label="放大">＋</button>'
    + '<button type="button" class="mm-btn" data-mmlb="one">原图大小</button>'
    + '<button type="button" class="mm-btn mm-expand" data-mmlb="close">'
    + iconHtml('close') + ' 关闭</button>'
    + '</div>';
  document.body.appendChild(box);

  var stage = box.querySelector('.mm-lightbox-stage');
  var canvas = box.querySelector('.mm-canvas');
  var scaleEl = box.querySelector('.mm-scale');
  canvas.appendChild(mount.svg);

  var engine = _mmEngine(stage, canvas, mount.w, mount.h, function (s) {
    if (scaleEl) scaleEl.textContent = Math.round(s * 100) + '%';
  });
  engine.fit();

  box.addEventListener('click', function (e) {
    if (e.target === box) { closeMermaidLightbox(); return; }
    var btn = e.target && e.target.closest ? e.target.closest('[data-mmlb]') : null;
    if (!btn) return;
    var act = btn.getAttribute('data-mmlb');
    if (act === 'fit') engine.fit();
    else if (act === 'in') engine.zoomBy(1.2);
    else if (act === 'out') engine.zoomBy(1 / 1.2);
    else if (act === 'one') engine.one();
    else if (act === 'close') closeMermaidLightbox();
  });
  stage.addEventListener('dblclick', function () {
    engine.getScale() > 0.95 ? engine.fit() : engine.one();
  });

  var onKey = function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closeMermaidLightbox(); }
  };
  document.addEventListener('keydown', onKey, true);

  _mmOpenLightbox = {
    box: box, svg: mount.svg, canvas: mount.canvas,
    engine: mount.engine, onKey: onKey
  };
}

function renderNodeMermaid(mermaidCode, containerId) {
  if (!mermaidCode) return '';
  return '<div class="field-label">流程图</div>'
    + '<div class="mermaid-block" data-mermaid-id="' + containerId + '">'
    + '<pre class="mermaid-source" style="display:none;">' + escapeHtml(mermaidCode) + '</pre>'
    + '<div class="mermaid-render" id="' + containerId + '"></div></div>';
}

function renderNodeTables(tables) {
  if (!tables || tables.length === 0) return '';
  var html = '<div class="field-label">Tables</div>';
  tables.forEach(function(tableStr) {
    html += renderTableBlock(tableStr, []);
  });
  return html;
}

// ── 富文本块渲染（讲义/摘要等静态展示用） ──
// 与 formatContent 同口径处理「段落 / markdown 表 / HTML 表 / mermaid」，
// 但对讲义等无 mermaid 库的静态页面输出占位提示；段落保留单换行为 <br>。
// 真实数据中 HTML 表格常接在一段正文之后（同段落内），必须行级扫描才能切出来，
// 否则 <td> 会被整段 escapeHtml 成裸文本。
function formatTextBlocks(text, keywords, paraClass) {
  if (!text) return '';
  text = stripLocalImageRefs(text);
  paraClass = paraClass || 'content-para';
  var lines = String(text).split('\n');
  var html = '';
  var buf = [];
  function flushPara() {
    if (!buf.length) return;
    var s = buf.join('\n').trim();
    buf = [];
    if (!s) return;
    var processed = highlightKeywords(escapeHtml(s), keywords);
    processed = markdownToHtml(processed);
    html += '<p class="' + paraClass + '">' + processed.replace(/\n/g, '<br>') + '</p>';
  }
  var i = 0;
  while (i < lines.length) {
    var line = lines[i].trim();
    if (!line) { flushPara(); i++; continue; }
    if (line.startsWith('```')) {
      // 围栏代码块：mermaid 讲义里无法渲染给占位；普通代码块原样展示（不能吞掉内容）
      var isMermaid = line.toLowerCase().startsWith('```mermaid');
      flushPara();
      i++;
      var codeLines = [];
      while (i < lines.length && lines[i].trim() !== '```') {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++;
      if (isMermaid) {
        html += '<p class="' + paraClass + '">［此节点含流程图，请在软件内查看］</p>';
      } else if (codeLines.length) {
        html += '<pre style="white-space:pre-wrap;margin:4px 0;padding:6px 10px;background:#f5f0e8;border-radius:4px;font-size:10pt;">'
             + escapeHtml(codeLines.join('\n')) + '</pre>';
      }
      continue;
    }
    if (line.startsWith('|') && line.endsWith('|')) {
      var tbl = [];
      while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
        tbl.push(lines[i].trim());
        i++;
      }
      flushPara();
      html += renderMarkdownTable(tbl, keywords);
      continue;
    }
    var tIdx = line.toLowerCase().indexOf('<table');
    if (tIdx >= 0) {
      // 行内夹着前置文字的表格：先把文字当段落
      if (tIdx > 0) {
        var before = line.substring(0, tIdx);
        if (before.trim()) { buf.push(before); flushPara(); }
      }
      var tableHtml = line.substring(tIdx);
      i++;
      while (!/<\/table>/i.test(tableHtml) && i < lines.length) {
        tableHtml += '\n' + lines[i];
        i++;
      }
      html += renderHtmlTable(tableHtml, keywords);
      // </table> 之后同行/后续残余文字继续当段落
      var m = tableHtml.match(/<\/table>([\s\S]*)$/i);
      if (m && m[1].trim()) buf.push(m[1]);
      continue;
    }
    buf.push(line);
    i++;
  }
  flushPara();
  return html;
}
// MinerU 输出两种格式都会出现（content 里常嵌 <table><tr><td>…），
// 任何渲染表格的地方都应走这里，避免 <td> 以裸文本形式泄漏。
function renderTableBlock(tableStr, keywords) {
  if (!tableStr || !String(tableStr).trim()) return '';
  var s = String(tableStr).trim();
  if (/<table[\s>]/i.test(s)) return renderHtmlTable(s, keywords || []);
  var lines = s.split('\n').filter(function(l) { return l.trim(); });
  return renderMarkdownTable(lines, keywords || []);
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
  var t = stripLocalImageRefs(text);
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
        i++;
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


// Mermaid 图表主题：统一跟随 CDS 暖色表面，避免深色模式出现白底紫字。
function mermaidThemeConfig() {
  var dark = getTheme() === 'dark';
  return {
    startOnLoad: false,
    theme: 'base',
    themeVariables: {
      fontFamily: '"PingFang SC", "Hiragino Sans GB", system-ui, sans-serif',
      fontSize: '13px',
      background: dark ? '#131312' : '#fcfcfb',
      primaryColor: dark ? '#20201f' : '#ffffff',
      primaryTextColor: dark ? '#eeede8' : '#1f1e1d',
      primaryBorderColor: dark ? '#e08264' : '#d97757',
      lineColor: dark ? '#706c65' : '#c3b7aa',
      secondaryColor: dark ? '#2c2c2a' : '#f4f3ef',
      tertiaryColor: dark ? '#1a1a19' : '#f9f9f7',
      mainBkg: dark ? '#20201f' : '#ffffff',
      nodeBorder: dark ? '#e08264' : '#d97757',
      clusterBkg: dark ? '#1a1a19' : '#f4f3ef',
      clusterBorder: dark ? '#3a3833' : '#d9d3ca',
      titleColor: dark ? '#eeede8' : '#1f1e1d',
      edgeLabelBackground: dark ? '#1a1a19' : '#ffffff',
      textColor: dark ? '#eeede8' : '#1f1e1d'
    }
  };
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
          if (typeof mermaid === 'undefined' || !mermaid.render) {
            throw new Error('流程图组件未能加载（需要联网），请刷新页面重试');
          }
          if (mermaid.initialize) mermaid.initialize(mermaidThemeConfig());
          var id = renderEl.id || ('mermaid-' + Math.random().toString(36).substr(2, 9));
          var result = await mermaid.render(id + '-svg', code);
          renderEl.innerHTML = result.svg;
          renderEl.setAttribute('data-processed', 'true');
          // 装进可缩放画布：大图不再被压成看不清的小字
          mountMermaidViewport(block, renderEl);
        } catch (e) {
          renderEl.innerHTML = '<pre style="color:var(--danger);font-size:11px;">'
            + escapeHtml(e.message || String(e)) + '</pre>';
          renderEl.setAttribute('data-processed', 'true');
        }
      }
    }
  }
}


// ── Image Lightbox ──
var _ktImageLightbox = null;

function closeImageLightbox() {
  if (!_ktImageLightbox) return;
  var st = _ktImageLightbox;
  _ktImageLightbox = null;
  if (st.box && st.box.parentNode) st.box.parentNode.removeChild(st.box);
  document.removeEventListener('keydown', st.onKey, true);
}

function openImageLightbox(src, alt) {
  if (!src) return;
  closeImageLightbox();
  var box = document.createElement('div');
  box.className = 'kt-image-lightbox';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-label', alt || '图片预览');
  box.innerHTML = '<div class="kt-image-stage"><img alt="' + escapeHtml(alt || '') + '"></div>'
    + '<div class="kt-image-toolbar">'
    + '<button type="button" data-ktimg="out" aria-label="缩小">−</button>'
    + '<span class="kt-image-scale">100%</span>'
    + '<button type="button" data-ktimg="in" aria-label="放大">＋</button>'
    + '<button type="button" data-ktimg="reset">100%</button>'
    + '<button type="button" data-ktimg="close">' + iconHtml('close') + ' 关闭</button>'
    + '</div>';
  document.body.appendChild(box);

  var stage = box.querySelector('.kt-image-stage');
  var img = box.querySelector('img');
  var scaleEl = box.querySelector('.kt-image-scale');
  img.src = src;
  var scale = 1, tx = 0, ty = 0;
  var drag = null;

  function apply() {
    img.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    if (scaleEl) scaleEl.textContent = Math.round(scale * 100) + '%';
  }
  function zoomTo(next, clientX, clientY) {
    next = Math.max(1, Math.min(5, next));
    var rect = stage.getBoundingClientRect();
    var px = (clientX == null ? rect.left + rect.width / 2 : clientX) - (rect.left + rect.width / 2);
    var py = (clientY == null ? rect.top + rect.height / 2 : clientY) - (rect.top + rect.height / 2);
    var ratio = next / scale;
    tx = px - (px - tx) * ratio;
    ty = py - (py - ty) * ratio;
    scale = next;
    if (scale <= 1.01) { tx = 0; ty = 0; }
    apply();
  }
  function reset() { scale = 1; tx = 0; ty = 0; apply(); }

  stage.addEventListener('wheel', function (e) {
    e.preventDefault();
    zoomTo(scale * Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY);
  }, { passive: false });
  stage.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || scale <= 1.01) return;
    drag = { x: e.clientX, y: e.clientY, tx: tx, ty: ty, id: e.pointerId };
    try { stage.setPointerCapture(e.pointerId); } catch (err) {}
    e.preventDefault();
  });
  stage.addEventListener('pointermove', function (e) {
    if (!drag) return;
    tx = drag.tx + (e.clientX - drag.x);
    ty = drag.ty + (e.clientY - drag.y);
    apply();
  });
  function endDrag(e) {
    if (!drag) return;
    try { stage.releasePointerCapture(drag.id); } catch (err) {}
    drag = null;
  }
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);
  stage.addEventListener('dblclick', function (e) {
    scale > 1.05 ? reset() : zoomTo(2, e.clientX, e.clientY);
  });
  box.addEventListener('click', function (e) {
    if (e.target === box || e.target === stage) { closeImageLightbox(); return; }
    var btn = e.target.closest ? e.target.closest('[data-ktimg]') : null;
    if (!btn) return;
    var act = btn.getAttribute('data-ktimg');
    if (act === 'in') zoomTo(scale * 1.25);
    else if (act === 'out') zoomTo(scale / 1.25);
    else if (act === 'reset') reset();
    else if (act === 'close') closeImageLightbox();
  });
  var onKey = function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closeImageLightbox(); }
    else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomTo(scale * 1.2); }
    else if (e.key === '-') { e.preventDefault(); zoomTo(scale / 1.2); }
    else if (e.key === '0') { e.preventDefault(); reset(); }
  };
  document.addEventListener('keydown', onKey, true);
  _ktImageLightbox = { box: box, onKey: onKey };
  apply();
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
    guide: node.guide || '',
    prerequisites: node.prerequisites || [],
    fallback: !!node.fallback,
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
    guide: node.guide || '',
    prerequisites: node.prerequisites || [],
    fallback: !!node.fallback,
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

// ── Book / Module helpers（信息页与导图页共用） ──

// "模块" = 顶层分支；单册包装结构（根下仅一个容器，如 教心→9章）取容器的 children
function bookModules(tree) {
  if (!tree || !tree.children || !tree.children.length) return [];
  var top = tree.children;
  if (top.length === 1 && (top[0].children || []).length > 0) return top[0].children;
  return top;
}

function moduleStats(node) {
  var nodes = 0, examCount = 0, enhanced = 0, fallback = 0;
  (function walk(n) {
    nodes++;
    if (n.summary) enhanced++;
    if (n.fallback) fallback++;
    if (n.exam_points) examCount += n.exam_points.length;
    (n.children || []).forEach(walk);
  })(node);
  return { nodes: nodes, examCount: examCount, enhanced: enhanced, fallback: fallback };
}

// 学习顺序：优先用 prerequisites 拓扑排序（代码执行，不靠 LLM），失败退化为文档顺序
function computeStudyOrder(modules) {
  var n = modules.length;
  var titleToIdx = {};
  modules.forEach(function (m, i) { titleToIdx[m.title || m.name] = i; });
  var hasPrereqs = modules.some(function (m) { return m.prerequisites && m.prerequisites.length > 0; });
  if (!hasPrereqs) return modules.map(function (_, i) { return i; });

  var indeg = new Array(n).fill(0);
  var adj = modules.map(function () { return []; });
  modules.forEach(function (m, i) {
    (m.prerequisites || []).forEach(function (p) {
      if (titleToIdx[p] != null && titleToIdx[p] !== i) {
        adj[titleToIdx[p]].push(i);
        indeg[i]++;
      }
    });
  });
  // Kahn's algorithm，同层按文档顺序 → 结果确定
  var queue = [];
  for (var i = 0; i < n; i++) if (indeg[i] === 0) queue.push(i);
  var order = [];
  while (queue.length) {
    queue.sort(function (a, b) { return a - b; });
    var cur = queue.shift();
    order.push(cur);
    adj[cur].forEach(function (nx) { if (--indeg[nx] === 0) queue.push(nx); });
  }
  if (order.length < n) {
    for (var j = 0; j < n; j++) if (order.indexOf(j) === -1) order.push(j);  // 出现环：余下按文档序
  }
  return order;
}

// 概念/学习路径图：有 prerequisites → 依赖图；否则按学习顺序排成链
function conceptMermaid(modules, studyOrder) {
  var lines = ['graph LR'];
  var hasPrereqs = modules.some(function (m) { return m.prerequisites && m.prerequisites.length > 0; });
  var label = function (m) {
    var t = (m.title || m.name || '').replace(/["\[\]]/g, ' ').trim();
    return t.length > 16 ? t.substring(0, 15) + '…' : t;
  };
  if (hasPrereqs) {
    var titleToId = {};
    modules.forEach(function (m, i) { titleToId[m.title || m.name] = 'M' + i; });
    var anyEdge = false;
    modules.forEach(function (m, i) {
      (m.prerequisites || []).forEach(function (p) {
        if (titleToId[p]) { lines.push('  ' + titleToId[p] + ' --> M' + i); anyEdge = true; }
      });
    });
    modules.forEach(function (m, i) { lines.push('  M' + i + '["' + label(m) + '"]'); });
    if (!anyEdge) hasPrereqs = false;
  }
  if (!hasPrereqs) {
    var chain = studyOrder.map(function (i) { return 'M' + i + '["' + label(modules[i]) + '"]'; });
    lines.push('  ' + chain.join(' --> '));
  }
  return lines.join('\n');
}

// 从树根出发解析某标题的完整路径（含单册包装层），供深链与下钻
function modulePathByTitle(tree, title) {
  var found = null;
  (function walk(node, path) {
    if (found) return;
    if (path.length && (node.title || node.name) === title) { found = path; return; }
    (node.children || []).forEach(function (c) {
      walk(c, path.concat([c.title || c.name]));
    });
  })(tree, []);
  return found;
}

// ── Panel Manager（面板互斥：打开一个自动收起其余，避免侧栏叠在一起） ──
// 用法：KT.Panels.register('search', {isOpen, close});
//       打开某面板前先调 KT.Panels.closeOthers('search')
var _panels = {};

function registerPanel(name, api) {
  _panels[name] = api || {};
}

function _isPanelOpen(name) {
  var p = _panels[name];
  try { return !!(p && p.isOpen && p.isOpen()); } catch (e) { return false; }
}

// 收起除 name 之外所有已打开的面板
function closeOtherPanels(name) {
  Object.keys(_panels).forEach(function (n) {
    if (n === name) return;
    if (!_isPanelOpen(n)) return;
    try { _panels[n].close && _panels[n].close(); } catch (e) {}
  });
}

function closePanelByName(name) {
  if (!_isPanelOpen(name)) return;
  try { _panels[name].close && _panels[name].close(); } catch (e) {}
}

// ── Toast ──

function showToast(msg) {
  var toast = document.getElementById('toast');
  if (!toast) return;
  var text = String(msg == null ? '' : msg);
  var kind = /(失败|错误|无法|不能|异常)/.test(text) ? 'alert' : 'check';
  toast.innerHTML = '<span class="toast-ic">' + iconHtml(kind) + '</span>'
    + '<span class="toast-copy">' + escapeHtml(text).replace(/\n/g, '<br>') + '</span>';
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
  // Motion
  prefersReducedMotion: prefersReducedMotion,
  gsapReady: gsapReady,
  // Colors
  getNodeColor: getNodeColor,
  getTooltipColors: getTooltipColors,
  getChartLabelColor: getChartLabelColor,
  getChartLeafLabelColor: getChartLeafLabelColor,
  // HTML
  escapeHtml: escapeHtml,
  htmlToMarkdown: htmlToMarkdown,
  markdownToHtml: markdownToHtml,
  stripLocalImageRefs: stripLocalImageRefs,
  highlightKeywords: highlightKeywords,
  // Content
  formatSummary: formatSummary,
  formatContent: formatContent,
  renderInlineFormulas: renderInlineFormulas,
  renderMarkdownTable: renderMarkdownTable,
  renderHtmlTable: renderHtmlTable,
  renderTableBlock: renderTableBlock,
  renderNodeTables: renderNodeTables,
  formatTextBlocks: formatTextBlocks,
  renderNodeMermaid: renderNodeMermaid,
  renderHighlights: renderHighlights,
  applyHighlights: applyHighlights,
  postRenderMathAndMermaid: postRenderMathAndMermaid,
  mermaidThemeConfig: mermaidThemeConfig,
  // Mermaid 可缩放画布
  mountMermaidViewport: mountMermaidViewport,
  openMermaidLightbox: openMermaidLightbox,
  closeMermaidLightbox: closeMermaidLightbox,
  openImageLightbox: openImageLightbox,
  closeImageLightbox: closeImageLightbox,
  // 图标
  iconHtml: iconHtml,
  applyIconSlots: applyIconSlots,
  // Tree
  transformNode: transformNode,
  transformNodeWithPath: transformNodeWithPath,
  flattenNodes: flattenNodes,
  // Book / Module
  bookModules: bookModules,
  moduleStats: moduleStats,
  computeStudyOrder: computeStudyOrder,
  conceptMermaid: conceptMermaid,
  modulePathByTitle: modulePathByTitle,
  // Panels（互斥管理）
  Panels: {
    register: registerPanel,
    closeOthers: closeOtherPanels,
    close: closePanelByName,
    isOpen: _isPanelOpen
  },
  // ECharts
  getLabelPosition: getLabelPosition,
  getLeafLabelPosition: getLeafLabelPosition,
  wrapLabel: wrapLabel,
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
