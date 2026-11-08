/* ════════════════════════════════════════════════════════════════
   AnythingMindmap — map exports
   Handout / Markdown / Anki / PNG / data package. Kept separate from
   the map controller so the export contracts stay easy to test.
   ════════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  function map() { return global.KTMap; }
  function tree() { return map() && map().getDisplayTree(); }
  function selectedId() { return map() && map().getSelectedId(); }
  function selectedTree() {
    var id = selectedId();
    return id ? map().getExportTree(id) : tree();
  }
  function key() {
    var st = map() && map().getState();
    return st && st.key || '';
  }
  function titleOf(node) { return node && (node.title || node.name) || ''; }
  function pageBookTitle() {
    var t = tree();
    return (t && (t.title || t.name)) || '知识树';
  }
  function currentExportTitle() {
    var id = selectedId();
    if (id) return map().getNode(id) && (map().getNode(id).title || map().getNode(id).name) || pageBookTitle();
    return pageBookTitle();
  }
  function getModules() {
    var t = tree();
    return t && global.KT && KT.bookModules ? KT.bookModules(t) : [];
  }
  function sanitizeFileName(name) {
    return (name || '知识树').replace(/[\\\/:*?"<>|\r\n\t]+/g, '_').replace(/\s+/g, ' ').trim().slice(0, 60) || '知识树';
  }
  function toast(message) {
    if (global.KT && KT.showToast) KT.showToast(message);
  }

  // ── PNG ──────────────────────────────────────────────────────
  function exportPNG() {
    if (!map()) return;
    map().exportPNG().catch(function (err) {
      console.error(err);
      alert('导出 PNG 失败：' + (err && err.message || err));
    });
  }

  // ── handout ──────────────────────────────────────────────────
  function _handoutNodeHTML(node, depth) {
    var h = Math.min(2 + depth, 5);
    var html = '<div class="nb">';
    html += '<h' + h + ' class="nh">' + KT.escapeHtml(titleOf(node)) + '</h' + h + '>';
    if (node.summary) {
      html += KT.formatTextBlocks(node.summary, [], 'ns');
    }
    if (node.guide) {
      html += '<div class="nguide">' + KT.formatTextBlocks(node.guide, [], 'ns') + '</div>';
    }
    if (node.keywords && node.keywords.length) {
      html += '<div class="nk">' + node.keywords.slice(0, 12).map(function (k) {
        var t = (typeof k === 'object' && k && k.term) ? k.term : k;
        return '<span class="kw">' + KT.escapeHtml(String(t)) + '</span>';
      }).join('') + '</div>';
    }
    if (node.exam_points && node.exam_points.length) {
      html += '<div class="ne">';
      node.exam_points.forEach(function (ep) {
        var freq = ep.frequency || '';
        html += '<div class="ep">'
          + '<span class="etag' + (freq ? ' ef-' + KT.escapeHtml(freq) : '') + '">' + KT.escapeHtml(freq || '考点') + '</span>'
          + (ep.type ? '<span class="etype">' + KT.escapeHtml(ep.type) + '</span>' : '')
          + '<span class="ept">' + KT.escapeHtml(ep.point || '') + '</span></div>';
      });
      html += '</div>';
    }
    if (node.captions && node.captions.length) {
      node.captions.forEach(function (cap) {
        html += '<div class="cap"><b>' + KT.escapeHtml(cap.label || '') + '</b> ' + KT.escapeHtml(cap.text || '') + '</div>';
      });
    }
    if (node.content) {
      html += KT.formatContent(node.content, [], 0);
    }
    if (node.tables && node.tables.length) {
      node.tables.forEach(function (tbl) {
        html += KT.renderTableBlock(tbl, []);
      });
    }
    if (node.mermaid) html += '<div class="mm">［此节点含流程图，请在软件内查看］</div>';
    html += '</div>';
    (node.children || []).forEach(function (c) { html += _handoutNodeHTML(c, depth + 1); });
    return html;
  }

  function buildHandoutHTML(rootNode, title, forceWhole) {
    var modules = getModules();
    var order = KT.computeStudyOrder(modules);
    var bookTitle = pageBookTitle();
    var isModule = modules.some(function (m) { return titleOf(m) === title; });
    var wholeBook = !!forceWhole || !selectedId();
    var stats = KT.moduleStats(rootNode);
    var dateStr = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });

    var css = ''
      + '@page { size: A4; margin: 18mm 16mm 20mm; }'
      + '* { box-sizing: border-box; }'
      + "body { font-family: 'Noto Serif SC','Songti SC',SimSun,Georgia,serif; color:#2c1810; background:#fff; margin:0; font-size:12.5pt; line-height:1.75; }"
      + '.print-bar { position:sticky; top:0; background:#faf6ef; border-bottom:1px solid #e8dcc8; padding:10px 16px; display:flex; justify-content:space-between; align-items:center; gap:12px; font-size:13px; }'
      + '.print-bar button { background:#c07a2e; color:#fff; border:none; border-radius:20px; padding:8px 20px; font-size:13px; cursor:pointer; }'
      + '.wrap { max-width: 180mm; margin: 0 auto; padding: 16px 8px 40px; }'
      + '.hd { border-bottom:2px solid #c07a2e; padding-bottom:10px; margin-bottom:16px; }'
      + '.hd-book { font-size:10pt; letter-spacing:1px; color:#8a6a3a; }'
      + '.hd-title { font-size:20pt; margin:6px 0 4px; font-weight:700; line-height:1.3; }'
      + '.hd-meta { font-size:9.5pt; color:#8a7a68; }'
      + '.guide { background:#faf6ef; border:1px solid #e8dcc8; border-left:4px solid #c07a2e; border-radius:6px; padding:10px 14px; margin:0 0 16px; break-inside:avoid; }'
      + '.guide-h { font-weight:700; color:#a8681e; margin-bottom:4px; font-size:11pt; }'
      + '.guide p { margin:6px 0; }'
      + '.prereq { font-size:10.5pt; color:#5a4a38; background:#f3efe7; border-radius:4px; padding:6px 12px; margin:0 0 14px; }'
      + '.path { margin:0 0 18px; }'
      + '.path ol { margin:6px 0 0 22px; padding:0; }'
      + '.path li { margin:2px 0; }'
      + '.nb { break-inside:avoid; margin:0 0 10px; }'
      + '.nh { margin:14px 0 6px; line-height:1.4; break-after:avoid; }'
      + 'h2.nh { font-size:15pt; border-left:4px solid #c07a2e; padding-left:8px; color:#8a5a1e; }'
      + 'h3.nh { font-size:13pt; color:#1e5f52; }'
      + 'h4.nh { font-size:12pt; color:#3e5a86; }'
      + 'h5.nh { font-size:11.5pt; color:#6a4f86; }'
      + '.ns { margin:4px 0; color:#4a3a2c; }'
      + '.nguide { margin:6px 0 10px; padding:8px 12px; background:#faf6ef; border-left:3px solid #c07a2e; border-radius:4px; color:#5a4a38; font-size:10.5pt; }'
      + '.nk { margin:4px 0; }'
      + '.kw { display:inline-block; background:#f3efe7; border:1px solid #e0d5c2; color:#8a5a1e; border-radius:10px; padding:1px 8px; font-size:9pt; margin:2px 3px 2px 0; }'
      + '.ne { margin:6px 0; }'
      + '.ep { margin:3px 0; padding:3px 8px; border-left:3px solid #c07a2e; background:#fdf9f3; break-inside:avoid; }'
      + '.etag { display:inline-block; font-size:8.5pt; font-weight:700; padding:0 6px; border-radius:3px; margin-right:6px; background:#eee; color:#666; }'
      + '.ef-高频 { background:#e2f2e6; color:#2e7a44; }'
      + '.ef-中频 { background:#faeed8; color:#a8681e; }'
      + '.etype { color:#8a7a68; font-size:9pt; margin-right:6px; }'
      + '.nc, .content-para { margin:4px 0; text-indent:2em; }'
      + '.block-formula { margin:8px 0; text-align:center; break-inside:avoid; }'
      + '.inline-formula { white-space:nowrap; }'
      + '.katex-display { margin:8px 0; overflow-x:auto; overflow-y:hidden; }'
      + '.cap { font-size:10pt; color:#5a4a38; margin:2px 0; }'
      + '.mm { font-size:9.5pt; color:#8a7a68; font-style:italic; }'
      + '.table-wrapper { margin:8px 0; break-inside:avoid; overflow-x:auto; }'
      + '.md-table { width:100%; border-collapse:collapse; font-size:10pt; }'
      + '.md-table th,.md-table td { border:1px solid #d8ccb8; padding:4px 8px; text-align:left; }'
      + '.md-table th { background:#f5f0e8; }'
      + '.sum-hl { color:#a8681e; font-weight:600; }'
      + '.foot { margin-top:24px; padding-top:8px; border-top:1px solid #e0d5c2; font-size:9pt; color:#9a8a76; }'
      + '@media print { .print-bar { display:none; } .wrap { padding:0; max-width:none; } body { font-size:12pt; } }';

    var body = '<div class="print-bar"><span>打印讲义 · ' + KT.escapeHtml(title) + '</span>'
      + '<button onclick="window.print()">打印 / 另存 PDF</button></div><div class="wrap">';
    body += '<div class="hd">';
    body += '<div class="hd-book">' + KT.escapeHtml(bookTitle) + ' · 复习讲义</div>';
    body += '<div class="hd-title">' + KT.escapeHtml(title) + '</div>';
    body += '<div class="hd-meta">' + dateStr + ' · 共 ' + stats.nodes + ' 个知识点'
      + (stats.examCount ? ' · ' + stats.examCount + ' 个考点标注' : '') + '</div>';
    body += '</div>';
    if (rootNode.fallback) body += '<div class="prereq">本章部分内容为离线兜底生成，可在软件内重新增强。</div>';
    if (rootNode.guide) {
      body += '<section class="guide"><div class="guide-h">导学</div>';
      String(rootNode.guide).split(/\n+/).forEach(function (p) {
        if (p.trim()) body += '<p>' + KT.escapeHtml(p.trim()) + '</p>';
      });
      body += '</section>';
    }
    if (isModule && rootNode.prerequisites && rootNode.prerequisites.length) {
      body += '<div class="prereq">建议先修：' + rootNode.prerequisites.map(function (p) { return KT.escapeHtml(p); }).join('、') + '</div>';
    }
    if (wholeBook && modules.length > 1) {
      body += '<section class="path"><div class="guide-h">推荐学习路径</div><ol>';
      order.forEach(function (mi) { body += '<li>' + KT.escapeHtml(titleOf(modules[mi])) + '</li>'; });
      body += '</ol></section>';
    }
    (rootNode.children || []).forEach(function (c) { body += _handoutNodeHTML(c, 0); });
    body += '<div class="foot">' + KT.escapeHtml(bookTitle) + ' · ' + KT.escapeHtml(title) + ' · 由 AnythingMindmap 生成</div>';
    body += '</div>';

    return '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
      + '<title>' + KT.escapeHtml(title) + ' 讲义</title>'
      + '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">'
      + '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"><\/script>'
      + '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"><\/script>'
      + '<style>' + css + '</style></head><body>'
      + body
      + '<script>document.addEventListener("DOMContentLoaded",function(){try{if(typeof renderMathInElement==="function"){renderMathInElement(document.body,{delimiters:[{left:"$$",right:"$$",display:true},{left:"$",right:"$",display:false}],throwOnError:false});}}catch(e){}document.documentElement.setAttribute("data-kt-math-ready","1");});<\/script>'
      + '</body></html>';
  }

  function openPrintWindow(html) {
    var w = window.open('', '_blank');
    if (!w) { alert('浏览器阻止了弹出窗口，请允许弹窗后重试'); return; }
    w.document.open();
    w.document.write(html);
    w.document.close();
    var printed = false, started = false;
    var doPrint = function () { if (printed) return; printed = true; try { w.focus(); w.print(); } catch (e) {} };
    var printWhenReady = function () {
      if (started) return;
      started = true;
      var tries = 0;
      var check = function () {
        tries++;
        var ready = false;
        try { ready = w.document.documentElement.getAttribute('data-kt-math-ready') === '1'; }
        catch (e) { ready = true; }
        if (!ready && tries < 40) { setTimeout(check, 100); return; }
        doPrint();
      };
      setTimeout(check, 80);
    };
    w.onload = printWhenReady;
    setTimeout(printWhenReady, 1200);
  }

  function exportModuleHandout() {
    var rootNode = selectedTree();
    var title = currentExportTitle();
    openPrintWindow(buildHandoutHTML(rootNode, title, false));
  }
  function exportWholeHandout() {
    openPrintWindow(buildHandoutHTML(tree(), pageBookTitle(), true));
  }

  // ── text downloads ───────────────────────────────────────────
  function _downloadText(filename, text, mime) {
    if (mime === 'text/csv' && text.charAt(0) !== '﻿') text = '﻿' + text;
    var url = '';
    try {
      url = URL.createObjectURL(new Blob([text], { type: mime + ';charset=utf-8' }));
    } catch (e) {
      url = 'data:' + mime + ';charset=utf-8,' + encodeURIComponent(text);
    }
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (url.indexOf('blob:') === 0) setTimeout(function () { try { URL.revokeObjectURL(url); } catch (e) {} }, 1200);
  }

  function _outlineAnnos(node, out) {
    if (node.summary) out.push('> 摘要：' + String(node.summary).replace(/\n+/g, ' '));
    if (node.keywords && node.keywords.length) {
      out.push('> 关键词：' + node.keywords.slice(0, 12).map(function (k) {
        return typeof k === 'object' && k && k.term ? k.term : k;
      }).join('、'));
    }
    (node.exam_points || []).forEach(function (ep) {
      var meta = [ep.frequency, ep.type].filter(Boolean).join(' · ');
      out.push('> 考点' + (meta ? '（' + meta + '）' : '') + '：' + (ep.point || ''));
    });
  }

  function buildOutlineMarkdown(rootNode, title) {
    var head = title || titleOf(rootNode) || '知识大纲';
    var out = ['# ' + head, ''];
    _outlineAnnos(rootNode, out);
    function walk(node, depth) {
      (node.children || []).forEach(function (c) {
        var t = titleOf(c);
        if (!t) return;
        if (depth === 1 && t === head) {
          _outlineAnnos(c, out);
          walk(c, depth);
          return;
        }
        var hashes = new Array(Math.min(depth + 1, 6) + 1).join('#');
        out.push('');
        out.push(hashes + ' ' + t);
        _outlineAnnos(c, out);
        walk(c, depth + 1);
      });
    }
    walk(rootNode, 1);
    out.push('');
    return out.join('\n');
  }

  function exportOutlineMarkdown() {
    var rootNode = selectedTree();
    var title = currentExportTitle();
    _downloadText(sanitizeFileName(title) + '_大纲.md', buildOutlineMarkdown(rootNode, title), 'text/markdown');
    toast('已导出 Markdown 大纲：' + title);
  }

  function exportDataPackage() {
    var resourceKey = key();
    if (!resourceKey) { toast('当前资料没有可导出的数据包'); return; }
    var link = document.createElement('a');
    link.href = '/api/package/' + encodeURIComponent(resourceKey);
    link.setAttribute('download', '');
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast('正在导出资料数据包…');
  }

  function _csvField(v) { return '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"'; }
  function _ankiTag(s) {
    return String(s || '').replace(/["\r\n\t]/g, '').replace(/\s+/g, '_').replace(/^:+|:+$/g, '');
  }

  function buildAnkiCSV(rootNode, bookTitle) {
    var rows = [['Front', 'Back', 'Tags'].map(_csvField).join(',')];
    var count = 0;
    function walk(node, path) {
      var title = titleOf(node);
      var myPath = path.concat(title ? [title] : []);
      var kids = (node.children || []).map(titleOf).filter(Boolean);
      var exam = node.exam_points || [];
      if (title && (exam.length || (kids.length >= 2 && kids.length <= 8))) {
        var parent = myPath.length > 1 ? myPath[myPath.length - 2] : (bookTitle || '');
        var front = '【' + parent + '】' + title + ' —— ' + (exam.length ? '考什么？' : '包括哪几部分？');
        var backs = [];
        if (kids.length >= 2 && kids.length <= 8) {
          backs.push(kids.map(function (k, i) { return (i + 1) + '. ' + k; }).join('\n'));
        }
        exam.forEach(function (ep) {
          var meta = [ep.frequency, ep.type].filter(Boolean).join(' · ');
          backs.push('考点' + (meta ? '（' + meta + '）' : '') + '：' + (ep.point || ''));
        });
        if (node.summary) backs.push('摘要：' + String(node.summary).replace(/\n+/g, ' '));
        var tag = '333' + (myPath[0] ? '::' + _ankiTag(myPath[0]) : '');
        rows.push([front, backs.join('\n\n'), tag].map(_csvField).join(','));
        count++;
      }
      (node.children || []).forEach(function (c) { walk(c, myPath); });
    }
    walk(rootNode, []);
    return { csv: rows.join('\r\n') + '\r\n', count: count };
  }

  function exportAnkiCSV() {
    var built = buildAnkiCSV(tree(), pageBookTitle());
    if (!built.count) { toast('没有可导出的复习卡（先完成 AI 增强或检查结构）'); return; }
    _downloadText(sanitizeFileName(pageBookTitle()) + '_复习卡.csv', built.csv, 'text/csv');
    toast('已导出 ' + built.count + ' 张复习卡（Anki 可导入）');
  }

  global.KTMapExports = {
    exportPNG: exportPNG,
    exportModuleHandout: exportModuleHandout,
    exportWholeHandout: exportWholeHandout,
    exportOutlineMarkdown: exportOutlineMarkdown,
    exportAnkiCSV: exportAnkiCSV,
    exportDataPackage: exportDataPackage,
    buildHandoutHTML: buildHandoutHTML,
    buildOutlineMarkdown: buildOutlineMarkdown,
    buildAnkiCSV: buildAnkiCSV,
    pageBookTitle: pageBookTitle,
    currentExportTitle: currentExportTitle,
    sanitizeFileName: sanitizeFileName
  };
})(window);
