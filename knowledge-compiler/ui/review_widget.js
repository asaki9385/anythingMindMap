/* ════════════════════════════════════════════════════════════════
   对话复习 · 悬浮组件（自然 AI 对话 · 流式）
   形态：右下角迷你输入框（只能输入+发送，顶边融合上箭头）；
        点发送或点上箭头 → 向上展开为完整对话面板；点 ⊙ 收回迷你框。
   用法：
     ReviewWidget.mount({ file: 'task_xxx', chapter: '第一章 心理发展' });
     ReviewWidget.startChapter('第一章 心理发展');   // 信息页章节卡片按钮（显式方向，直接开始）
     ReviewWidget.setContext({ chapter: '…' });      // 导图页切换章节时同步
   流程：从迷你输入框 / 导图页入口打开时，先按导图内容展示"复习方向"列表
        （章节分组 + 全书混合，来自 /api/review/units），用户选定后才开始对话；
        显式 startChapter（信息页章节卡 / 会话中的切换按钮）跳过选择、直接开始。
   ════════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  // 离开超过这么久再回来（且上一轮没结束）→ 打开时先问「接着上次 / 换方向」
  var RESUME_PROMPT_AFTER = 30 * 60;

  var state = {
    mounted: false, open: false, file: '', chapter: '', sessionId: '',
    sessionChapter: '', _pendingChapter: '',
    snap: null, done: false, busy: false, degraded: false, streamText: '', streamKind: '',
    pendingSend: '',
    chooser: false, chooseData: null, chooseErr: '',
    scope: 'all', _directStart: false,
    mode: 'probe',   // 'probe' 追问模式 | 'learn' 学习模式
    learnMsgs: [], _learnCount: undefined, _reqMode: '',
    resumePrompt: false, _resumeOk: '', container: null,
    mentions: [], mentionsLoading: null,
    suggestMode: null, suggestItems: [], suggestIndex: 0,
    suggestStart: 0, suggestEnd: 0, suggestLoading: false, suggestError: '',
    completionMode: false,
    prefs: { replyStyle: 'auto', thinkingEffort: 'standard', citationStyle: 'auto', language: 'zh', customInstruction: '' },
    messagesPinned: true, scrollJumping: false,
    hiddenRevealKeys: {},
    composing: false, lastCompEnd: 0
  };
  var els = {};

  function esc(s) {
    var t = (s == null ? '' : String(s));
    return (global.KT && KT.escapeHtml) ? KT.escapeHtml(t) : t.replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  // Markdown 富文本：列表 / 表格 / 行内与块级公式 / 代码 / 强调。
  // 非公式文本一律先转义；公式保留 $...$ / $$...$$ 交给 KaTeX 在 DOM 插入后排版。
  function listMatch(line) {
    var m = String(line || '').match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (!m) return null;
    return {
      indent: m[1].replace(/\t/g, '  ').length,
      ordered: /^\d/.test(m[2]),
      text: m[3]
    };
  }

  function formatMarkdownText(s) {
    var codeParts = [];
    var t = String(s == null ? '' : s).replace(/`([^`\n]+)`/g, function (_, code) {
      var token = '\u0000C' + codeParts.length + '\u0000';
      codeParts.push('<code>' + esc(code) + '</code>');
      return token;
    });
    t = esc(t);
    t = t.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    t = t.replace(/~~([^~\n]+)~~/g, '<s>$1</s>');
    t = t.replace(/\u0000C(\d+)\u0000/g, function (_, idx) { return codeParts[Number(idx)] || ''; });
    return t;
  }

  function inlineMd(s) {
    var text = String(s == null ? '' : s);
    var out = '';
    var last = 0;
    var match;
    var re = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g;
    while ((match = re.exec(text))) {
      out += formatMarkdownText(text.slice(last, match.index));
      out += '<span class="rw-formula-inline">' + esc(match[0]) + '</span>';
      last = match.index + match[0].length;
    }
    out += formatMarkdownText(text.slice(last));
    return out;
  }

  function splitTableRow(line) {
    var s = String(line || '').trim();
    if (s.charAt(0) === '|') s = s.slice(1);
    if (s.charAt(s.length - 1) === '|') s = s.slice(0, -1);
    return s.split('|').map(function (cell) { return cell.trim(); });
  }

  function renderChatTable(rows) {
    if (!rows || !rows.length) return '';
    if (rows.length === 1) return '<p class="rw-p">' + inlineMd(rows[0]) + '</p>';
    var header = splitTableRow(rows[0]);
    var start = (rows[1] && /^[\s|:-]+$/.test(rows[1])) ? 2 : 1;
    var html = '<div class="rw-table-wrap"><table class="rw-table"><thead><tr>';
    header.forEach(function (cell) { html += '<th>' + inlineMd(cell) + '</th>'; });
    html += '</tr></thead><tbody>';
    for (var r = start; r < rows.length; r++) {
      var cells = splitTableRow(rows[r]);
      html += '<tr>';
      for (var c = 0; c < header.length; c++) html += '<td>' + inlineMd(cells[c] || '') + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table></div>';
    return html;
  }

  function renderChatList(items) {
    if (!items || !items.length) return '';
    var baseIndent = Infinity;
    items.forEach(function (item) { if (item.indent < baseIndent) baseIndent = item.indent; });
    var top = items.filter(function (item) { return item.indent === baseIndent; });
    var tag = (top[0] && top[0].ordered) ? 'ol' : 'ul';
    var html = '<' + tag + '>';
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (item.indent !== baseIndent) continue;
      html += '<li>' + inlineMd(item.text);
      var nested = [];
      var j = i + 1;
      while (j < items.length && items[j].indent > baseIndent) {
        nested.push(items[j]);
        j++;
      }
      if (nested.length) {
        var nTag = nested[0].ordered ? 'ol' : 'ul';
        html += '<' + nTag + '>';
        nested.forEach(function (n) { html += '<li>' + inlineMd(n.text) + '</li>'; });
        html += '</' + nTag + '>';
        i = j - 1;
      }
      html += '</li>';
    }
    return html + '</' + tag + '>';
  }

  function isBlockStart(line) {
    var t = String(line || '').trim();
    return /^```/.test(t)
      || t.indexOf('$$') === 0
      || /^\|.*\|$/.test(t)
      || /^<table[\s>]/i.test(t)
      || !!listMatch(line)
      || /^(#{1,6})\s+/.test(t);
  }

  function md(s) {
    var raw = String(s == null ? '' : s).replace(/\r\n?/g, '\n');
    // 兼容从 Markdown/HTML 复制过来的前导空格与转义下划线。
    raw = raw.replace(/&#x20;/gi, ' ').replace(/\\_/g, '_');
    var lines = raw.split('\n');
    var html = '';
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      var trimmed = line.trim();
      if (!trimmed) { i++; continue; }

      if (/^```/.test(trimmed)) {
        var codeLines = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i].trim())) {
          codeLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++;
        html += '<pre class="rw-pre"><code>' + esc(codeLines.join('\n')) + '</code></pre>';
        continue;
      }

      if (trimmed.indexOf('$$') === 0) {
        var formulaLines = [trimmed];
        if (trimmed.length > 4 && trimmed.slice(-2) === '$$') {
          i++;
        } else {
          i++;
          while (i < lines.length) {
            formulaLines.push(lines[i].trim());
            if (lines[i].trim().slice(-2) === '$$') { i++; break; }
            i++;
          }
        }
        html += '<div class="rw-formula-block">' + esc(formulaLines.join('\n')) + '</div>';
        continue;
      }

      if (/^<table[\s>]/i.test(trimmed)) {
        var tableLines = [line];
        i++;
        while (i < lines.length) {
          tableLines.push(lines[i]);
          var last = lines[i].trim();
          i++;
          if (/<\/table>/i.test(last)) break;
        }
        if (global.KT && typeof KT.renderHtmlTable === 'function') {
          html += KT.renderHtmlTable(tableLines.join('\n'));
        } else {
          html += '<pre class="rw-pre"><code>' + esc(tableLines.join('\n')) + '</code></pre>';
        }
        continue;
      }

      if (/^\|.*\|$/.test(trimmed)) {
        var tableRows = [];
        while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
          tableRows.push(lines[i].trim());
          i++;
        }
        html += renderChatTable(tableRows);
        continue;
      }

      var listItem = listMatch(line);
      if (listItem) {
        var listItems = [];
        while (i < lines.length) {
          var li = listMatch(lines[i]);
          if (li) {
            listItems.push(li);
            i++;
            continue;
          }
          if (!lines[i].trim() && i + 1 < lines.length && listMatch(lines[i + 1])) {
            i++;
            continue;
          }
          break;
        }
        html += renderChatList(listItems);
        continue;
      }

      var heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        html += '<h4 class="rw-h">' + inlineMd(heading[2]) + '</h4>';
        i++;
        continue;
      }

      var para = [inlineMd(trimmed)];
      i++;
      while (i < lines.length) {
        var next = lines[i];
        if (!next.trim() || isBlockStart(next)) break;
        para.push(inlineMd(next.trim()));
        i++;
      }
      html += '<p class="rw-p">' + para.join('<br>') + '</p>';
    }
    return html;
  }

  var rwMathFrame = 0;
  function scheduleMath(container) {
    if (!container) return;
    if (rwMathFrame && global.cancelAnimationFrame) global.cancelAnimationFrame(rwMathFrame);
    var run = function () {
      if (!container || container.isConnected === false) return;
      if (global.KT && typeof KT.postRenderMathAndMermaid === 'function') {
        try {
          var pending = KT.postRenderMathAndMermaid(container);
          if (pending && typeof pending.catch === 'function') pending.catch(function () {});
        } catch (err) {}
      } else if (typeof global.renderMathInElement === 'function') {
        try {
          global.renderMathInElement(container, {
            delimiters: [
              { left: '$$', right: '$$', display: true },
              { left: '$', right: '$', display: false }
            ],
            throwOnError: false
          });
        } catch (err) {}
      }
    };
    if (global.requestAnimationFrame) rwMathFrame = global.requestAnimationFrame(run);
    else rwMathFrame = global.setTimeout(run, 0);
  }

  function toast(m) { if (global.KT && global.KT.showToast) global.KT.showToast(m); }

  // 章节标题太长时截断，仅用于按钮文案
  function shortTitle(t) {
    t = String(t || '');
    return t.length > 12 ? t.slice(0, 11) + '…' : t;
  }

  // ── 头像与加载 icon（与图标系统同风格：24 viewBox / stroke 1.75 / round） ──
  var SVG_AI = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="5" y="9" width="14" height="10" rx="3"/>'
    + '<circle cx="9.3" cy="14" r="1.15" fill="currentColor" stroke="none"/>'
    + '<circle cx="14.7" cy="14" r="1.15" fill="currentColor" stroke="none"/>'
    + '<path d="M12 5.5V9"/><circle cx="12" cy="4.2" r="1.4"/>'
    + '<path d="M5 13H3.7"/><path d="M20.3 13H19"/></svg>';
  var SVG_USER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round">'
    + '<circle cx="12" cy="8.6" r="3.4"/>'
    + '<path d="M5.6 19.6c0-3.5 2.9-6 6.4-6s6.4 2.5 6.4 6"/></svg>';
  var SVG_SPIN = '<svg class="rw-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">'
    + '<path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5"/></svg>';
  var SVG_UP = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M12 19V6"/><path d="M6 11l6-6 6 6"/></svg>';
  var SVG_SPARK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M10.6 3.2c.5 4.7 3.7 7.9 8.4 8.4-4.7.5-7.9 3.7-8.4 8.4-.5-4.7-3.7-7.9-8.4-8.4 4.7-.5 7.9-3.7 8.4-8.4Z"/>'
    + '<path d="M18.6 3.5c.15 1.35 1.05 2.25 2.4 2.4-1.35.15-2.25 1.05-2.4 2.4-.15-1.35-1.05-2.25-2.4-2.4 1.35-.15 2.25-1.05 2.4-2.4Z"/></svg>';
  var SVG_PAPERCLIP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="m20.4 11.6-7.9 7.9a6 6 0 0 1-8.5-8.5l8.6-8.6a4 4 0 0 1 5.7 5.7l-8.6 8.6a2 2 0 0 1-2.8-2.8l7.9-7.9"/></svg>';
  var SVG_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="m21 3-7.2 18-3.8-7-7-3.8L21 3Z"/><path d="m10 14 4.5-4.5"/></svg>';
  var SVG_DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M12 5v14"/><path d="m6 13 6 6 6-6"/></svg>';
  var SVG_COMPLETE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M10.6 3.2c.5 4.7 3.7 7.9 8.4 8.4-4.7.5-7.9 3.7-8.4 8.4-.5-4.7-3.7-7.9-8.4-8.4 4.7-.5 7.9-3.7 8.4-8.4Z"/>'
    + '<path d="M18.5 13.5v6"/><path d="M15.5 16.5h6"/></svg>';

  function avatarOf(role) {
    return role === 'learner'
      ? '<div class="rw-avatar user" title="我">' + SVG_USER + '</div>'
      : '<div class="rw-avatar ai" title="AI 助手">' + SVG_AI + '</div>';
  }

  // ── 样式（组件自带，注入一次） ──
  function injectCSS() {
    if (document.getElementById('rw-styles')) return;
    var css = ''
      // 迷你输入框（折叠态）：胶囊型，与左下角间距按钮（left:20 bottom:20 40×40）并列；
      // 内部无文字；顶边融合上箭头。一律用 #rwMini ID 选择器压制 theme.css 的全局 textarea/button 规则
      + '#rwMini{position:fixed;left:68px;bottom:20px;z-index:1500;width:min(210px,calc(100vw - 140px));height:40px;'
      + 'background:var(--surface,#fff);border:1px solid var(--border,rgba(0,0,0,.12));border-radius:var(--radius-full,999px);'
      + 'box-shadow:var(--shadow-lg,0 8px 32px rgba(0,0,0,.18));padding:4px 4px 4px 14px;display:flex;gap:6px;align-items:center;'
      + 'transition:opacity .22s ease, transform .26s cubic-bezier(.2,.8,.25,1), visibility .26s, left .34s var(--ease-out,ease-out);}'
      + '#rwMini.rw-mini-solo{left:20px;}'   // 页面无间距按钮时贴角
      + 'body:has(.spacing-slider.open) #rwMini{left:272px;}'   // 间距调整盒展开时让位
      + '#rwMini.hidden{opacity:0;visibility:hidden;pointer-events:none;transform:translateY(10px) scale(.96);}'
      + '#rwMini textarea{flex:1;resize:none;box-sizing:border-box;height:100%;min-height:0;max-height:none;padding:0;'
      + 'margin:0;line-height:1.4;background:transparent;color:var(--ink,#2c1810);'
      + 'border:none;border-radius:0;font-family:inherit;font-size:12.5px;outline:none;}'
      + '#rwMini textarea:focus{border:none;box-shadow:none;}'
      // 重置 theme 的 button 全局样式，再按需重设（保证箭头/按钮不被 padding 挤没）
      + '#rwMini button{appearance:none;-webkit-appearance:none;margin:0;padding:0;border:none;background:none;'
      + 'font-family:inherit;line-height:1;cursor:pointer;}'
      + '#rwMini button:hover{transform:none;}'
      // 胶囊内的圆形发送按钮
      + '#rwMini .rw-msend{box-sizing:border-box;height:32px;min-width:32px;padding:0 12px;flex:none;'
      + 'border-radius:var(--radius-full,999px);border:1px solid var(--accent,#c07a2e);background:var(--accent,#c07a2e);'
      + 'color:#fff;font-size:11.5px;font-weight:600;cursor:pointer;white-space:nowrap;'
      + 'display:flex;align-items:center;justify-content:center;}'
      + '#rwMini .rw-msend:hover{background:var(--accent-hover,#a8681e);transform:none;}'
      // 上箭头：与上框融合的小把手
      + '#rwMini .rw-mini-up{position:absolute;top:-10px;left:50%;transform:translateX(-50%);width:24px;height:15px;'
      + 'border:1px solid var(--border,rgba(0,0,0,.12));border-bottom:none;border-radius:5px 5px 0 0;'
      + 'background:var(--surface,#fff);display:flex;align-items:center;justify-content:center;'
      + 'color:var(--ink-secondary,#6b5b4e);}'
      + '#rwMini .rw-mini-up svg{width:10px;height:10px;display:block;}'
      + '#rwMini .rw-mini-up:hover{color:var(--accent-hover,#a8681e);transform:translateX(-50%);}'
      // 完整对话面板：从左下角 mini 胶囊向上展开（transform-origin 对齐 mini 位置）
      + '.rw-panel{position:fixed;left:20px;bottom:20px;z-index:1504;width:min(400px,calc(100vw - 28px));'  // 高于搜索/章节面板(1502)，展开对话时不被遮挡
      + 'height:min(640px,82vh);display:flex;flex-direction:column;background:var(--surface,#fff);'
      + 'border:1px solid var(--border,rgba(0,0,0,.12));border-radius:var(--radius-lg,14px);overflow:hidden;'
      + 'box-shadow:0 12px 48px rgba(0,0,0,.22);'
      + 'opacity:0;visibility:hidden;pointer-events:none;transform:translateY(18px) scale(.96);'
      + 'transform-origin:bottom left;'
      + 'transition:opacity .28s ease, transform .34s cubic-bezier(.2,.8,.25,1), visibility .34s;}'
      + '.rw-panel.open{opacity:1;visibility:visible;pointer-events:auto;transform:none;}'
      + '.rw-head{padding:11px 14px;border-bottom:1px solid var(--border,rgba(0,0,0,.12));background:var(--surface-raised,#f5f0eb);}'
      + '.rw-head .rw-title{font-family:var(--font-serif,serif);font-weight:700;font-size:14px;color:var(--ink,#2c1810);'
      + 'display:flex;align-items:center;gap:8px;}'
      + '.rw-head .rw-sub{font-size:11.5px;color:var(--ink-muted,#9a8b7e);font-weight:400;flex:1;min-width:0;'
      + 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '.rw-head .rw-min{background:none;border:none;color:var(--ink-muted,#9a8b7e);cursor:pointer;font-size:15px;padding:2px 6px;border-radius:4px;}'
      + '.rw-head .rw-min:hover{background:var(--tag-accent-bg,rgba(192,122,46,.1));color:var(--ink,#2c1810);}'
      + '.rw-mode{display:flex;align-items:center;gap:2px;margin-left:8px;padding:2px;flex:none;'
      + 'background:var(--canvas,#faf8f5);border:1px solid var(--border,rgba(0,0,0,.12));border-radius:var(--radius-full,999px);}'
      + '.rw-mode button{appearance:none;-webkit-appearance:none;margin:0;border:none;background:none;cursor:pointer;'
      + 'font-family:inherit;font-size:11.5px;line-height:1;color:var(--ink-secondary,#6b5b4e);padding:5px 10px;'
      + 'border-radius:var(--radius-full,999px);transition:background .18s ease,color .18s ease;}'
      + '.rw-mode button.on{background:var(--accent,#c07a2e);color:#fff;}'
      + '.rw-mode button:hover{transform:none;}'
      + '.rw-meta{display:flex;gap:6px;margin-top:7px;flex-wrap:wrap;}'
      // 消息流：头像 + 气泡
      + '.rw-msgs{flex:1;overflow-y:auto;padding:12px;background:var(--canvas,#faf8f5);}'
      + '.rw-scroll-bottom{position:absolute;right:18px;z-index:30;width:34px;height:34px;display:flex;align-items:center;justify-content:center;'
      + 'padding:0;border:1px solid var(--border,rgba(0,0,0,.12));border-radius:50%;background:var(--surface,#fff);color:var(--ink-secondary,#6b5b4e);'
      + 'box-shadow:0 6px 20px rgba(0,0,0,.14),0 1px 3px rgba(0,0,0,.08);cursor:pointer;'
      + 'opacity:0;visibility:hidden;pointer-events:none;transform:translateY(6px) scale(.94);'
      + 'transition:opacity .18s ease,transform .22s var(--ease-out,ease),visibility .22s,background .15s ease,color .15s ease;}'
      + '.rw-scroll-bottom.is-visible{opacity:1;visibility:visible;pointer-events:auto;transform:none;}'
      + '.rw-scroll-bottom:hover{background:var(--surface-raised,#f5f0eb);color:var(--accent-hover,#a8681e);}'
      + '.rw-scroll-bottom:active{transform:scale(.94);}'
      + '.rw-scroll-bottom:focus-visible{outline:2px solid var(--accent,#c07a2e);outline-offset:2px;}'
      + '.rw-scroll-bottom svg{width:16px;height:16px;display:block;}'
      + '.rw-msg{margin-bottom:12px;display:flex;gap:8px;align-items:flex-start;}'
      + '.rw-msg .rw-b{max-width:78%;padding:9px 12px;border-radius:12px;font-size:13px;line-height:1.65;'
      + 'white-space:normal;word-break:break-word;}'
      + '.rw-avatar{width:28px;height:28px;flex:none;border-radius:50%;display:flex;align-items:center;justify-content:center;}'
      + '.rw-avatar svg{width:16px;height:16px;}'
      + '.rw-avatar.ai{background:var(--tag-accent-bg,rgba(192,122,46,.12));color:var(--accent-hover,#a8681e);'
      + 'border:1px solid var(--border-accent,rgba(192,122,46,.3));}'
      + '.rw-avatar.user{background:var(--surface-raised,#f5f0eb);color:var(--ink-secondary,#6b5b4e);'
      + 'border:1px solid var(--border,rgba(0,0,0,.12));}'
      + '.rw-msg.guide .rw-b{background:var(--surface,#fff);border:1px solid var(--border-accent,rgba(192,122,46,.3));'
      + 'border-left:3px solid var(--accent,#c07a2e);color:var(--ink,#2c1810);}'
      + '.rw-msg.hint .rw-b{border-left-color:var(--warning,#c07a2e);}'
      // 头像位置约定：AI 头像居左（气泡左侧），用户头像居右（气泡右侧）
      + '.rw-msg{justify-content:flex-start;}'
      + '.rw-msg.learner{justify-content:flex-end;}'
      + '.rw-msg.learner .rw-b{background:var(--surface-raised,#f5f0eb);border:1px solid var(--border,rgba(0,0,0,.12));color:var(--ink,#2c1810);}'
      + '.rw-msg.system .rw-b{background:var(--canvas,#faf8f5);border:1px dashed var(--border-strong,rgba(0,0,0,.2));'
      + 'color:var(--ink-secondary,#6b5b4e);font-size:12px;}'
      + '.rw-msg.reveal .rw-b{position:relative;padding-right:42px;background:var(--tag-accent-bg,rgba(192,122,46,.1));border:1px solid var(--border-accent,rgba(192,122,46,.3));}'
      + '.rw-reveal-toggle{position:absolute;top:7px;right:7px;width:28px;height:28px;display:grid;place-items:center;'
      + 'border:0;border-radius:8px;background:color-mix(in srgb,var(--surface,#fff) 58%,transparent);'
      + 'color:var(--accent-hover,#a8681e);cursor:pointer;padding:0;'
      + 'transition:background .15s ease,color .15s ease,transform .15s ease;}'
      + '.rw-reveal-toggle:hover{background:var(--surface-raised,rgba(255,255,255,.9));color:var(--accent-hover,#a8681e);}'
      + '.rw-reveal-toggle:active{transform:scale(.94);}'
      + '.rw-reveal-toggle:focus-visible{outline:2px solid var(--accent,#c07a2e);outline-offset:1px;}'
      + '.rw-reveal-toggle svg{width:15px;height:15px;display:block;}'
      + '.rw-reveal-content[hidden],.rw-reveal-note[hidden]{display:none!important;}'
      + '.rw-reveal-note{display:flex;align-items:center;color:var(--ink-secondary,#6b5b4e);font-size:12px;line-height:1.5;padding:2px 0;}'
      + '.rw-msg.report .rw-b{background:var(--surface,#fff);border:1px solid var(--success,#3a8a50);font-size:12.5px;}'
      + '.rw-b strong{color:var(--accent-hover,#a8681e);font-weight:700;}'
      + '.rw-b code{font-family:var(--font-mono,monospace);font-size:12px;background:var(--surface-raised,#f5f0eb);'
      + 'padding:1px 5px;border-radius:4px;}'
      + '.rw-b em{font-style:normal;border-bottom:1.5px dashed var(--accent,#c07a2e);}'
      + '.rw-msg .rw-b p.rw-p{margin:0 0 8px;}'
      + '.rw-msg .rw-b p.rw-p:last-child{margin-bottom:0;}'
      + '.rw-msg .rw-b ul,.rw-msg .rw-b ol{margin:6px 0 8px;padding-left:20px;}'
      + '.rw-msg .rw-b li{margin:3px 0;}'
      + '.rw-msg .rw-b li>ul,.rw-msg .rw-b li>ol{margin:3px 0;padding-left:18px;}'
      + '.rw-msg .rw-b h4.rw-h{margin:8px 0 6px;font-size:13.5px;font-weight:650;color:var(--ink,#2c1810);}'
      + '.rw-table-wrap{max-width:100%;overflow-x:auto;margin:8px 0;}'
      + '.rw-msg .rw-b table.rw-table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.5;}'
      + '.rw-msg .rw-b table.rw-table th,.rw-msg .rw-b table.rw-table td{border:1px solid var(--border,rgba(0,0,0,.12));padding:6px 8px;text-align:left;vertical-align:top;}'
      + '.rw-msg .rw-b table.rw-table th{background:var(--surface-raised,#f5f0eb);font-weight:600;color:var(--ink,#2c1810);}'
      + '.rw-msg .rw-b .table-wrapper{max-width:100%;overflow-x:auto;margin:8px 0;}'
      + '.rw-msg .rw-b .md-table{width:100%;border-collapse:collapse;font-size:12px;}'
      + '.rw-msg .rw-b .md-table th,.rw-msg .rw-b .md-table td{border:1px solid var(--border,rgba(0,0,0,.12));padding:6px 8px;text-align:left;}'
      + '.rw-formula-block{margin:8px 0;overflow-x:auto;overflow-y:hidden;}'
      + '.rw-msg .rw-b .katex-display{margin:8px 0;padding:2px 0;overflow-x:auto;overflow-y:hidden;}'
      + '.rw-msg .rw-b .katex{font-size:1.02em;}'
      + '.rw-msg .rw-b .rw-formula-inline{white-space:nowrap;}'
      + '.rw-msg .rw-b pre.rw-pre{margin:8px 0;padding:10px 12px;overflow-x:auto;border-radius:8px;background:var(--surface-raised,#f5f0eb);}'
      + '.rw-msg .rw-b pre.rw-pre code{padding:0;background:transparent;font-size:12px;}'
      // 思考中：AI 头像右侧的思考行，出现在对话气泡流的末尾位置（不是页面底部），含旋转加载 icon
      + '.rw-msg.rw-in{animation:rwMsgIn .24s ease both;}'
      + '@keyframes rwMsgIn{from{opacity:0;transform:translateY(8px);}}'
      + '.rw-typing{display:none;align-items:center;gap:8px;margin:0 0 12px;}'
      + '.rw-typing.on{display:flex;}'
      + '.rw-typing .rw-tdot{display:flex;align-items:center;gap:6px;padding:7px 12px;border-radius:12px;'
      + 'background:var(--surface,#fff);border:1px solid var(--border-accent,rgba(192,122,46,.3));'
      + 'color:var(--ink-muted,#9a8b7e);font-size:12px;}'
      + '.rw-spin{width:13px;height:13px;animation:rwSpin .8s linear infinite;}'
      + '.rw-typing .rw-dot{width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.35;'
      + 'animation:rwDot 1.1s ease-in-out infinite;}'
      + '.rw-typing .rw-dot:nth-child(2){animation-delay:.15s;}'
      + '.rw-typing .rw-dot:nth-child(3){animation-delay:.3s;}'
      + '@keyframes rwDot{0%,60%,100%{opacity:.3;transform:translateY(0);}30%{opacity:1;transform:translateY(-3px);}}'
      + '.rw-sources{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;padding-top:7px;border-top:1px solid var(--border,rgba(0,0,0,.08));}'
      + '.rw-source-pill{display:inline-flex;align-items:center;gap:4px;max-width:100%;padding:3px 8px;border-radius:999px;background:var(--tag-accent-bg,rgba(192,122,46,.1));color:var(--accent-hover,#a8681e);font-size:9px;line-height:1.35;}'
      + '.rw-source-pill svg{width:10px;height:10px;flex:none;opacity:.75;}'
      + '.rw-b .rw-caret{display:inline-block;width:2px;height:1em;margin-left:2px;vertical-align:-2px;'
      + 'background:currentColor;animation:rwCaret .9s steps(1) infinite;}'
      + '@keyframes rwCaret{50%{opacity:0;}}'
      + '@media (prefers-reduced-motion: reduce){.rw-msg.rw-in{animation:none;}.rw-scroll-bottom{transition:none;}'
      + '.rw-typing .rw-dot{animation:none;opacity:.6;}'
      + '.rw-b .rw-caret{animation:none;opacity:.5;}}'
      + '@keyframes rwSpin{to{transform:rotate(360deg);}}'
      + '.rw-quick{display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px 0;background:var(--surface,#fff);}'
      + '.rw-quick:empty{display:none;}'
      + '.rw-qbtn{font-size:12px;padding:5px 12px;border-radius:999px;cursor:pointer;'
      + 'background:var(--surface-raised,#f5f0eb);border:1px solid var(--border,rgba(0,0,0,.12));'
      + 'color:var(--ink-secondary,#6b5b4e);transition:all .15s;}'
      + '.rw-qbtn:hover{border-color:var(--accent,#c07a2e);color:var(--accent-hover,#a8681e);'
      + 'background:var(--tag-accent-bg,rgba(192,122,46,.1));}'
      + '.rw-qbtn.primary{background:var(--accent,#c07a2e);color:#fff;border-color:var(--accent,#c07a2e);}'
      + '.rw-inputbar{display:flex;gap:8px;padding:10px 12px 12px;background:var(--surface,#fff);align-items:flex-end;}'
      + '.rw-inputbar textarea{flex:1;resize:none;min-height:42px;max-height:120px;padding:10px 12px;'
      + 'background:var(--canvas,#faf8f5);color:var(--ink,#2c1810);border:1px solid var(--border,rgba(0,0,0,.12));'
      + 'border-radius:10px;font-size:13px;line-height:1.5;outline:none;font-family:inherit;}'
      + '.rw-inputbar textarea:focus{border-color:var(--accent,#c07a2e);}'
      + '.rw-inputbar button{padding:9px 16px;border-radius:10px;border:1px solid var(--accent,#c07a2e);'
      + 'background:var(--accent,#c07a2e);color:#fff;font-size:13px;cursor:pointer;white-space:nowrap;}'
      + '.rw-inputbar button:hover{background:var(--accent-hover,#a8681e);}'
      + '.rw-inputbar button:disabled{opacity:.5;cursor:not-allowed;}'
      + '.rw-composer{position:relative;background:var(--surface,#fff);}'
      + '.rw-suggest{position:absolute;left:10px;right:10px;bottom:calc(100% + 8px);z-index:20;border-radius:14px;overflow:hidden;background:var(--surface,#fff);border:1px solid var(--border,rgba(0,0,0,.12));box-shadow:0 14px 38px rgba(0,0,0,.16);}'
      + '.rw-suggest-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 11px;border-bottom:1px solid var(--border,rgba(0,0,0,.08));font-size:10px;font-weight:600;color:var(--ink-secondary,#6b5b4e);}'
      + '.rw-suggest-head span:last-child{font-weight:400;font-size:9px;color:var(--ink-muted,#9a8b7e);}'
      + '.rw-suggest-list{max-height:220px;overflow-y:auto;padding:5px;}'
      + '.rw-suggest-item{display:flex;align-items:center;gap:9px;width:100%;padding:8px 9px;border:0;border-radius:9px;background:transparent;text-align:left;cursor:pointer;color:var(--ink,#2c1810);}'
      + '.rw-suggest-item:hover,.rw-suggest-item.is-selected{background:var(--tag-accent-bg,rgba(192,122,46,.1));color:var(--accent-hover,#a8681e);}'
      + '.rw-suggest-key{font-family:var(--font-mono,monospace);font-size:10.5px;font-weight:600;white-space:nowrap;}'
      + '.rw-suggest-desc{margin-left:auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9.5px;color:var(--ink-muted,#9a8b7e);}'
      + '.rw-suggest-empty{padding:14px 12px;color:var(--ink-muted,#9a8b7e);font-size:12px;text-align:center;}'
      + '.rw-answer-meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:8px;}'
      + '.rw-supplement-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:999px;'
      + 'background:var(--tag-accent-bg,rgba(192,122,46,.1));color:var(--accent-hover,#a8681e);font-size:9px;font-weight:600;}'
      + '.rw-chin{display:flex;align-items:center;gap:7px;padding:0 12px 10px;font-size:10px;color:var(--ink-muted,#9a8b7e);}'
      + '.rw-chin-model{font-weight:600;color:var(--ink-secondary,#6b5b4e);max-width:48%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '.rw-chin-effort{display:inline-flex;align-items:center;height:18px;padding:0 7px;border-radius:999px;background:var(--tag-accent-bg,rgba(192,122,46,.1));color:var(--accent-hover,#a8681e);font-size:9px;white-space:nowrap;}'
      + '.rw-chin-hint{margin-left:auto;font-size:9px;color:var(--ink-muted,#9a8b7e);white-space:nowrap;}'
      // 复习方向选择（先选方向，再开始对话）
      + '.rw-choose{padding:16px 16px 6px;}'
      + '.rw-choose-title{font-size:14px;font-weight:600;color:var(--ink,#2c1810);margin-bottom:4px;}'
      + '.rw-choose-sub{font-size:12px;color:var(--ink-secondary,#6b5b4e);margin-bottom:12px;}'
      + '.rw-copt{display:block;width:100%;text-align:left;box-sizing:border-box;padding:10px 14px;margin-bottom:8px;'
      + 'background:var(--surface,#fff);border:1px solid var(--border,rgba(0,0,0,.12));border-radius:10px;cursor:pointer;'
      + 'font-size:13px;color:var(--ink,#2c1810);font-family:inherit;transition:all .15s;}'
      + '.rw-copt:hover{border-color:var(--accent,#c07a2e);background:var(--tag-accent-bg,rgba(192,122,46,.08));}'
      + '.rw-copt .t{display:block;font-weight:600;}'
      + '.rw-copt .s{display:block;margin-top:2px;font-size:11.5px;color:var(--ink-muted,#9a8b7a);font-weight:normal;}'
      + '.rw-copt.primary{border-color:var(--accent,#c07a2e);background:var(--tag-accent-bg,rgba(192,122,46,.06));}'
      + '.rw-copt-badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:6px;font-size:10.5px;'
      + 'background:var(--tag-accent-bg,rgba(192,122,46,.1));color:var(--accent-hover,#a8681e);font-weight:normal;}'
      + '.rw-choose-load{padding:26px 14px;display:flex;align-items:center;gap:10px;color:var(--ink-secondary,#6b5b4e);font-size:13px;}'
      + '.rw-choose-load .rw-spin{width:16px;height:16px;}'
      + '.rw-choose-err{padding:6px 2px 10px;font-size:13px;color:var(--danger,#b3423a);line-height:1.7;}';
    var style = document.createElement('style');
    style.id = 'rw-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ── DOM ──
  function build() {
    if (state.mounted) return;
    injectCSS();

    var mini = document.createElement('div');
    mini.className = 'rw-mini hidden';
    mini.id = 'rwMini';
    // 与左下角间距按钮并列；页面没有间距按钮时贴左下角
    if (!document.querySelector('.spacing-slider')) mini.classList.add('rw-mini-solo');
    mini.innerHTML = ''
      + '<button class="rw-mini-up" id="rwMiniUp" title="展开对话">' + SVG_UP + '</button>'
      + '<textarea id="rwMiniInput" rows="1" placeholder="" aria-label="输入内容"></textarea>'
      + '<button class="rw-msend" id="rwMiniSend">发送</button>';

    var panel = document.createElement('div');
    panel.className = 'rw-panel';
    panel.id = 'rwPanel';
    panel.innerHTML = ''
      + '<div class="rw-head">'
      + '  <div class="rw-title"><span class="rw-title-text">'
      + (global.KT && global.KT.iconHtml ? global.KT.iconHtml('target') : '')
      + ' ' + esc(state.title || '对话复习') + '</span><span class="rw-sub" id="rwSub"></span>'
      + '    <div class="rw-mode" id="rwMode" role="group" aria-label="模式切换">'
      + '      <button type="button" id="rwModeLearn" aria-pressed="false" title="学习模式：自由提问，AI 依据原文讲解，出处随答附上">学习</button>'
      + '      <button type="button" id="rwModeProbe" aria-pressed="true" title="追问模式：AI 提问，你来答；答不全就继续追问">追问</button>'
      + '    </div>'
      + '    <button class="rw-min" id="rwMin" title="收起">'
      + (global.KT && global.KT.iconHtml ? global.KT.iconHtml('chevronDown') : '') + '</button></div>'
      + '  <div class="rw-meta" id="rwMeta"></div>'
      + '</div>'
      + '<div class="rw-msgs" id="rwMsgs"></div>'
      + '<button class="rw-scroll-bottom" id="rwScrollBottom" type="button" title="回到底部" aria-label="回到底部" aria-hidden="true" tabindex="-1">' + SVG_DOWN + '</button>'
      + '<div class="rw-quick" id="rwQuick"></div>'
      + '<div class="rw-composer">'
      + '  <div class="rw-suggest" id="rwSuggest" hidden>'
      + '    <div class="rw-suggest-head" id="rwSuggestHead"></div>'
      + '    <div class="rw-suggest-list" id="rwSuggestList"></div>'
      + '  </div>'
      + '  <div class="rw-inputbar">'
      + '    <span class="rw-compose-lead" aria-hidden="true">' + SVG_SPARK + '</span>'
      + '    <textarea id="rwInput" rows="1" placeholder="有什么问题可以问我…" aria-label="向学习助手提问" aria-describedby="rwComposerHint"></textarea>'
      + '    <div class="rw-compose-tools">'
      + '      <button id="rwMentionBtn" class="rw-compose-tool" type="button" title="引用章节" aria-label="引用章节" aria-controls="rwSuggest" aria-expanded="false">' + SVG_PAPERCLIP + '</button>'
      + '      <button id="rwCompleteBtn" class="rw-compose-tool" type="button" title="知识补全：资料不足时使用 AI 通用知识补充" aria-label="知识补全" aria-pressed="false">' + SVG_COMPLETE + '</button>'
      + '    </div>'
      + '    <button id="rwSend" type="button" aria-label="发送消息">' + SVG_SEND + '<span>发送</span></button>'
      + '  </div>'
      + '  <div class="rw-chin">'
      + '    <span class="rw-chin-model" id="rwChinModel">Claude</span>'
      + '    <span class="rw-chin-effort" id="rwChinEffort">标准</span>'
      + '    <span class="rw-chin-hint" id="rwComposerHint">Enter 发送 · Shift+Enter 换行</span>'
      + '  </div>'
      + '</div>';
    var host = state.container || document.body;
    if (state.container) {
      mini.classList.add('hidden');
      panel.classList.add('rw-embedded');
    }
    host.appendChild(mini);
    host.appendChild(panel);

    // 思考行作为独立节点，渲染时挂在消息流末尾（气泡应出现的位置）
    var typing = document.createElement('div');
    typing.className = 'rw-typing';
    typing.id = 'rwTyping';
    typing.innerHTML = avatarOf('guide')
      + '<div class="rw-tdot"><i class="rw-dot"></i><i class="rw-dot"></i><i class="rw-dot"></i><span>思考中</span></div>';

    els.mini = mini; els.panel = panel;
    els.miniInput = mini.querySelector('#rwMiniInput');
    els.sub = panel.querySelector('#rwSub'); els.meta = panel.querySelector('#rwMeta');
    els.msgs = panel.querySelector('#rwMsgs'); els.typing = typing;
    els.quick = panel.querySelector('#rwQuick'); els.input = panel.querySelector('#rwInput');
    els.send = panel.querySelector('#rwSend');
    els.scrollBottom = panel.querySelector('#rwScrollBottom');
    els.mentionBtn = panel.querySelector('#rwMentionBtn');
    els.completeBtn = panel.querySelector('#rwCompleteBtn');
    els.chinModel = panel.querySelector('#rwChinModel');
    els.chinEffort = panel.querySelector('#rwChinEffort');
    els.suggest = panel.querySelector('#rwSuggest');
    els.suggestHead = panel.querySelector('#rwSuggestHead');
    els.suggestList = panel.querySelector('#rwSuggestList');
    els.modeLearn = panel.querySelector('#rwModeLearn');
    els.modeProbe = panel.querySelector('#rwModeProbe');
    els.modeLearn.addEventListener('click', function () { setMode('learn'); });
    els.modeProbe.addEventListener('click', function () { setMode('probe'); });

    mini.querySelector('#rwMiniUp').addEventListener('click', function () { open(true); });
    mini.querySelector('#rwMiniSend').addEventListener('click', miniSend);
    els.miniInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); miniSend(); }
    });
    panel.querySelector('#rwMin').addEventListener('click', close);
    els.send.addEventListener('click', function () { submit(); });
    els.msgs.addEventListener('scroll', onMessagesScroll, { passive: true });
    els.msgs.addEventListener('click', function (event) {
      var btn = event.target && event.target.closest ? event.target.closest('.rw-reveal-toggle') : null;
      if (!btn || !els.msgs.contains(btn)) return;
      event.preventDefault();
      toggleRevealMessage(btn);
    });
    els.scrollBottom.addEventListener('click', jumpMessagesToBottom);
    window.addEventListener('resize', positionScrollBottomButton);
    els.mentionBtn.addEventListener('click', function () {
      if (state.suggestMode === 'mention' && els.suggest && !els.suggest.hidden) {
        closeSuggestions();
        return;
      }
      openMentionPicker();
    });
    els.completeBtn.addEventListener('click', function () {
      toggleCompletionMode();
    });
    els.input.addEventListener('input', function () { if (!state.composing) detectSuggestions(); });
    els.input.addEventListener('compositionstart', function () { state.composing = true; });
    els.input.addEventListener('compositionend', function (e) {
      state.composing = false;
      state.lastCompEnd = e.timeStamp || performance.now();
    });
    els.input.addEventListener('keydown', handleComposerKeydown);
    els.suggestList.addEventListener('mousedown', function (e) {
      var item = e.target.closest('[data-suggest-index]');
      if (!item) return;
      e.preventDefault();
      applySuggestion(Number(item.getAttribute('data-suggest-index')));
    });
    // 面板互斥：对话打开时收起其他侧栏；其他面板打开时本对话自动收起
    if (global.KT && KT.Panels) {
      KT.Panels.register('review', {
        isOpen: function () { return state.open; },
        close: close
      });
    }
    state.mounted = true;
    updateModeUI();
    updateScrollBottomButton();
    announceActivity('idle', '已就绪，等待提问');
  }


  // ── Rich Composer：/ 指令与 @ 章节引用 ─────────────────────────
  function slashCommands() {
    return [
      { id: 'summary', name: 'summary', label: '梳理本章', desc: '用要点整理当前章节', prompt: '请用要点梳理当前章节的核心内容。' },
      { id: 'quiz', name: 'quiz', label: '出题考我', desc: '基于资料生成问题', prompt: '请根据当前资料出三个问题考我，先不要给答案。' },
      { id: 'explain', name: 'explain', label: '解释重点', desc: '解释重点并附原文依据', prompt: '请解释当前章节最重要的知识点，并给出原文依据。' },
      { id: 'complete', name: 'complete', label: '知识补全', desc: '资料不足时用 AI 通用知识补充', prompt: '请先依据当前资料回答；资料未覆盖的部分用「【AI 补充】」标注并补全。' }
    ];
  }

  function isAppleWebKit() {
    return typeof navigator !== 'undefined' && /Apple Computer/.test(navigator.vendor || '');
  }

  function closeSuggestions() {
    state.suggestMode = null;
    state.suggestItems = [];
    state.suggestIndex = 0;
    state.suggestLoading = false;
    state.suggestError = '';
    if (els.suggest) els.suggest.hidden = true;
    if (els.mentionBtn) els.mentionBtn.setAttribute('aria-expanded', 'false');
  }

  function loadChapterMentions() {
    if (state.mentions.length) return Promise.resolve();
    if (!state.file) {
      state.suggestError = '当前没有可引用的资料';
      return Promise.resolve();
    }
    if (state.mentionsLoading) return state.mentionsLoading;
    state.mentionsLoading = fetch('/api/review/units?file=' + encodeURIComponent(state.file) + '&max_units=300')
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
          var labels = (data.groups || []).map(function (g) { return g && g.name; }).filter(Boolean);
          if (!labels.length) {
            var seen = {};
            (data.units || []).forEach(function (u) {
              var name = u && (u.group || u.chapter);
              if (name && !seen[name]) { seen[name] = 1; labels.push(name); }
            });
          }
          state.mentions = labels.map(function (name) { return { id: String(name), label: String(name) }; });
        });
      })
      .catch(function (err) {
        state.suggestError = '章节读取失败：' + ((err && err.message) || err);
      })
      .then(function () {
        state.mentionsLoading = null;
      });
    return state.mentionsLoading;
  }

  async function openMentionPicker() {
    if (!els.input || !els.suggest) return;
    var start = typeof els.input.selectionStart === 'number' ? els.input.selectionStart : els.input.value.length;
    state.suggestMode = 'mention';
    state.suggestItems = state.mentions.slice();
    state.suggestIndex = 0;
    state.suggestStart = start;
    state.suggestEnd = start;
    state.suggestError = '';
    state.suggestLoading = !state.suggestItems.length;
    renderSuggestions();
    if (!state.suggestItems.length) {
      await loadChapterMentions();
      if (state.suggestMode !== 'mention') return;
      state.suggestLoading = false;
      state.suggestItems = state.mentions.slice();
      renderSuggestions();
    }
    els.input.focus();
  }

  function renderSuggestions() {
    if (!els.suggest || !els.suggestHead || !els.suggestList) return;
    if (!state.suggestMode) { closeSuggestions(); return; }
    var isSlash = state.suggestMode === 'slash';
    els.suggestHead.innerHTML = '<span>' + (isSlash ? '快捷指令' : '引用章节') + '</span><span>↑↓ 导航 · Enter 选择</span>';
    if (state.suggestLoading) {
      els.suggestList.innerHTML = '<div class="rw-suggest-empty">正在读取章节…</div>';
      els.suggest.hidden = false;
      if (els.mentionBtn) els.mentionBtn.setAttribute('aria-expanded', isSlash ? 'false' : 'true');
      return;
    }
    if (!state.suggestItems.length) {
      els.suggestList.innerHTML = '<div class="rw-suggest-empty">' + esc(state.suggestError || '当前资料暂无章节可引用') + '</div>';
      els.suggest.hidden = false;
      if (els.mentionBtn) els.mentionBtn.setAttribute('aria-expanded', isSlash ? 'false' : 'true');
      return;
    }
    els.suggestList.innerHTML = state.suggestItems.map(function (item, index) {
      var label = isSlash ? ('/' + item.name) : ('@' + item.label);
      var desc = isSlash ? (item.label + ' · ' + item.desc) : '将本章节作为对话上下文';
      return '<button class="rw-suggest-item' + (index === state.suggestIndex ? ' is-selected' : '') + '" type="button" data-suggest-index="' + index + '">'
        + '<span class="rw-suggest-key">' + esc(label) + '</span><span class="rw-suggest-desc">' + esc(desc) + '</span></button>';
    }).join('');
    els.suggest.hidden = false;
    if (els.mentionBtn) els.mentionBtn.setAttribute('aria-expanded', isSlash ? 'false' : 'true');
  }

  function detectSuggestions() {
    if (!els.input) return;
    var value = els.input.value;
    var pos = typeof els.input.selectionStart === 'number' ? els.input.selectionStart : value.length;
    var before = value.slice(0, pos);
    var match = /(^|\s)([/@])([^\s/]*)$/.exec(before);
    if (!match) { closeSuggestions(); return; }
    state.suggestLoading = false;
    state.suggestError = '';
    var mode = match[2] === '/' ? 'slash' : 'mention';
    var query = (match[3] || '').toLowerCase();
    var items = mode === 'slash'
      ? slashCommands().filter(function (cmd) {
          return !query || (cmd.name + ' ' + cmd.label + ' ' + cmd.desc).toLowerCase().indexOf(query) >= 0;
        })
      : state.mentions.filter(function (item) {
          return !query || String(item.label || '').toLowerCase().indexOf(query) >= 0;
        });
    if (!items.length) { closeSuggestions(); return; }
    state.suggestMode = mode;
    state.suggestItems = items;
    state.suggestIndex = 0;
    state.suggestStart = before.length - match[0].length + match[1].length;
    state.suggestEnd = pos;
    renderSuggestions();
  }

  function applySuggestion(index) {
    var item = state.suggestItems[index];
    if (!item || !els.input) return;
    var value = els.input.value;
    var insertion = state.suggestMode === 'slash' ? item.prompt : ('@' + item.label + ' ');
    var next = value.slice(0, state.suggestStart) + insertion + value.slice(state.suggestEnd);
    els.input.value = next;
    var caret = state.suggestStart + insertion.length;
    closeSuggestions();
    els.input.focus();
    try { els.input.setSelectionRange(caret, caret); } catch (e) {}
  }

  function handleComposerKeydown(e) {
    if (state.suggestMode) {
      if (state.suggestItems.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        var delta = e.key === 'ArrowDown' ? 1 : -1;
        state.suggestIndex = (state.suggestIndex + delta + state.suggestItems.length) % state.suggestItems.length;
        renderSuggestions();
        return;
      }
      if (state.suggestItems.length && (e.key === 'Enter' || e.key === 'Tab')) {
        e.preventDefault();
        applySuggestion(state.suggestIndex);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        closeSuggestions();
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      if (state.composing || e.isComposing || e.keyCode === 229) return;
      if (isAppleWebKit() && state.lastCompEnd && (e.timeStamp - state.lastCompEnd) < 500) return;
      e.preventDefault();
      submit();
    }
  }

  function setPreferences(prefs) {
    prefs = prefs && typeof prefs === 'object' ? prefs : {};
    var replyStyle = ['concise', 'auto', 'detailed'].indexOf(prefs.replyStyle) >= 0 ? prefs.replyStyle : 'auto';
    var thinkingEffort = ['fast', 'standard', 'deep'].indexOf(prefs.thinkingEffort) >= 0 ? prefs.thinkingEffort : 'standard';
    var citationStyle = ['minimal', 'auto', 'strong'].indexOf(prefs.citationStyle) >= 0 ? prefs.citationStyle : 'auto';
    var language = ['zh', 'bilingual'].indexOf(prefs.language) >= 0 ? prefs.language : 'zh';
    state.prefs = {
      replyStyle: replyStyle,
      thinkingEffort: thinkingEffort,
      citationStyle: citationStyle,
      language: language,
      customInstruction: String(prefs.customInstruction || '').slice(0, 300)
    };
  }

  function setMentions(items) {
    state.mentions = (items || []).filter(function (item) { return item && item.label; }).map(function (item) {
      return { id: String(item.id || item.label), label: String(item.label) };
    });
    if (state.suggestMode === 'mention' && !state.suggestLoading) {
      state.suggestItems = state.mentions.slice();
      state.suggestIndex = 0;
      renderSuggestions();
    } else if (els.input && /(^|\s)@[^\s]*$/.test(els.input.value)) {
      detectSuggestions();
    }
  }

  function composerPlaceholder() {
    return state.completionMode ? '提问；资料不足时由 AI 补全…' : '有什么问题可以问我…';
  }

  function syncCompletionModeUI() {
    if (!els.completeBtn) return;
    var available = state.mode === 'learn';
    if (!available) state.completionMode = false;
    els.completeBtn.disabled = !available;
    els.completeBtn.classList.toggle('is-active', !!state.completionMode);
    els.completeBtn.setAttribute('aria-pressed', state.completionMode ? 'true' : 'false');
    els.completeBtn.title = !available
      ? '知识补全（学习模式可用）'
      : (state.completionMode ? '知识补全已开启' : '知识补全：资料不足时使用 AI 通用知识补充');
  }

  function toggleCompletionMode() {
    if (state.mode !== 'learn') {
      toast('知识补全用于学习模式，请先切换到「学习」');
      return;
    }
    state.completionMode = !state.completionMode;
    syncCompletionModeUI();
    if (els.input && !els.input.disabled) els.input.placeholder = composerPlaceholder();
    toast(state.completionMode
      ? '已开启知识补全：资料不足处会用 AI 通用知识补充，并标注为「AI 补充」'
      : '已关闭知识补全');
  }

  // ── 模式切换：学习（自由问答，出处自动附上）/ 追问（AI 提问式复习） ──
  function loadMode() {
    try {
      var m = localStorage.getItem('kt_review_mode');
      if (m === 'learn' || m === 'probe') return m;
    } catch (e) {}
    return 'probe';
  }

  function updateModeUI() {
    if (!els.modeLearn) return;
    els.modeLearn.classList.toggle('on', state.mode === 'learn');
    els.modeProbe.classList.toggle('on', state.mode === 'probe');
    els.modeLearn.setAttribute('aria-pressed', state.mode === 'learn' ? 'true' : 'false');
    els.modeProbe.setAttribute('aria-pressed', state.mode === 'probe' ? 'true' : 'false');
    syncCompletionModeUI();
    try {
      document.dispatchEvent(new CustomEvent('kt-review-mode', { detail: { mode: state.mode } }));
    } catch (err) {}
  }

  function setMode(mode) {
    if (mode !== 'learn' && mode !== 'probe') return;
    if (state.mode === mode) return;
    state.mode = mode;
    closeSuggestions();
    try { localStorage.setItem('kt_review_mode', mode); } catch (e) {}
    updateModeUI();
    state.streamText = '';
    renderStream();
    if (!state.open) return;
    if (mode === 'learn') {
      ensureLearnIntro();
      renderLearn();
      renderQuick();
    } else {
      showProbeView();
    }
  }

  function showProbeView() {
    if (state.snap) render();
    else if (state.chooser && state.chooseData) { renderChooser(); renderQuick(); }
    else showChooser();
  }

  // ── API（NDJSON 流式：逐字 delta → 最终 snapshot；旧 JSON 响应兼容） ──
  // 错误响应要先说人话：404=服务端还是旧版本（常见于改完代码没重启服务），
  // 5xx=把服务端的原始报错带出来——不再统一伪装成"连接中断"。
  async function _httpError(res) {
    var msg = '';
    try {
      var body = await res.json();
      msg = body.error || body.detail || '';
    } catch (e) {}
    if (!msg) msg = 'HTTP ' + res.status;
    if (res.status === 404) {
      msg = '服务端没有这个接口（404）——当前服务器可能还是旧版本，把服务重启一次再试';
    } else if (res.status >= 500) {
      msg = '服务端出错了（' + res.status + '）：' + msg;
    }
    return msg;
  }

  async function api(path, body, onDelta) {
    var res = await fetch('/api/review' + path, body === undefined ? undefined : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({}, body, { stream: true }))
    });
    if (!res.ok) throw new Error(await _httpError(res));
    if (body !== undefined && res.body && typeof res.body.getReader === 'function') {
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buf = '', snap = null, err = null;
      var handleLine = function (line) {
        line = line.trim();
        if (!line) return;
        var ev;
        try { ev = JSON.parse(line); } catch (e) { return; }
        if (ev.t === 'd' && onDelta) onDelta(ev.text, ev.kind || '');
        else if (ev.t === 'snap') snap = ev.data;
        else if (ev.t === 'err') err = ev.error;
      };
      for (;;) {
        var r = await reader.read();
        if (r.done) break;
        buf += decoder.decode(r.value, { stream: true });
        var lines = buf.split('\n');
        buf = lines.pop();
        for (var li = 0; li < lines.length; li++) handleLine(lines[li]);
      }
      buf += decoder.decode();   // 冲刷解码器：处理最后一段没有换行的数据
      if (buf) handleLine(buf);
      if (err) throw new Error(err);
      if (!snap) throw new Error('连接中断，请重试');
      return snap;
    }
    var data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
  }

  function messagesMaxScroll() {
    if (!els.msgs) return 0;
    return Math.max(0, (els.msgs.scrollHeight || 0) - (els.msgs.clientHeight || 0));
  }

  function positionScrollBottomButton() {
    if (!els.scrollBottom || !els.msgs || !els.panel) return;
    try {
      var panelRect = els.panel.getBoundingClientRect();
      var messagesRect = els.msgs.getBoundingClientRect();
      if (!panelRect.height || !messagesRect.height) return;
      var bottom = Math.max(8, panelRect.bottom - messagesRect.bottom + 10);
      els.scrollBottom.style.bottom = bottom + 'px';
    } catch (err) {}
  }

  function updateScrollBottomButton() {
    if (!els.scrollBottom) return;
    var show = !state.messagesPinned && messagesMaxScroll() > 24;
    els.scrollBottom.classList.toggle('is-visible', show);
    els.scrollBottom.setAttribute('aria-hidden', show ? 'false' : 'true');
    els.scrollBottom.tabIndex = show ? 0 : -1;
    if (show) {
      positionScrollBottomButton();
      window.requestAnimationFrame(positionScrollBottomButton);
    }
  }

  function onMessagesScroll() {
    if (!els.msgs || state.scrollJumping) return;
    var distance = messagesMaxScroll() - (els.msgs.scrollTop || 0);
    state.messagesPinned = distance <= 28;
    updateScrollBottomButton();
  }

  function scrollMessagesToBottom(force) {
    if (!els.msgs) return;
    if (force) state.messagesPinned = true;
    if (state.messagesPinned) {
      els.msgs.scrollTop = els.msgs.scrollHeight;
      window.requestAnimationFrame(function () {
        if (state.messagesPinned && els.msgs) els.msgs.scrollTop = els.msgs.scrollHeight;
        positionScrollBottomButton();
      });
    }
    updateScrollBottomButton();
  }

  function captureMessageScroll() {
    return { top: els.msgs ? (els.msgs.scrollTop || 0) : 0, pinned: state.messagesPinned };
  }

  function restoreMessageScroll(snapshot) {
    if (!els.msgs || !snapshot) return;
    state.messagesPinned = !!snapshot.pinned;
    if (state.messagesPinned) {
      scrollMessagesToBottom(true);
      return;
    }
    els.msgs.scrollTop = snapshot.top;
    window.requestAnimationFrame(function () {
      if (!els.msgs || state.messagesPinned) return;
      els.msgs.scrollTop = snapshot.top;
      updateScrollBottomButton();
    });
    updateScrollBottomButton();
  }

  function jumpMessagesToBottom() {
    if (!els.msgs) return;
    state.messagesPinned = true;
    state.scrollJumping = true;
    updateScrollBottomButton();
    var behavior = (global.KT && KT.prefersReducedMotion && KT.prefersReducedMotion()) ? 'auto' : 'smooth';
    try {
      if (typeof els.msgs.scrollTo === 'function') {
        els.msgs.scrollTo({ top: els.msgs.scrollHeight, behavior: behavior });
      } else {
        els.msgs.scrollTop = els.msgs.scrollHeight;
      }
    } catch (err) {
      els.msgs.scrollTop = els.msgs.scrollHeight;
    }
    window.setTimeout(function () {
      state.scrollJumping = false;
      state.messagesPinned = true;
      if (els.msgs) els.msgs.scrollTop = els.msgs.scrollHeight;
      updateScrollBottomButton();
    }, behavior === 'smooth' ? 420 : 40);
  }

  function resetMessagesToTop() {
    if (!els.msgs) return;
    state.messagesPinned = false;
    els.msgs.scrollTop = 0;
    updateScrollBottomButton();
  }

  // 流式出字：主话语逐字播进临时气泡，快照到达后整体重渲染收口
  function onDelta(t, kind) {
    if (state._reqMode && state._reqMode !== state.mode) return;   // 已切走的模式不再往当前视图播字
    if (!state.streamText) {
      state.streamKind = kind || '';
      els.typing.classList.remove('on');
    } else if (kind) {
      state.streamKind = kind;
    }
    state.streamText += t;
    renderStream();
    emitConversationState();
  }
  function renderStream() {
    if (!els.msgs) return;
    var el = document.getElementById('rwStream');
    if (!state.streamText) { if (el && el.parentNode) el.parentNode.removeChild(el); return; }
    if (!el) {
      el = document.createElement('div');
      el.id = 'rwStream';
      el.className = 'rw-msg ' + (state.streamKind === 'reveal' ? 'reveal' : 'guide') + ' rw-in';
      el.innerHTML = avatarOf('guide') + '<div class="rw-b"></div>';
      // 流式气泡插在思考行之前（都在消息流末尾的气泡位）
      if (els.typing && els.typing.parentNode === els.msgs) els.msgs.insertBefore(el, els.typing);
      else els.msgs.appendChild(el);
    } else if (state.streamKind === 'reveal') {
      el.classList.remove('guide');
      el.classList.add('reveal');
    } else {
      el.classList.remove('reveal');
      el.classList.add('guide');
    }
    var streamBody = el.querySelector('.rw-b');
    streamBody.innerHTML = md(state.streamText) + '<span class="rw-caret"></span>';
    scheduleMath(streamBody);
    scrollMessagesToBottom();
  }


  function setProcess(phase, detail) {
    state.processPhase = phase || 'idle';
    state.processDetail = detail || '';
  }

  function announceActivity(phase, detail) {
    setProcess(phase, detail);
    try {
      document.dispatchEvent(new CustomEvent('kt-review-activity', {
        detail: { phase: phase, detail: detail || '', mode: state.mode, file: state.file, chapter: state.chapter }
      }));
    } catch (err) {}
  }

  function hasConversationContent() {
    if (state.busy || state.streamText) return true;
    if (state.mode === 'learn') {
      return state.learnMsgs.some(function (m) {
        return m && (m.role === 'learner' || m.kind === 'answer'
          || m.kind === 'verdict' || m.kind === 'clarify');
      });
    }
    var msgs = (state.snap && state.snap.messages) || [];
    return msgs.some(function (m) {
      return m && (m.role === 'learner' || ['question', 'hint', 'clarify', 'reveal', 'report', 'verdict'].indexOf(m.kind) >= 0);
    });
  }

  function emitConversationState() {
    try {
      document.dispatchEvent(new CustomEvent('kt-review-conversation', {
        detail: {
          active: hasConversationContent(), mode: state.mode,
          file: state.file, chapter: state.chapter, busy: !!state.busy
        }
      }));
    } catch (err) {}
  }

  async function startSession() {
    state.busy = true;
    state._reqMode = 'probe';
    state.resumePrompt = false;
    emitConversationState();
    announceActivity('thinking', '正在读取资料并准备复习方向…');
    renderQuick();
    try {
      var body = { file: state.file, scope: state.scope || 'all', prefs: state.prefs };
      if (state.chapter) body.chapter = state.chapter;
      var snap = await api('/start', body, onDelta);
      state.sessionId = snap.session_id;
      state.sessionChapter = state.chapter;
      state.degraded = !!snap.degraded;
      applySnapshot(snap);
      announceActivity('done', '已生成第一个复习问题');
    } catch (e) {
      state.snap = {
        messages: [{ role: 'system', kind: 'verdict', text: '无法开始复习：' + e.message }],
        units: [], counters: { turn: 0, turn_max: 6, hint: 0, hint_max: 2 }, done: true, report: null
      };
      state.done = true;
      announceActivity('done', '复习准备未能完成');
      render();
    }
    state.busy = false;
    renderQuick();
    flushPending();
  }

  function applySnapshot(snap) {
    state.snap = snap;
    state._lastActivityAt = Date.now() / 1000;
    state.done = !!snap.done;
    state.streamText = '';
    state.streamKind = '';
    renderStream();
    if (state.mode === 'probe') render();
  }

  // ── 迷你输入框：点发送 → 展开并发送（会话就绪后冲入） ──
  function miniSend() {
    var text = els.miniInput.value.trim();
    els.miniInput.value = '';
    if (text) state.pendingSend = text;
    open(true);
    flushPending();
  }
  function flushPending() {
    if (!state.pendingSend || state.busy) return;
    if (state.mode === 'learn') {
      if (!state.file) return;
      var lt = state.pendingSend;
      state.pendingSend = '';
      learnSend(lt);
      return;
    }
    if (!state.snap || state.done) return;
    var t = state.pendingSend;
    state.pendingSend = '';
    sendAnswer(t);
  }

  async function submit() {
    if (state.busy) return;
    closeSuggestions();
    var text = els.input.value.trim();
    if (!text) return;
    if (state.mode === 'learn') {
      els.input.value = '';
      await learnSend(text);
      return;
    }
    if (state.done) return;
    els.input.value = '';
    await sendAnswer(text);
  }

  async function sendAnswer(text) {
    state.busy = true;
    state._reqMode = 'probe';
    announceActivity('thinking', '正在检索原文并判断回答…');
    // 用户消息立即上屏（乐观渲染）；AI 回复随后流式进入，二者先后出现
    if (state.snap && state.snap.messages) {
      state.snap.messages.push({ role: 'learner', text: text, kind: 'text' });
      render();
    }
    els.typing.classList.add('on');
    scrollMessagesToBottom();
    renderQuick();
    try {
      var snap = await api('/answer', { session_id: state.sessionId, text: text }, onDelta);
      applySnapshot(snap);
      announceActivity('done', '已完成本轮反馈');
    } catch (e) {
      state.streamText = '';
      renderStream();
      announceActivity('done', '提交失败，请重试');
      toast('提交失败：' + e.message);
    }
    els.typing.classList.remove('on');
    state.busy = false;
    renderQuick();
    els.input.focus();
  }

  async function skipUnit() {
    if (state.busy || state.done) return;
    state.busy = true;
    state._reqMode = 'probe';
    els.typing.classList.add('on');
    scrollMessagesToBottom();
    try { applySnapshot(await api('/skip', { session_id: state.sessionId }, onDelta)); }
    catch (e) { state.streamText = ''; renderStream(); toast('操作失败：' + e.message); }
    els.typing.classList.remove('on');
    state.busy = false;
    renderQuick();
  }

  async function endRound() {
    if (state.busy || state.done) return;
    state.busy = true;
    state._reqMode = 'probe';
    els.typing.classList.add('on');
    scrollMessagesToBottom();
    try { applySnapshot(await api('/end', { session_id: state.sessionId }, onDelta)); }
    catch (e) { state.streamText = ''; renderStream(); toast('操作失败：' + e.message); }
    els.typing.classList.remove('on');
    state.busy = false;
    renderQuick();
  }

  // ── 学习模式（自由问答）：与追问模式的会话相互独立，互不干扰 ──
  function ensureLearnIntro() {
    if (state.learnMsgs.length) return;
    var tip = state.chapter
      ? '想弄懂「**' + state.chapter + '**」的哪一块？直接问我——我只依据这本资料回答，出处会在末尾自动附上。'
      : '想问哪一块？直接问我——我只依据这本资料回答，出处会在末尾自动附上。';
    state.learnMsgs.push({ role: 'guide', text: tip, kind: 'question' });
  }

  function renderLearn() {
    if (!els.msgs) return;
    var scrollState = captureMessageScroll();
    var msgList = state.learnMsgs;
    els.sub.textContent = state.chapter || '自由提问';
    els.meta.innerHTML = '';
    var prevCount = state._learnCount;
    var firstPaint = typeof prevCount !== 'number' || msgList.length < prevCount;
    var animateFrom = firstPaint ? 0 : prevCount;
    state._learnCount = msgList.length;
    els.msgs.innerHTML = msgList.map(function (m, i) { return renderMsg(m, i); }).join('');
    if (msgList.length > animateFrom) {
      var nodes = els.msgs.querySelectorAll('.rw-msg');
      for (var ni = animateFrom; ni < nodes.length; ni++) {
        nodes[ni].classList.add('rw-in');
        if (firstPaint) nodes[ni].style.animationDelay = (ni * 0.05) + 's';
      }
    }
    els.msgs.appendChild(els.typing);
    renderStream();
    scheduleMath(els.msgs);
    restoreMessageScroll(scrollState);
    els.input.disabled = false;
    els.send.disabled = state.busy;
    els.input.placeholder = composerPlaceholder();
    emitConversationState();
  }

  async function learnSend(text) {
    if (state.busy || !state.file) return;
    state.busy = true;
    state._reqMode = 'learn';
    announceActivity('thinking', state.completionMode ? '正在检索资料并准备知识补全…' : '正在检索原文并组织回答…');
    state.learnMsgs.push({ role: 'learner', text: text, kind: 'text' });
    renderLearn();
    els.typing.classList.add('on');
    renderQuick();
    try {
      var history = state.learnMsgs.slice(0, -1).slice(-16).map(function (m) {
        return { role: m.role === 'learner' ? 'learner' : 'guide', text: m.text };
      });
      var snap = await api('/learn', {
        file: state.file, chapter: state.chapter || '', question: text, history: history,
        completion_mode: !!state.completionMode,
        reply_style: state.prefs.replyStyle,
        thinking_effort: state.prefs.thinkingEffort,
        citation_style: state.prefs.citationStyle,
        language: state.prefs.language,
        custom_instruction: state.prefs.customInstruction
      }, onDelta);
      state.streamText = '';
      var reply = (snap && snap.reply) || '（没有返回内容，稍后再试）';
      var src = (snap && snap.sources) || [];
      var shortSrc = [];
      if (src.length) {
        // 出处只展示路径末两级（完整路径太长），同一条只保留一次
        var seenSrc = {};
        src.forEach(function (sp) {
          var seg = String(sp).split(' › ');
          var s2 = seg.slice(-2).join(' › ');
          if (s2 && !seenSrc[s2]) { seenSrc[s2] = 1; shortSrc.push(s2); }
        });
      }
      var sourceLimit = state.prefs.citationStyle === 'minimal' ? 1 : (state.prefs.citationStyle === 'strong' ? 4 : 3);
      state.learnMsgs.push({ role: 'guide', text: reply, kind: 'answer', sources: shortSrc.slice(0, sourceLimit),
        supplemental: !!(snap && snap.completion_mode) });
      announceActivity('done', state.completionMode ? '已生成含 AI 补充的回答' : '已生成带出处的回答');
    } catch (e) {
      state.streamText = '';
      announceActivity('done', '回答失败，请稍后重试');
      state.learnMsgs.push({ role: 'system', text: '回答失败：' + ((e && e.message) || e), kind: 'verdict' });
    }
    els.typing.classList.remove('on');
    state.busy = false;
    if (state.mode === 'learn') renderLearn();
    renderQuick();
    if (state.mode === 'learn') els.input.focus();
  }

  // ── 渲染 ──
  function render() {
    if (state.resumePrompt) return;
    if (state.chooser) { renderChooser(); return; }
    var snap = state.snap;
    if (!snap) return;
    var scrollState = captureMessageScroll();
    var cur = snap.current;
    els.sub.textContent = cur ? cur.title : '';
    // 对话里不展示回合/提示/层级等进度记账——只有自然对话
    els.meta.innerHTML = '';

    var msgList = snap.messages || [];
    var prevCount = state._renderedMsgCount;
    var firstPaint = typeof prevCount !== 'number' || msgList.length < prevCount;   // 少于上次 = 新会话
    var animateFrom = firstPaint ? 0 : prevCount;
    state._renderedMsgCount = msgList.length;
    els.msgs.innerHTML = msgList.map(function (m, i) { return renderMsg(m, i); }).join('');
    // 入场动画：首次打开全部错峰浮入；之后只给新增消息播
    if (msgList.length > animateFrom) {
      var nodes = els.msgs.querySelectorAll('.rw-msg');
      for (var ni = animateFrom; ni < nodes.length; ni++) {
        nodes[ni].classList.add('rw-in');
        if (firstPaint) nodes[ni].style.animationDelay = (ni * 0.05) + 's';
      }
    }
    els.msgs.appendChild(els.typing);   // 思考行跟随消息流，出现在气泡应出现的位置
    renderStream();   // 若正在流式，把临时气泡插回消息流末尾（思考行之前）
    scheduleMath(els.msgs);
    restoreMessageScroll(scrollState);
    els.input.disabled = state.done;
    els.send.disabled = state.done;
    if (!state.done) els.input.placeholder = composerPlaceholder();
    renderQuick();
    emitConversationState();
  }

  function revealMessageKey(m, index) {
    return (state.sessionId || state.file || 'session') + ':' + index + ':' + String((m && m.ts) || '');
  }

  function revealDomId(key) {
    return 'rwReveal_' + String(key).replace(/[^0-9A-Za-z_-]/g, '_');
  }

  function revealIcon(name) {
    return (global.KT && global.KT.iconHtml) ? global.KT.iconHtml(name) : '';
  }

  function setRevealVisibility(wrap, hidden) {
    if (!wrap) return;
    var key = wrap.getAttribute('data-reveal-key') || '';
    if (key) state.hiddenRevealKeys[key] = !!hidden;
    wrap.classList.toggle('is-reveal-hidden', !!hidden);
    var content = wrap.querySelector('.rw-reveal-content');
    var note = wrap.querySelector('.rw-reveal-note');
    var btn = wrap.querySelector('.rw-reveal-toggle');
    if (content) content.hidden = !!hidden;
    if (note) note.hidden = !hidden;
    if (btn) {
      var label = hidden ? '显示考点内容' : '隐藏考点内容';
      var icon = btn.querySelector('.rw-reveal-icon');
      btn.setAttribute('aria-expanded', hidden ? 'false' : 'true');
      btn.setAttribute('aria-label', label);
      btn.title = label;
      if (icon) icon.innerHTML = revealIcon(hidden ? 'eyeOff' : 'eye');
    }
  }

  function toggleRevealMessage(btn) {
    var wrap = btn && btn.closest ? btn.closest('.rw-msg.reveal') : null;
    if (!wrap) return;
    var hidden = !wrap.classList.contains('is-reveal-hidden');
    setRevealVisibility(wrap, hidden);
  }

  function renderMsg(m, index) {
    var text = md(m.text || '');
    var sourceHtml = '';
    if (m.supplemental || (m.sources && m.sources.length)) {
      sourceHtml = '<div class="rw-answer-meta">';
      if (m.supplemental) sourceHtml += '<span class="rw-supplement-badge">含 AI 补充</span>';
      if (m.sources && m.sources.length) {
        sourceHtml += '<div class="rw-sources">' + m.sources.map(function (src) {
          return '<span class="rw-source-pill">' + (global.KT && global.KT.iconHtml ? global.KT.iconHtml('file') : '') + esc(src) + '</span>';
        }).join('') + '</div>';
      }
      sourceHtml += '</div>';
    }
    if (m.role === 'learner') {
      // DOM 序 [气泡, 头像] + justify-end → 头像贴右缘
      return '<div class="rw-msg learner"><div class="rw-b">' + text + '</div>' + avatarOf('learner') + '</div>';
    }
    if (m.kind === 'reveal') {
      var revealKey = revealMessageKey(m, typeof index === 'number' ? index : 0);
      var revealId = revealDomId(revealKey);
      var revealHidden = !!state.hiddenRevealKeys[revealKey];
      var revealLabel = revealHidden ? '显示考点内容' : '隐藏考点内容';
      return '<div class="rw-msg reveal' + (revealHidden ? ' is-reveal-hidden' : '') + '" data-reveal-key="' + esc(revealKey) + '">'
        + avatarOf('guide')
        + '<div class="rw-b rw-reveal-box">'
        + '<button class="rw-reveal-toggle" type="button" data-reveal-toggle aria-controls="' + esc(revealId)
        + '" aria-expanded="' + (revealHidden ? 'false' : 'true') + '" aria-label="' + revealLabel + '" title="' + revealLabel + '">'
        + '<span class="rw-reveal-icon">' + revealIcon(revealHidden ? 'eyeOff' : 'eye') + '</span></button>'
        + '<div class="rw-reveal-content" id="' + esc(revealId) + '"' + (revealHidden ? ' hidden' : '') + '>' + text + sourceHtml + '</div>'
        + '<div class="rw-reveal-note"' + (revealHidden ? '' : ' hidden') + '>'
        + '<span>考点内容已隐藏</span></div>'
        + '</div></div>';
    }
    var cls = 'guide';
    if (m.kind === 'report') cls = 'report';
    else if (m.kind === 'verdict' || m.role === 'system') cls = 'system';
    else if (m.kind === 'hint') cls = 'guide hint';
    // DOM 序 [头像, 气泡] + justify-start → 头像贴左缘
    return '<div class="rw-msg ' + cls + '">' + avatarOf('guide') + '<div class="rw-b">' + text + sourceHtml + '</div></div>';
  }

  // 快捷选项（AI 给出的选项按钮；用户无需自己组织问题）
  function renderQuick() {
    if (!els.quick) return;
    if (state.resumePrompt) { els.quick.innerHTML = ''; return; }
    if (state.busy) { els.quick.innerHTML = ''; return; }
    if (state.mode === 'learn') {
      if (state.learnMsgs.length <= 1) {
        var lchips = [];
        if (state.chapter) {
          lchips.push(chip('这章讲什么', '', function () { learnSend('这章主要讲了什么？用要点帮我梳理一下。'); }));
          lchips.push(chip('重点 / 难点', '', function () { learnSend('这章的重点和难点分别是什么？'); }));
        } else {
          lchips.push(chip('这本书讲什么', '', function () { learnSend('这本书主要讲了什么？'); }));
        }
        els.quick.innerHTML = lchips.join('');
      } else {
        els.quick.innerHTML = '';
      }
      return;
    }
    var btns = [];
    if (state.chooser) { els.quick.innerHTML = ''; return; }
    if (!state.snap) {
      btns.push(chip('选择复习方向', 'primary', function () { showChooser(); }));
      return els.quick.innerHTML = btns.join('');
    }
    if (state.done) {
      btns.push(chip('重练' + (state.chapter ? '本章' : '本书'), 'primary', restart));
      btns.push(chip('换方向', '', function () { showChooser(); }));
      return els.quick.innerHTML = btns.join('');
    }
    // 页面上下文已切到别的章节：给一个可点的切换入口（由用户决定，不自动重置）
    if (state._pendingChapter && state._pendingChapter !== state.sessionChapter) {
      var pc = state._pendingChapter;
      btns.push(chip('切换「' + shortTitle(pc) + '」', 'primary', function () {
        state._pendingChapter = '';
        startChapter(pc);
      }));
    }
    btns.push(chip('想不起来', '', function () { sendAnswer('想不起来了'); }));
    btns.push(chip('给点提示', '', function () { sendAnswer('提示我一下'); }));
    btns.push(chip('跳过这个点', '', skipUnit));
    btns.push(chip('结束本轮', '', endRound));
    els.quick.innerHTML = btns.join('');
  }

  function chip(label, cls, fn) {
    var id = 'rwq_' + Math.random().toString(36).slice(2, 9);
    if (!global.__rwQuickHandlers) global.__rwQuickHandlers = {};
    global.__rwQuickHandlers[id] = fn;
    return '<button class="rw-qbtn' + (cls ? ' ' + cls : '') + '" onclick="__rwQuickHandlers[\'' + id + '\']()">' + esc(label) + '</button>';
  }

  // ── 旧会话未结束：打开先问「接着上次 / 换方向」 ──
  function _lastActivityTs() {
    // 以服务端消息时间为准（最后一条消息就是你上次动作的时间）；
    // 消息缺时间戳时才退回本地记录的最近一次快照时间。
    var msgs = (state.snap && state.snap.messages) || [];
    var last = msgs[msgs.length - 1];
    return (last && last.ts) || state._lastActivityAt || 0;
  }

  function isStaleSession() {
    if (!state.snap || state.snap.done || state.busy) return false;
    if (!state.sessionId || state._resumeOk === state.sessionId) return false;
    var ts = _lastActivityTs();
    if (!ts) return false;   // 没有时间信息（旧数据/测试桩）→ 不打扰
    return (Date.now() / 1000 - ts) > RESUME_PROMPT_AFTER;
  }

  function _agoText(sec) {
    if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + ' 分钟前';
    if (sec < 86400) return Math.round(sec / 3600) + ' 小时前';
    return Math.round(sec / 86400) + ' 天前';
  }

  function showResumePrompt() {
    if (!els.msgs) return;
    state.resumePrompt = true;
    var cur = state.snap.current || {};
    var where = cur.title || state.sessionChapter || state.chapter || '上次的考点';
    var ago = _agoText(Math.max(0, Date.now() / 1000 - _lastActivityTs()));
    els.sub.textContent = '';
    els.meta.innerHTML = '';
    var html = '<div class="rw-choose">'
      + '<div class="rw-choose-title">接着上次，还是换方向？</div>'
      + '<div class="rw-choose-sub">上次进行到「' + esc(where) + '」（' + ago + '），这一轮还没结束。</div>'
      + chooseBtn('<span class="t">接着上次</span><span class="s">从「' + esc(shortTitle(where)) + '」继续</span>',
          'primary', resumeContinue)
      + chooseBtn('<span class="t">换方向</span><span class="s">重新选章节 / 全书混合</span>',
          '', resumeChangeDirection)
      + '</div>';
    els.msgs.innerHTML = html;
    resetMessagesToTop();
    els.input.disabled = true;
    els.send.disabled = true;
    els.input.placeholder = '先选「接着上次」或「换方向」…';
    els.quick.innerHTML = '';
    state._renderedMsgCount = undefined;
  }

  function resumeContinue() {
    state.resumePrompt = false;
    state._resumeOk = state.sessionId;
    render();
    renderQuick();
    if (els.input) els.input.focus();
  }

  function resumeChangeDirection() {
    state.resumePrompt = false;
    state.snap = null;
    state.sessionId = '';
    state.done = false;
    state._pendingChapter = '';
    showChooser();
  }

  // ── 复习方向选择：先按导图内容列出章节方向，选定后开始对话 ──
  function chooseBtn(html, cls, fn) {
    var id = 'rwc_' + Math.random().toString(36).slice(2, 9);
    if (!global.__rwChooseHandlers) global.__rwChooseHandlers = {};
    global.__rwChooseHandlers[id] = fn;
    return '<button class="rw-copt' + (cls ? ' ' + cls : '') + '" onclick="__rwChooseHandlers[\'' + id + '\']()">'
      + html + '</button>';
  }

  function normT(t) { return String(t || '').replace(/\s+/g, '').trim(); }

  function isCurrentChapter(name) {
    return !!state.chapter && normT(state.chapter) === normT(name);
  }

  async function showChooser() {
    state.chooser = true;
    state.resumePrompt = false;
    state.chooseErr = '';
    renderChooser();
    renderQuick();
    if (!state.file) { state.chooseErr = '缺少资料（file）'; renderChooser(); return; }
    try {
      var res = await fetch('/api/review/units?file=' + encodeURIComponent(state.file) + '&max_units=300');
      var data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
      state.chooseData = data;
    } catch (e) {
      state.chooseData = null;
      state.chooseErr = (e && e.message) || String(e);
    }
    renderChooser();
    renderQuick();
  }

  function renderChooser() {
    if (!els.msgs) return;
    els.sub.textContent = '';
    els.meta.innerHTML = '';
    els.input.disabled = true;
    els.send.disabled = true;
    els.input.placeholder = '先选一个复习方向…';
    var html = '<div class="rw-choose">'
      + '<div class="rw-choose-title">选择复习方向</div>'
      + '<div class="rw-choose-sub">按章节挑一个方向，或全书混合复习——选定后开始对话</div>';
    if (state.chooseErr) {
      html += '<div class="rw-choose-err">方向列表加载失败：' + esc(state.chooseErr) + '</div>'
        + chooseBtn('<span class="t">重新加载</span>', '', function () { showChooser(); })
        + chooseBtn('<span class="t">全书混合复习</span><span class="s">直接开始（不指定章节）</span>', 'primary',
          function () { chooseDirection({ chapter: '', scope: 'all' }); });
    } else if (!state.chooseData) {
      html += '<div class="rw-choose-load">' + SVG_SPIN + '<span>正在读取导图…</span></div>';
    } else {
      var data = state.chooseData;
      var total = data.total_candidates || 0;
      html += chooseBtn('<span class="t">全书混合复习</span><span class="s">跨章节随机 · 共 '
        + total + ' 个复习单元' + (data.truncated ? '（列表已截断）' : '') + '</span>', 'primary',
        function () { chooseDirection({ chapter: '', scope: 'all' }); });
      if (data.retest_count > 0 || data.weak_count > 0) {
        var pool = (data.retest_count || 0) + (data.weak_count || 0);
        var rsub = pool + ' 个待复练' + (data.due_count ? ' · 今日到期 ' + data.due_count : '');
        html += chooseBtn('<span class="t">薄弱点重练</span><span class="s">' + rsub + '</span>', '',
          function () { chooseDirection({ chapter: '', scope: 'retest' }); });
      }
      var hf = {};
      (data.units || []).forEach(function (u) {
        var g = u.group || '未分组';
        if (u.high_freq) hf[g] = (hf[g] || 0) + 1;
      });
      (data.groups || []).forEach(function (g) {
        var sub = g.count + ' 个复习单元' + (hf[g.name] ? ' · 高频 ' + hf[g.name] : '')
          + (g.due ? ' · 待复练 ' + g.due : '');
        var badge = isCurrentChapter(g.name) ? '<span class="rw-copt-badge">当前</span>' : '';
        html += chooseBtn('<span class="t">' + esc(g.name) + badge + '</span><span class="s">' + esc(sub) + '</span>',
          '', (function (name) {
            return function () { chooseDirection({ chapter: name, scope: 'all' }); };
          })(g.name));
      });
    }
    html += '</div>';
    els.msgs.innerHTML = html;
    resetMessagesToTop();
    state._renderedMsgCount = undefined;   // 进入对话后按"首屏"播消息入场
  }

  function chooseDirection(opt) {
    opt = opt || {};
    state.chooser = false;
    state.chooseErr = '';
    state.scope = opt.scope || 'all';
    state.chapter = typeof opt.chapter === 'string' ? opt.chapter : '';
    state._pendingChapter = '';
    state.sessionId = '';
    state.snap = null;
    state.done = false;
    state.hiddenRevealKeys = {};
    // 立即切到"对话准备"视图：清掉方向列表，把思考行挂回消息流
    if (els.msgs) {
      els.msgs.innerHTML = '';
      els.msgs.appendChild(els.typing);
    }
    startSession();
  }

  async function restart() {
    state.chooser = false;
    state.sessionId = ''; state.snap = null; state.done = false;
    state.messagesPinned = true;
    state.scrollJumping = false;
    state.hiddenRevealKeys = {};
    await startSession();
  }

  // ── 开关（迷你框 ⇄ 完整面板，向上展开/收回） ──
  function open(silent) {
    build();
    if (global.KT && KT.Panels) KT.Panels.closeOthers('review');
    state.open = true;
    els.panel.classList.add('open');
    els.mini.classList.add('hidden');
    updateModeUI();
    if (state.mode === 'learn') {
      ensureLearnIntro();
      renderLearn();
      renderQuick();
    } else if (!state.snap && !state.busy) {
      if (state._directStart) {
        // 显式指定了方向（信息页章节卡 / 会话中切换）：直接开始，不再问方向
        state._directStart = false;
        state.chooser = false;
        startSession();
      } else if (state.chooser && state.chooseData) {
        render();            // 已有方向列表：直接展示
      } else {
        showChooser();       // 上来先给"复习方向"选项列表
      }
    } else {
      if (isStaleSession()) showResumePrompt();
      else render();
    }
    if (!silent) setTimeout(function () { els.input.focus(); }, 60);
    window.requestAnimationFrame(function () {
      positionScrollBottomButton();
      updateScrollBottomButton();
    });
  }
  function close() {
    state.open = false;
    closeSuggestions();
    if (els.panel) els.panel.classList.remove('open');
    if (els.mini) els.mini.classList.toggle('hidden', !state.file);
  }
  function toggle() { state.open ? close() : open(); }



  function setComposerMeta(model, effort) {
    if (els.chinModel && model) els.chinModel.textContent = model;
    if (els.chinEffort) els.chinEffort.textContent = effort || '标准';
  }

  function ask(text) {
    text = String(text || '').trim();
    if (!state.file || !text || state.busy) return;
    state.mode = 'learn';
    updateModeUI();
    state.resumePrompt = false;
    state.chooser = false;
    open(true);
    learnSend(text);
  }

  // ── 对外 ──
  function mount(opts) {
    opts = opts || {};
    if (opts.container) state.container = opts.container;
    var nextFile = opts.file || state.file;
    var fileChanged = !!opts.file && opts.file !== state.file;
    state.file = nextFile;
    state.chapter = opts.chapter || '';
    state.title = opts.title || '';
    if (fileChanged) {
      state.sessionId = '';
      state.snap = null;
      state.done = false;
      state.busy = false;
      state.chooser = false;
      state.chooseData = null;
      state.chooseErr = '';
      state._renderedMsgCount = undefined;
      state.learnMsgs = [];
      state._learnCount = undefined;
      state.resumePrompt = false;
      state.mentions = [];
      state.completionMode = false;
      state.messagesPinned = true;
      state.scrollJumping = false;
      state.hiddenRevealKeys = {};
      closeSuggestions();
    }
    if (!state.mounted) state.mode = loadMode();
    build();
    var titleEl = els.panel && els.panel.querySelector ? els.panel.querySelector('.rw-title-text') : null;
    if (titleEl) {
      titleEl.innerHTML = (global.KT && global.KT.iconHtml ? global.KT.iconHtml('target') : '')
        + ' ' + esc(state.title || '对话复习');
    }
    // 有文件才显示迷你输入框；嵌入式对话不需要迷你框
    els.mini.classList.toggle('hidden', !!state.container || !state.file || state.open);
    emitConversationState();
  }

  function setContext(opts) {
    opts = opts || {};
    if (typeof opts.chapter === 'string') {
      var changed = state.chapter !== opts.chapter;
      state.chapter = opts.chapter;
      if (changed && state.mode === 'learn') {
        // 学习模式跟随当前章节：换章后从新语境重新开始
        state.learnMsgs = [];
        state._learnCount = undefined;
        if (state.open) { ensureLearnIntro(); renderLearn(); renderQuick(); }
      } else if (changed && state.snap && !state.done) {
        // 正在复习别的章节：给一个可点的切换入口，
        // 由用户点按钮再切换，避免打断正在进行的这一轮
        state._pendingChapter = opts.chapter;
        if (changed && state.chapter === '') state._pendingChapter = '';
      } else if (!state.snap || state.done) {
        state._pendingChapter = '';
      }
    }
    if (state.snap && state.done && opts.autoHint) renderQuick();
    else if (state.snap && state._pendingChapter) renderQuick();
  }

  function startChapter(title, scope) {
    // 显式「复习这一章」入口走追问模式（仅本次，不改用户已存的模式偏好）
    state.mode = 'probe';
    updateModeUI();
    state.chapter = title || '';
    state.scope = scope || 'all';
    state._pendingChapter = '';
    state.sessionId = ''; state.snap = null; state.done = false;
    state.hiddenRevealKeys = {};
    state.chooser = false;
    state._directStart = true;
    open(true);
  }

  global.ReviewWidget = {
    mount: mount, open: open, close: close, toggle: toggle,
    setContext: setContext, startChapter: startChapter, ask: ask, setComposerMeta: setComposerMeta, setMentions: setMentions,
    setPreferences: setPreferences, getPreferences: function () { return Object.assign({}, state.prefs); },
    setMode: setMode, getMode: function () { return state.mode; }, restart: restart,
    hasConversationContent: hasConversationContent,
    renderMarkdown: md
  };
})(window);
