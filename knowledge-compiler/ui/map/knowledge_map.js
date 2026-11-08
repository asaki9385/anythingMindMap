/* ════════════════════════════════════════════════════════════════
   AnythingMindmap — map controller (KTMap)
   Owns the single Mind Elixir instance and the page-level map UI:
   layout, theme, selection, detail panel, search, chapter drawer,
   breadcrumb, minimap, hover peek, view persistence, postMessage,
   keyboard navigation and ARIA.
   ════════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  var A = global.KTMapAdapter;
  var state = null;
  var els = {};
  var listeners = {};
  var peekTimer = 0;
  var saveTimer = 0;
  var viewTimer = 0;
  var miniTimer = 0;
  var searchActive = -1;
  var searchResults = [];
  var ctxMenu = null;
  var helpOpen = false;

  var PALETTE_LIGHT = ['#c26a4a', '#6f8f6a', '#6f86a8', '#8a6f9e', '#b08a4f', '#a86f6f'];
  var PALETTE_DARK = ['#e08264', '#65b88f', '#8da5cb', '#b39bd1', '#e0c28f', '#cc8a82'];

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    if (value == null) return '';
    var text = String(value);
    if (global.KT && KT.escapeHtml) return KT.escapeHtml(text);
    return text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function on(name, fn) {
    (listeners[name] = listeners[name] || []).push(fn);
    return function () { off(name, fn); };
  }
  function off(name, fn) {
    var list = listeners[name] || [];
    var index = list.indexOf(fn);
    if (index >= 0) list.splice(index, 1);
  }
  function emit(name, payload) {
    (listeners[name] || []).slice().forEach(function (fn) {
      try { fn(payload); } catch (err) { console.error(err); }
    });
  }
  function themeMode() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }
  function hexToRgba(hex, alpha) {
    var h = String(hex || '').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    if (!isFinite(n)) return 'rgba(194,106,74,' + alpha + ')';
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
  }
  function palette() { return themeMode() === 'dark' ? PALETTE_DARK : PALETTE_LIGHT; }
  function branchColor(index) {
    var p = palette();
    if (index == null || index < 0) return p[0];
    return p[index % p.length];
  }
  function themeObject() {
    var dark = themeMode() === 'dark';
    return {
      name: dark ? 'WarmAcademicDark' : 'WarmAcademic',
      type: dark ? 'dark' : 'light',
      palette: palette(),
      // 引擎在 init() 时用 theme.generateSubBranch 覆盖实例方法，所以连线几何
      // 必须挂在 theme 上（构造参数里的同名选项会在 init 时被替换掉）。
      generateSubBranch: generateSubBranch,
      cssVar: {
        '--bgcolor': 'transparent',
        '--main-color': dark ? '#6d655b' : '#b9aa99',
        '--main-bgcolor': dark ? '#20201e' : '#ffffff',
        '--main-bgcolor-transparent': dark ? 'rgba(32,32,30,.92)' : 'rgba(255,255,255,.92)',
        '--main-border': '1px solid ' + (dark ? 'rgba(255,255,255,.10)' : 'rgba(31,30,29,.10)'),
        '--color': dark ? '#eeede8' : '#2b2723',
        '--selected': dark ? '#e08264' : '#c26a4a',
        '--accent-color': dark ? '#e08264' : '#c26a4a',
        '--root-color': '#fffaf5',
        '--root-bgcolor': dark ? '#d97757' : '#c26a4a',
        '--root-border-color': dark ? '#e08264' : '#c26a4a',
        '--root-radius': '14px',
        '--main-radius': '10px',
        '--topic-padding': '6px 12px',
        '--panel-color': dark ? '#eeede8' : '#2b2723',
        '--panel-bgcolor': dark ? '#20201e' : '#ffffff',
        '--panel-border-color': dark ? 'rgba(255,255,255,.12)' : 'rgba(31,30,29,.12)',
        '--map-padding': '84px',
        '--node-gap-x': '34px',
        '--node-gap-y': '12px',
        '--main-gap-x': '52px',
        '--main-gap-y': '18px'
      }
    };
  }
  // ── connector geometry ───────────────────────────────────────
  // 品牌视觉修复：Mind Elixir 6 默认把子分支连线画到子节点框「远端」边
  // （LHS: cL + GAP / RHS: cL + cW - GAP），并沿框的底边横穿过去。默认主题
  // 的子节点没有边框和底色，看起来像下划线；本项目的节点都是有边框、带底
  // 色（L1 还半透明）的卡片，于是连线会被看见从框内穿过、贴着底边压线。
  // 这里保留引擎的 S 曲线 + 水平肘形，但两端都落在**面向父节点的那条边**
  // 的中点：父节点框边缘 → 子节点框边缘，连线不再进入任何节点框。
  // 坐标口径与引擎一致：pL/pW/pT/pH 是父 `.me-parent` 盒，cL/cW/cT/cH 是
  // 子 `.me-parent` 盒；`.me-parent` 的水平内边距等于 --node-gap-x，一级
  // 节点（`.me-main > .me-wrapper > .me-parent`，引擎用 isFirst 标记）为 0。
  var CONNECTOR_DOWN_RADIUS = 8;
  var CONNECTOR_PAD_Y = 6; // 内置样式表里 `.me-parent` 的上下内边距

  function connectorSide(direction) {
    var d = String(direction == null ? '' : direction).toLowerCase();
    if (d === 'down' || d === '3') return 'down';
    if (d === 'lhs' || d === 'left' || d === '0') return 'lhs';
    return 'rhs';
  }

  // 上下布局：竖 → 横 → 竖的圆角折线（与引擎 roundedVertical 同形）。
  function roundedVertical(x1, y1, x2, y2, radius) {
    if (x1 === x2) return 'M ' + x1 + ' ' + y1 + ' V ' + y2;
    var midY = (y1 + y2) / 2;
    var dir = x2 > x1 ? 1 : -1;
    var r = Math.min(radius, Math.abs(x2 - x1) / 2, Math.abs(midY - y1), Math.abs(y2 - midY));
    return 'M ' + x1 + ' ' + y1 +
      ' V ' + (midY - r) +
      ' Q ' + x1 + ' ' + midY + ' ' + (x1 + dir * r) + ' ' + midY +
      ' H ' + (x2 - dir * r) +
      ' Q ' + x2 + ' ' + midY + ' ' + x2 + ' ' + (midY + r) +
      ' V ' + y2;
  }

  function generateSubBranch(params) {
    params = params || {};
    var GAP = parseInt((this && this.container && this.container.style.getPropertyValue('--node-gap-x')) || '0', 10);
    if (!isFinite(GAP) || GAP < 0) GAP = 0;
    var pT = Number(params.pT) || 0, pL = Number(params.pL) || 0;
    var pW = Number(params.pW) || 0, pH = Number(params.pH) || 0;
    var cT = Number(params.cT) || 0, cL = Number(params.cL) || 0;
    var cW = Number(params.cW) || 0, cH = Number(params.cH) || 0;
    var side = connectorSide(params.direction);
    // 一级节点没有内边距；更深层的 `.me-parent` 四边留出 GAP / 6px。
    var padX = params.isFirst ? 0 : GAP;
    var padY = params.isFirst ? 0 : CONNECTOR_PAD_Y;

    if (side === 'down') {
      return roundedVertical(
        pL + pW / 2, pT + pH - padY,
        cL + cW / 2, cT + CONNECTOR_PAD_Y,
        CONNECTOR_DOWN_RADIUS
      );
    }

    var left = side === 'lhs';
    var y0 = pT + pH / 2;   // 父节点竖中线
    var y1 = cT + cH / 2;   // 子节点竖中线
    var x0 = left ? pL + padX : pL + pW - padX;         // 父框面向子节点的一侧
    var xEnd = left ? cL + cW - GAP : cL + GAP;         // 子框面向父节点的一侧
    var xMid = left ? x0 - GAP : x0 + GAP;              // 肘形拐点，落在节点间隙内
    var off = Math.min(Math.abs(y0 - y1) / 300 * GAP, GAP); // 引擎同款 S 曲线拉伸量
    return 'M ' + x0 + ' ' + y0 +
      ' C ' + (left ? x0 - off : x0 + off) + ' ' + y0 +
      ' ' + (left ? xMid + off : xMid - off) + ' ' + y1 +
      ' ' + xMid + ' ' + y1 +
      ' H ' + xEnd;
  }

  function styleFor(node, depth) {
    var dark = themeMode() === 'dark';
    if (depth === 0) {
      return {
        fontFamily: '"Songti SC", "STSong", Georgia, serif',
        fontSize: '16px',
        fontWeight: '600'
      };
    }
    if (depth === 1) {
      var color = branchColor(state.adapter.branchIndexById.get(node.id));
      return {
        background: hexToRgba(color, dark ? 0.18 : 0.10),
        border: '1px solid ' + hexToRgba(color, dark ? 0.55 : 0.35),
        color: color,
        fontFamily: '"Songti SC", "STSong", Georgia, serif',
        fontSize: '14px',
        fontWeight: '600'
      };
    }
    return {
      background: dark ? '#20201e' : '#ffffff',
      border: '1px solid ' + (dark ? 'rgba(255,255,255,.10)' : 'rgba(31,30,29,.10)'),
      color: dark ? '#eeede8' : '#2b2723',
      fontSize: '12.5px'
    };
  }

  // ── engine data ──────────────────────────────────────────────

  function adaptiveExpandLevel(adapter) {
    var count = (adapter || (state && state.adapter)).count();
    if (count > 800) return 1;
    if (count > 300) return 2;
    return 2;
  }

  function buildData(opts) {
    opts = opts || {};
    var theme = themeObject();
    var data = state.adapter.toMindElixirData({
      layout: state.layout,
      expandLevel: opts.expandLevel == null ? state.expandLevel : opts.expandLevel,
      expandedOverrides: state.overrides,
      branchColorFor: function (node, depth) {
        if (depth === 0) return branchColor(-1);
        if (depth === 1) return branchColor(state.adapter.branchIndexById.get(node.id));
        return undefined;
      },
      styleFor: styleFor
    });
    data.theme = theme;
    return data;
  }

  function findTopic(id) {
    if (!state.mei || !id) return null;
    try { return state.mei.findEle(id); } catch (err) { return null; }
  }

  function ensureVisible(id) {
    if (findTopic(id)) return true;
    var meRoot = state.mei && state.mei.nodeData;
    if (!meRoot) return false;
    var path = A.findMePath(meRoot, id) || [];
    if (!path.length) return false;
    for (var i = 0; i < path.length - 1; i++) {
      path[i].expanded = true;
      state.overrides[path[i].id] = true;
    }
    var data = state.mei.getData();
    if (state.mei.diffRefresh) state.mei.diffRefresh(data);
    else state.mei.refresh(data);
    return !!findTopic(id);
  }

  function selectById(id, opts) {
    opts = opts || {};
    if (!id) return;
    if (!ensureVisible(id)) return;
    var topic = findTopic(id);
    if (!topic) return;
    if (!opts.spotlight) clearSpotlight();
    state.mei.selectNode(topic);
    if (opts.center !== false) state.mei.scrollIntoView(topic, true);
    state.selectedId = id;
    renderDetail(id);
    updateBreadcrumb();
    updateChapterActive();
    postSelection();
    saveView();
    if (opts.spotlight) spotlight(id);
  }

  function spotlight(id) {
    clearSpotlight();
    if (!id) return;
    var node = state.adapter.getNode(id);
    if (!node) return;
    var allowed = new Set();
    var path = A.findMePath(state.mei.nodeData, id) || [];
    path.forEach(function (n) { allowed.add(n.id); });
    (function walk(n) { allowed.add(n.id); (n.children || []).forEach(walk); })(node);
    Array.prototype.forEach.call(els.chart.querySelectorAll('.me-tpc'), function (tpc) {
      var raw = tpc.getAttribute('data-nodeid') || '';
      var nodeId = raw.slice(2);
      var wrapper = tpc.closest('.me-wrapper');
      if (!wrapper) return;
      wrapper.classList.toggle('ktm-dimmed', !allowed.has(nodeId));
      wrapper.classList.toggle('ktm-spotlight', nodeId === id);
    });
    state.focusId = id;
    updateBreadcrumb();
  }

  function clearSpotlight() {
    if (!els.chart) return;
    Array.prototype.forEach.call(els.chart.querySelectorAll('.ktm-dimmed,.ktm-spotlight'), function (el) {
      el.classList.remove('ktm-dimmed', 'ktm-spotlight');
    });
    state.focusId = '';
  }

  // ── detail panel ─────────────────────────────────────────────

  function field(label, html) {
    return '<div class="ktm-field"><div class="ktm-field-label">' + esc(label) + '</div>' + html + '</div>';
  }

  // ── node field editing (edit mode) ───────────────────────────
  // 编辑模式下详情面板同时是节点检视器：名称 / 摘要 / 关键词 / 原文 / 笔记
  // 都可以就地改写。轻字段走 metadata.kt（引擎会记历史，Ctrl+Z 可撤销）；
  // 正文等重字段平时不进 metadata，一旦被编辑就把旧值补进 metadata 当“改前
  // 快照”，这样撤销仍然还原得回来，也不会让所有节点的正文都背在快照里。
  var HEAVY_EDIT_FIELDS = { content: true };

  function ktMeta(id) {
    var topic = findTopic(id);
    var nodeObj = topic && topic.nodeObj;
    var meta = nodeObj && nodeObj.metadata && nodeObj.metadata.kt;
    return meta && typeof meta === 'object' ? meta : {};
  }

  function editField(label, fieldName, value, opts) {
    opts = opts || {};
    var text = value == null ? '' : String(value);
    return '<div class="ktm-field ktm-edit-field">'
      + '<div class="ktm-field-label">' + esc(label) + '</div>'
      + '<textarea class="ktm-edit-area' + (opts.mono ? ' is-mono' : '') + '" rows="' + (opts.rows || 3) + '"'
      + ' data-ktm-field="' + esc(fieldName) + '" placeholder="' + esc(opts.placeholder || '') + '"'
      + ' spellcheck="false">' + esc(text) + '</textarea>'
      + '</div>';
  }

  function keywordEditor(keywords) {
    var terms = [];
    (Array.isArray(keywords) ? keywords : []).forEach(function (kw) {
      var term = kw && typeof kw === 'object' ? kw.term : kw;
      term = term == null ? '' : String(term).trim();
      if (term) terms.push(term);
    });
    var html = '<div class="ktm-field ktm-edit-field"><div class="ktm-field-label">关键词</div>'
      + '<div class="ktm-edit-keywords">';
    terms.forEach(function (term) {
      html += '<span class="ktm-keyword ktm-keyword-edit">' + esc(term)
        + '<button type="button" class="ktm-kw-remove" data-ktm-kw-remove="' + esc(term)
        + '" aria-label="删除关键词 ' + esc(term) + '">×</button></span>';
    });
    html += '<input type="text" class="ktm-edit-kw-input" data-ktm-kw-input placeholder="添加关键词，回车确认"'
      + ' aria-label="添加关键词" autocomplete="off"></div></div>';
    return html;
  }

  function applyNodePatch(id, patch) {
    if (!state.editing || state.readonly) return false;
    var topic = findTopic(id);
    if (!topic || !state.mei || !state.mei.reshapeNode) return false;
    var nodeObj = topic.nodeObj;
    if (!nodeObj) return false;
    // A canvas inline editor (#input-box) may still be open; commit/close it
    // first so the panel write and the inline edit can't fight over the node.
    try {
      var box = state.mei.nodes && state.mei.nodes.querySelector('#input-box');
      if (box && box.blur) box.blur();
    } catch (err) {}
    var meta = (nodeObj.metadata && nodeObj.metadata.kt) || {};
    var nextMeta = Object.assign({}, meta);
    var seededHeavy = null;
    var keys = Object.keys(patch);
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (key === 'title') continue;
      var value = patch[key];
      if (HEAVY_EDIT_FIELDS[key]) {
        // Heavy fields must be written explicitly (even when cleared): the adapter
        // re-adds any heavy value that metadata.kt does not carry, so deleting the
        // key would resurrect the old content instead of clearing it.
        nextMeta[key] = value == null ? '' : value;
      } else if (value === undefined || value === null || value === '') {
        delete nextMeta[key];
      } else {
        nextMeta[key] = value;
      }
      // Seed the pre-edit value into the live metadata BEFORE reshapeNode so the
      // history entry's "origin" snapshot carries it and undo restores it.
      if (HEAVY_EDIT_FIELDS[key] && !Object.prototype.hasOwnProperty.call(meta, key)) {
        var heavy = state.adapter.heavyById.get(id) || {};
        var seeded = Object.prototype.hasOwnProperty.call(heavy, key) ? heavy[key] : '';
        if (!seededHeavy) seededHeavy = { key: key, value: seeded };
        meta[key] = seeded;
      }
    }
    var patchData = { metadata: Object.assign({}, nodeObj.metadata || {}, { kt: nextMeta }) };
    if (Object.prototype.hasOwnProperty.call(patch, 'title')) {
      if (state.adapter.displayRoot && nodeObj.id === state.adapter.displayRoot.id) { toast('根标题请到资料库重命名'); return false; }
      var title = String(patch.title == null ? '' : patch.title).replace(/\s+/g, ' ').trim();
      if (!title) return false;
      if (title === nodeObj.topic) return false;
      patchData.topic = title;
    }
    // The engine's undo journal records getData() snapshots, and heavy fields are
    // deliberately absent from them; a seed written only into the live metadata is
    // therefore lost on undo. Patch the just-recorded "before" snapshot so Ctrl+Z
    // restores the pre-edit 原文 (and covers adding content to a node that had none).
    state.patchCommit = true;
    try { state.mei.reshapeNode(topic, patchData); }
    catch (err) { console.error('[KTMap] applyNodePatch failed', err); state.patchCommit = false; return false; }
    state.patchCommit = false;
    if (seededHeavy) seedUndoBeforeSnapshot(id, seededHeavy.key, seededHeavy.value);
    scheduleSave();
    return true;
  }

  function snapshotNodeById(root, id) {
    var found = null;
    (function walk(n) {
      if (found || !n) return;
      if (n.id === id) { found = n; return; }
      (n.children || []).forEach(walk);
    })(root);
    return found;
  }

  function seedUndoBeforeSnapshot(nodeId, fieldName, value) {
    var stack = state.mei && state.mei.historyStack;
    if (!stack || typeof stack.getEntries !== 'function') return false;
    var entries = stack.getEntries();
    var index = typeof stack.currentIndex === 'number' ? stack.currentIndex : entries.length;
    var entry = entries[index - 1];
    var snapshot = entry && entry.before;
    var node = snapshot && snapshot.nodeData ? snapshotNodeById(snapshot.nodeData, nodeId) : null;
    if (!node) return false;
    node.metadata = Object.assign({}, node.metadata || {});
    node.metadata.kt = Object.assign({}, node.metadata.kt || {});
    node.metadata.kt[fieldName] = value;
    return true;
  }

  function currentFieldValue(id, fieldName) {
    var node = state.adapter.getNode(id);
    if (fieldName === 'title') return node ? String(node.title || '') : '';
    if (fieldName === 'content') {
      var kt = ktMeta(id);
      if (Object.prototype.hasOwnProperty.call(kt, 'content')) return String(kt.content == null ? '' : kt.content);
      var heavy = state.adapter.heavyById.get(id) || {};
      return String(heavy.content == null ? '' : heavy.content);
    }
    var meta = ktMeta(id);
    return String(meta[fieldName] == null ? '' : meta[fieldName]);
  }

  function autoGrowArea(el) {
    if (!el || el.tagName !== 'TEXTAREA') return;
    el.style.height = 'auto';
    el.style.height = Math.min(520, el.scrollHeight + 2) + 'px';
  }

  function bindDetailEditing(id) {
    if (!els.infoContent) return;
    var commit = function (fieldName, value) {
      if (value === currentFieldValue(id, fieldName)) return true;
      return applyNodePatch(id, (function () { var p = {}; p[fieldName] = value; return p; })());
    };
    els.infoContent.querySelectorAll('[data-ktm-field]').forEach(function (el) {
      var fieldName = el.getAttribute('data-ktm-field');
      if (el.tagName === 'INPUT') {
        el.addEventListener('change', function () {
          if (!commit(fieldName, el.value)) el.value = currentFieldValue(id, fieldName);
        });
        el.addEventListener('keydown', function (e) {
          if (e.isComposing || e.keyCode === 229) return;
          if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
          else if (e.key === 'Escape') {
            e.preventDefault();
            el.value = currentFieldValue(id, fieldName);
            el.blur();
          }
        });
        return;
      }
      autoGrowArea(el);
      el.addEventListener('input', function () {
        autoGrowArea(el);
        clearTimeout(el._ktmTimer);
        el._ktmTimer = setTimeout(function () { commit(fieldName, el.value); }, 600);
      });
      el.addEventListener('blur', function () {
        clearTimeout(el._ktmTimer);
        autoGrowArea(el);
        commit(fieldName, el.value);
      });
      el.addEventListener('keydown', function (e) {
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); el.blur(); }
        else if (e.key === 'Escape') {
          e.preventDefault();
          el.value = currentFieldValue(id, fieldName);
          el.blur();
        }
      });
    });

    var kwInput = els.infoContent.querySelector('[data-ktm-kw-input]');
    if (kwInput) {
      var addKeyword = function () {
        var term = String(kwInput.value || '').replace(/[,，;；\s]+/g, ' ').trim();
        if (!term) return;
        var terms = keywordTerms(id);
        if (terms.indexOf(term) === -1) {
          terms.push(term);
          applyNodePatch(id, { keywords: terms });
          renderDetail(id, { preservePanels: true });
          var next = els.infoContent.querySelector('[data-ktm-kw-input]');
          if (next) next.focus();
        } else {
          kwInput.value = '';
        }
      };
      kwInput.addEventListener('keydown', function (e) {
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === 'Enter' || e.key === ',' || e.key === '，') { e.preventDefault(); addKeyword(); }
        else if (e.key === 'Escape') { kwInput.value = ''; kwInput.blur(); }
      });
      kwInput.addEventListener('blur', function () { if (kwInput.value.trim()) addKeyword(); });
    }
    els.infoContent.querySelectorAll('[data-ktm-kw-remove]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var term = btn.getAttribute('data-ktm-kw-remove');
        var terms = keywordTerms(id).filter(function (t) { return t !== term; });
        applyNodePatch(id, { keywords: terms });
        renderDetail(id, { preservePanels: true });
      });
    });
  }

  function keywordTerms(id) {
    var raw = ktMeta(id).keywords;
    var terms = [];
    (Array.isArray(raw) ? raw : []).forEach(function (kw) {
      var term = kw && typeof kw === 'object' ? kw.term : kw;
      term = term == null ? '' : String(term).trim();
      if (term) terms.push(term);
    });
    return terms;
  }

  function ensureMermaid() {
    if (global.mermaid) return Promise.resolve(global.mermaid);
    if (state.mermaidPromise) return state.mermaidPromise;
    state.mermaidPromise = new Promise(function (resolve, reject) {
      var script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.0/dist/mermaid.min.js';
      script.onload = function () { resolve(global.mermaid); };
      script.onerror = function () { reject(new Error('Mermaid 加载失败')); };
      document.head.appendChild(script);
    });
    return state.mermaidPromise;
  }

  function renderDetail(id, opts) {
    opts = opts || {};
    var node = state.adapter.getNode(id);
    if (!node || !els.infoPanel) return;
    if (opts.onlyIfOpen && !els.infoPanel.classList.contains('show')) return;
    // Don't blow away the panel while the user is typing in one of its fields.
    if (opts.skipIfTyping && (state.patchCommit
        || (els.infoContent && els.infoContent.contains(document.activeElement)
            && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)))) return;
    var editing = !!(state.editing && !state.readonly);
    var isRoot = !!(state.adapter.displayRoot && id === state.adapter.displayRoot.id);
    var light = state.adapter.lightById.get(id) || {};
    var heavy = state.adapter.heavyById.get(id) || {};
    var kt = ktMeta(id);
    var merged = Object.assign({}, light, heavy, kt, { title: node.title });
    var path = state.adapter.getPath(id);
    var html = '<div class="ktm-detail-head' + (editing ? ' is-editing' : '') + '">';
    if (editing && !isRoot) {
      html += '<input class="ktm-detail-title ktm-edit-title" data-ktm-field="title" value="' + esc(node.title)
        + '" aria-label="节点名称" spellcheck="false">';
    } else {
      html += '<div class="ktm-detail-title">' + esc(node.title) + '</div>';
    }
    html += '<div class="ktm-detail-path">' + path.map(esc).join(' <span class="ktm-sep">›</span> ') + '</div>'
      + '</div>';

    var badges = [];
    if (merged.level != null) badges.push('Level ' + esc(merged.level));
    if (Array.isArray(merged.exam_points) && merged.exam_points.length) badges.push('考点 ' + merged.exam_points.length);
    if (node.children && node.children.length) badges.push(node.children.length + ' 个子节点');
    if (merged.fallback) badges.push('AI 兜底');
    if (editing) badges.push('编辑中');
    if (badges.length) html += '<div class="ktm-badges">' + badges.map(function (b) {
      return '<span class="ktm-badge">' + b + '</span>';
    }).join('') + '</div>';

    if (editing) {
      html += editField('摘要', 'summary', merged.summary, {
        rows: 3, placeholder: '一句话概括这个知识点（显示在悬停预览里）'
      });
      html += keywordEditor(merged.keywords);
    } else if (merged.summary) {
      html += field('摘要', '<div class="ktm-summary">' + (global.KT && KT.formatTextBlocks
        ? KT.formatTextBlocks(merged.summary, merged.keywords || [], 'ktm-para')
        : '<p class="ktm-para">' + esc(merged.summary) + '</p>') + '</div>');
    }
    if (!editing && Array.isArray(merged.keywords) && merged.keywords.length) {
      html += field('关键词', '<div class="ktm-keywords">' + merged.keywords.map(function (kw) {
        var term = kw && typeof kw === 'object' ? kw.term : kw;
        var context = kw && typeof kw === 'object' ? kw.context : '';
        return '<span class="ktm-keyword" title="' + esc(context || '') + '">' + esc(term) + '</span>';
      }).join('') + '</div>');
    }
    if (Array.isArray(merged.exam_points) && merged.exam_points.length) {
      html += field('考点', '<div class="ktm-exams">' + merged.exam_points.map(function (ep) {
        var freq = ep && ep.frequency || '';
        var type = ep && ep.type || '';
        var point = ep && ep.point || '';
        return '<div class="ktm-exam"><span class="ktm-exam-freq is-' + esc(freq) + '">' + esc(freq || '—') + '</span>'
          + '<span class="ktm-exam-type">' + esc(type) + '</span><span class="ktm-exam-point">' + esc(point) + '</span></div>';
      }).join('') + '</div>');
    }
    if (Array.isArray(merged.captions) && merged.captions.length) {
      html += field('图表说明', merged.captions.map(function (cap) {
        return '<div class="ktm-caption"><b>' + esc(cap && cap.label || '') + '</b> ' + esc(cap && cap.text || '') + '</div>';
      }).join(''));
    }
    if (merged.guide) {
      html += field('导学', global.KT && KT.formatTextBlocks
        ? KT.formatTextBlocks(merged.guide, [], 'ktm-para')
        : '<p class="ktm-para">' + esc(merged.guide) + '</p>');
    }
    if (Array.isArray(merged.prerequisites) && merged.prerequisites.length) {
      html += field('先修', '<div class="ktm-prereq">' + merged.prerequisites.map(function (p) {
        return '<span>' + esc(p) + '</span>';
      }).join('') + '</div>');
    }
    if (editing) {
      html += editField('原文', 'content', merged.content, {
        rows: 9, placeholder: '节点正文 / 讲义原文；留空表示删除', mono: false
      });
      html += editField('笔记', 'note', merged.note, {
        rows: 3, placeholder: '自己的补充、疑问或记忆线索'
      });
    } else {
      if (merged.content) {
        html += field('原文', '<div class="ktm-content">' + (global.KT && KT.formatContent
          ? KT.formatContent(merged.content, merged.keywords || [], 0, merged.highlights || [])
          : '<p class="ktm-para">' + esc(merged.content) + '</p>') + '</div>');
      }
      if (merged.note) {
        html += field('笔记', '<div class="ktm-note">' + esc(merged.note) + '</div>');
      }
    }
    if (Array.isArray(merged.tables) && merged.tables.length && global.KT && KT.renderNodeTables) {
      html += field('表格', KT.renderNodeTables(merged.tables));
    }
    if (merged.mermaid && global.KT && KT.renderNodeMermaid) {
      html += field('流程图', KT.renderNodeMermaid(merged.mermaid, 'ktm-mermaid-' + id));
    }
    if (merged.hyperLink) {
      html += field('链接', '<a class="ktm-link" href="' + esc(merged.hyperLink) + '" target="_blank" rel="noopener">' + esc(merged.hyperLink) + '</a>');
    }
    if (editing) {
      html += '<div class="ktm-edit-hint">改动会自动保存；双击节点可直接改标题，Ctrl+Z 撤销。</div>';
    }

    html += '<div class="ktm-detail-actions">'
      + '<button type="button" data-ktm-action="focus">' + (state.focusId === id ? '取消聚焦' : '聚焦此分支') + '</button>'
      + '<button type="button" data-ktm-action="copy">复制路径</button>'
      + '<button type="button" data-ktm-action="ask">在此提问</button>'
      + '<button type="button" data-ktm-action="review">复习此节点</button>'
      + '</div>';

    els.infoContent.innerHTML = html;
    els.infoPanel.classList.add('show');
    if (!opts.preservePanels && global.KT && KT.Panels) KT.Panels.closeOthers('info');
    var renderRich = function () {
      if (global.KT && KT.postRenderMathAndMermaid) KT.postRenderMathAndMermaid(els.infoContent);
    };
    if (merged.mermaid) ensureMermaid().then(renderRich).catch(renderRich);
    else renderRich();
    els.infoContent.querySelectorAll('[data-ktm-action]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var action = btn.getAttribute('data-ktm-action');
        if (action === 'focus') {
          if (state.focusId === id) { clearSpotlight(); renderDetail(id); }
          else spotlight(id);
        } else if (action === 'copy') {
          copyPath(id);
        } else if (action === 'ask') {
          askAboutNode(id);
        } else if (action === 'review') {
          reviewNode(id);
        }
      });
    });
    if (editing) bindDetailEditing(id);
  }

  function closeDetail() {
    if (els.infoPanel) els.infoPanel.classList.remove('show');
    if (els.chart) els.chart.focus();
  }

  function copyPath(id) {
    var path = state.adapter.getPath(id).join(' › ');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).then(function () {
        toast('已复制路径');
      }).catch(function () { toast(path); });
    } else {
      toast(path);
    }
  }

  function chapterOf(id) {
    var path = state.adapter.getPath(id);
    return path.length > 1 ? path[1] : (path[0] || '');
  }

  function askAboutNode(id) {
    var node = state.adapter.getNode(id);
    if (!node) return;
    var title = node.title || '';
    var question = '请结合原文解释「' + title + '」的核心内容、易混点和可能的考法。';
    var chapter = chapterOf(id);
    if (state.embed && window.parent && window.parent !== window) {
      try {
        window.parent.postMessage({ type: 'kt-open-chat', file: state.key, chapter: chapter, question: question }, window.location.origin);
        return;
      } catch (err) {}
    }
    if (global.ReviewWidget && ReviewWidget.ask) {
      if (global.ReviewWidget.setContext) ReviewWidget.setContext({ chapter: chapter });
      ReviewWidget.ask(question);
    } else {
      toast('对话组件未加载');
    }
  }

  function reviewNode(id) {
    var chapter = chapterOf(id);
    if (state.embed && window.parent && window.parent !== window) {
      try {
        window.parent.postMessage({ type: 'kt-open-chat', file: state.key, chapter: chapter, scope: 'all' }, window.location.origin);
        return;
      } catch (err) {}
    }
    if (global.ReviewWidget && ReviewWidget.startChapter) {
      ReviewWidget.startChapter(chapter, 'all');
    } else {
      toast('对话复习未加载');
    }
  }

  // ── breadcrumb ───────────────────────────────────────────────

  function updateBreadcrumb() {
    if (!els.breadcrumb) return;
    var id = state.selectedId;
    if (!id) { els.breadcrumb.innerHTML = ''; els.breadcrumb.classList.remove('show'); return; }
    var path = state.adapter.getPath(id);
    if (!path.length) { els.breadcrumb.classList.remove('show'); return; }
    var ids = [];
    var node = state.adapter.getNode(id);
    // Build id chain by walking display tree once.
    (function walk(n, chain) {
      if (!n) return;
      var next = chain.concat([n]);
      if (n.id === id) { ids = next; return; }
      (n.children || []).forEach(function (c) { if (!ids.length) walk(c, next); });
    })(state.adapter.displayRoot, []);
    var html = '<button type="button" data-ktm-crumb="">全书</button>';
    ids.forEach(function (n, index) {
      if (index === ids.length - 1) {
        html += '<span class="ktm-crumb-current">' + esc(n.title) + '</span>';
      } else {
        html += '<span class="ktm-crumb-sep">›</span><button type="button" data-ktm-crumb="' + esc(n.id) + '">' + esc(n.title) + '</button>';
      }
    });
    els.breadcrumb.innerHTML = html;
    els.breadcrumb.classList.add('show');
    els.breadcrumb.querySelectorAll('[data-ktm-crumb]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-ktm-crumb');
        if (!target) {
          clearSpotlight();
          closeDetail();
          state.selectedId = '';
          if (state.mei && state.mei.clearSelection) state.mei.clearSelection();
          updateBreadcrumb();
          updateChapterActive();
          decorateAria();
          return;
        }
        selectById(target, { center: true, spotlight: false });
      });
    });
  }

  // ── chapter drawer ───────────────────────────────────────────

  function modules() {
    if (!state.adapter || !global.KT || !KT.bookModules) return [];
    return KT.bookModules(state.adapter.getDisplayTree()) || [];
  }

  function moduleId(module) {
    return module && module.id;
  }

  function renderChapterList() {
    if (!els.chapterList) return;
    var list = modules();
    if (!list.length) {
      els.chapterList.innerHTML = '<div class="ktm-empty">这份资料暂时没有章节。</div>';
      return;
    }
    var order = global.KT && KT.computeStudyOrder ? KT.computeStudyOrder(list) : list.map(function (_, i) { return i; });
    var activeChapter = state.selectedId ? chapterOf(state.selectedId) : '';
    var html = '';
    order.forEach(function (index) {
      var module = list[index];
      if (!module) return;
      var title = module.title || module.name || '未命名章节';
      var stats = global.KT && KT.moduleStats ? KT.moduleStats(module) : { nodes: 0, examCount: 0 };
      var guide = module.guide || '';
      var isActive = title === activeChapter;
      var hasGuide = !!guide.trim();
      html += '<div class="ktm-chapter' + (isActive ? ' is-active' : '') + (hasGuide ? ' has-guide' : '') + '" data-ktm-chapter-id="' + esc(module.id) + '">'
        + '<div class="ktm-chapter-row">'
        + '<span class="ktm-chapter-num">' + (order.indexOf(index) + 1) + '</span>'
        + '<button type="button" class="ktm-chapter-main" data-ktm-chapter="' + esc(module.id) + '">'
        + '<span class="ktm-chapter-title">' + esc(title) + '</span>'
        + '<span class="ktm-chapter-meta">' + stats.nodes + ' 知识点' + (stats.examCount ? ' · 考点 ' + stats.examCount : '') + '</span>'
        + '</button>'
        + (hasGuide ? '<button type="button" class="ktm-chapter-guide-btn" data-ktm-guide="' + esc(module.id) + '" title="展开导学">' + (global.KT ? KT.iconHtml('chevronDown') : '⌄') + '</button>' : '')
        + '</div>'
        + (hasGuide ? '<div class="ktm-chapter-guide">' + (global.KT && KT.formatTextBlocks
          ? KT.formatTextBlocks(guide, [], 'ktm-para')
          : '<p class="ktm-para">' + esc(guide) + '</p>') + '</div>' : '')
        + '</div>';
    });
    els.chapterList.innerHTML = html;
    els.chapterList.querySelectorAll('[data-ktm-chapter]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-ktm-chapter');
        var topic = findTopic(id);
        if (topic && state.mei) state.mei.expandNode(topic, true);
        selectById(id, { center: true, spotlight: true });
        if (window.innerWidth < 900) toggleDrawer(false);
      });
    });
    els.chapterList.querySelectorAll('[data-ktm-guide]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var row = btn.closest('.ktm-chapter');
        if (row) row.classList.toggle('is-guide-open');
      });
    });
  }

  function updateChapterActive() {
    if (!els.chapterList) return;
    var active = state.selectedId ? chapterOf(state.selectedId) : '';
    els.chapterList.querySelectorAll('.ktm-chapter').forEach(function (row) {
      var title = row.querySelector('.ktm-chapter-title');
      row.classList.toggle('is-active', !!title && title.textContent === active);
    });
  }

  function toggleDrawer(force) {
    if (!els.chapterSidebar) return;
    var open = typeof force === 'boolean' ? force : !els.chapterSidebar.classList.contains('open');
    els.chapterSidebar.classList.toggle('open', open);
    if (open && global.KT && KT.Panels) KT.Panels.closeOthers('chapters');
  }

  // ── search ───────────────────────────────────────────────────

  function openSearch() {
    if (!els.searchPanel) return;
    if (global.KT && KT.Panels) KT.Panels.closeOthers('search');
    els.searchPanel.classList.add('open');
    els.searchPanel.setAttribute('aria-hidden', 'false');
    if (els.searchInput) {
      els.searchInput.value = '';
      els.searchInput.focus();
    }
    renderSearch('');
  }

  function closeSearch() {
    if (!els.searchPanel) return;
    els.searchPanel.classList.remove('open');
    els.searchPanel.setAttribute('aria-hidden', 'true');
    if (els.chart) els.chart.focus();
  }

  function toggleSearch() {
    if (!els.searchPanel) return;
    if (els.searchPanel.classList.contains('open')) closeSearch();
    else openSearch();
  }

  function renderSearch(query) {
    if (!els.searchResults) return;
    searchActive = -1;
    searchResults = query ? state.adapter.search(query) : [];
    if (!query) {
      els.searchResults.innerHTML = '<div class="ktm-search-empty">输入关键词搜索标题、摘要、关键词、考点或正文。</div>';
      if (els.searchStatus) els.searchStatus.textContent = '';
      return;
    }
    if (!searchResults.length) {
      els.searchResults.innerHTML = '<div class="ktm-search-empty">没有找到「' + esc(query) + '」</div>';
      if (els.searchStatus) els.searchStatus.textContent = '0 条结果';
      return;
    }
    var labels = { title: '标题', summary: '摘要', keywords: '关键词', exam: '考点', content: '正文', captions: '图表' };
    els.searchResults.innerHTML = searchResults.map(function (item, index) {
      return '<button type="button" class="ktm-search-item" role="option" data-ktm-result="' + index + '">'
        + '<span class="ktm-search-item-top"><b>' + esc(item.title) + '</b><span class="ktm-search-field">' + esc(labels[item.field] || item.field) + '</span></span>'
        + '<span class="ktm-search-path">' + esc(item.path) + '</span>'
        + '<span class="ktm-search-snippet">' + esc(item.snippet) + '</span>'
        + '</button>';
    }).join('');
    if (els.searchStatus) els.searchStatus.textContent = searchResults.length + ' 条结果';
    els.searchResults.querySelectorAll('[data-ktm-result]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var index = Number(btn.getAttribute('data-ktm-result'));
        jumpToResult(index);
      });
    });
  }

  function jumpToResult(index) {
    var item = searchResults[index];
    if (!item) return;
    selectById(item.id, { center: true, spotlight: true });
    closeSearch();
  }

  function moveSearchActive(delta) {
    if (!searchResults.length) return;
    searchActive = (searchActive + delta + searchResults.length) % searchResults.length;
    els.searchResults.querySelectorAll('.ktm-search-item').forEach(function (btn, index) {
      btn.classList.toggle('is-active', index === searchActive);
    });
    var active = els.searchResults.querySelector('.ktm-search-item.is-active');
    if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
  }

  // ── layout / zoom / view ─────────────────────────────────────

  function setLayout(layout, opts) {
    opts = opts || {};
    var next = A.legacyLayoutName(layout);
    if (next === state.layout && !opts.force) return;
    state.layout = next;
    var selected = state.selectedId;
    var data = buildData();
    if (state.mei) {
      // Mind Elixir's diffRefresh() intentionally ignores top-level `direction`.
      // A live layout switch must therefore go through the engine's direction
      // command first, then install the new node data (needed for SIDE weights).
      var directionMethod = {
        LEFT: 'initLeft',
        RIGHT: 'initRight',
        SIDE: 'initSide',
        DOWN: 'initDown'
      }[next];
      if (directionMethod && typeof state.mei[directionMethod] === 'function') {
        state.mei[directionMethod]();
      } else {
        state.mei.direction = A.layoutDirection(next);
      }
      if (typeof state.mei.refresh === 'function') state.mei.refresh(data);
      else if (state.mei.diffRefresh) state.mei.diffRefresh(data);
      try { state.mei.scaleFit(); } catch (err) { state.mei.toCenter(); }
    }
    if (selected) selectById(selected, { center: false });
    updateZoomUI();
    saveView();
  }

  function setExpandLevel(level) {
    var next = Math.max(1, Math.min(9, Number(level) || 2));
    state.expandLevel = next;
    state.overrides = {};
    var data = buildData();
    if (state.mei.diffRefresh) state.mei.diffRefresh(data);
    else state.mei.refresh(data);
    if (state.mei) {
      try { state.mei.scaleFit(); } catch (err) {}
    }
    updateZoomUI();
    saveView();
  }

  function expandAll() {
    setExpandLevel(9);
  }

  function collapseAll() {
    setExpandLevel(1);
  }

  function fit() {
    if (!state.mei) return;
    state.mei.scaleFit();
    updateZoomUI();
    saveView();
  }

  function zoomTo(value) {
    if (!state.mei) return;
    var next = Math.max(0.12, Math.min(2.5, Number(value) || 1));
    state.mei.scale(next);
    updateZoomUI();
    saveView();
  }

  function updateZoomUI() {
    if (!state.mei) return;
    var pct = Math.round((state.mei.scaleVal || 1) * 100);
    if (els.zoomLabel) els.zoomLabel.textContent = pct + '%';
    if (els.zoomRange) els.zoomRange.value = String(Math.max(5, Math.min(300, pct)));
    els.chart.classList.toggle('ktm-lod-compact', (state.mei.scaleVal || 1) < 0.55);
    updateMinimap();
  }

  function saveView() {
    if (!state.key) return;
    clearTimeout(viewTimer);
    viewTimer = setTimeout(function () {
      try {
        localStorage.setItem('kt_map_view_' + state.key, JSON.stringify({
          layout: state.layout,
          expandLevel: state.expandLevel,
          overrides: state.overrides,
          scale: state.mei ? state.mei.scaleVal : 1,
          selectedId: state.selectedId || '',
          focusId: state.focusId || ''
        }));
      } catch (err) {}
    }, 300);
  }

  function loadView() {
    if (!state.key) return null;
    try {
      var raw = localStorage.getItem('kt_map_view_' + state.key);
      return raw ? JSON.parse(raw) : null;
    } catch (err) { return null; }
  }

  // ── theme ────────────────────────────────────────────────────

  function setTheme() {
    if (!state.mei) return;
    var selected = state.selectedId;
    var scale = state.mei.scaleVal;
    var transform = state.mei.map && state.mei.map.style.transform;
    state.mei.changeTheme(themeObject(), false);
    var data = buildData();
    if (state.mei.diffRefresh) state.mei.diffRefresh(data);
    else state.mei.refresh(data);
    if (transform && state.mei.map) {
      state.mei.map.style.transform = transform;
      state.mei.scaleVal = scale;
    }
    if (selected) selectById(selected, { center: false });
    updateZoomUI();
    renderChapterList();
    if (selected) renderDetail(selected, { preservePanels: true, onlyIfOpen: true });
    refreshOutline();
  }

  // ── minimap ──────────────────────────────────────────────────

  function toggleMinimap(force) {
    if (!els.minimap) return;
    var open = typeof force === 'boolean' ? force : els.minimap.hidden;
    els.minimap.hidden = !open;
    state.minimapOn = open;
    if (!open) state.miniBox = null;
    document.querySelectorAll('[data-ktm-view="minimap"]').forEach(function (btn) {
      btn.classList.toggle('is-active', open);
      btn.setAttribute('aria-pressed', String(open));
    });
    if (open) updateMinimap();
    toast(open ? '小地图已开启：点击或拖拽可快速定位' : '小地图已关闭');
  }

  function updateMinimap() {
    if (!els.minimap || els.minimap.hidden || !state.mei) return;
    clearTimeout(miniTimer);
    miniTimer = setTimeout(function () {
      var canvas = els.minimap;
      var ctx = canvas.getContext('2d');
      if (!ctx) return;
      var rect = canvas.getBoundingClientRect();
      var dpr = Math.min(2, window.devicePixelRatio || 1);
      var w = Math.max(1, Math.round(rect.width));
      var h = Math.max(1, Math.round(rect.height));
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      var nodes = state.mei.nodes;
      var chartRect = els.chart && els.chart.getBoundingClientRect();
      if (!nodes || !chartRect) return;
      var topics = nodes.querySelectorAll('.me-tpc');
      if (!topics.length) {
        state.miniBox = null;
        return;
      }

      // Measure every visible topic in the engine's untransformed content
      // coordinate space. Using a reference topic later lets us derive the
      // viewport without parsing engine transform strings.
      var contentScale = Math.max(0.0001, state.mei.scaleVal || 1);
      var nodesRect = nodes.getBoundingClientRect();
      var boxes = [];
      var boxById = Object.create(null);
      Array.prototype.forEach.call(topics, function (tpc) {
        var r = tpc.getBoundingClientRect();
        var id = (tpc.getAttribute('data-nodeid') || '').slice(2);
        var depth = 0;
        var parentNode = tpc.nodeObj && tpc.nodeObj.parent;
        while (parentNode && parentNode.parent) {
          depth++;
          parentNode = parentNode.parent;
        }
        var box = {
          id: id,
          x: (r.left - nodesRect.left) / contentScale,
          y: (r.top - nodesRect.top) / contentScale,
          w: r.width / contentScale,
          h: r.height / contentScale,
          screenX: r.left,
          screenY: r.top,
          depth: depth,
          parentId: tpc.nodeObj && tpc.nodeObj.parent ? tpc.nodeObj.parent.id : '',
          color: depth === 0
            ? branchColor(-1)
            : depth === 1
              ? branchColor(state.adapter.branchIndexById.get(id))
              : ''
        };
        boxes.push(box);
        if (id) boxById[id] = box;
      });
      if (!boxes.length) return;

      var pad = 9;
      var fit = A.fitBoxesToCanvas(boxes, w, h, pad);
      if (!fit) return;
      var dark = themeMode() === 'dark';
      var clay = branchColor(-1);
      var toX = function (x) { return fit.ox + x * fit.scale; };
      var toY = function (y) { return fit.oy + y * fit.scale; };

      // Draw the visible tree structure first, then the viewport wash and node
      // blocks. This turns the minimap into an actual map preview rather than
      // a row of unrelated bars.
      ctx.save();
      ctx.beginPath();
      ctx.rect(pad, pad, Math.max(1, w - pad * 2), Math.max(1, h - pad * 2));
      ctx.clip();
      boxes.forEach(function (box) {
        var parent = box.parentId ? boxById[box.parentId] : null;
        if (!parent) return;
        var x1 = toX(parent.x + parent.w / 2);
        var y1 = toY(parent.y + parent.h / 2);
        var x2 = toX(box.x + box.w / 2);
        var y2 = toY(box.y + box.h / 2);
        var mid = (x1 + x2) / 2;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.bezierCurveTo(mid, y1, mid, y2, x2, y2);
        ctx.strokeStyle = hexToRgba(box.color || (dark ? '#b7aa9a' : '#8c7f72'), dark ? 0.42 : 0.34);
        ctx.lineWidth = 0.8;
        ctx.stroke();
      });

      var viewport = A.minimapViewport(chartRect, boxes[0], contentScale);
      if (viewport) {
        var vx = toX(viewport.x), vy = toY(viewport.y);
        var vw = viewport.w * fit.scale, vh = viewport.h * fit.scale;
        ctx.fillStyle = hexToRgba(clay, dark ? 0.12 : 0.075);
        ctx.fillRect(vx, vy, vw, vh);
      }

      boxes.forEach(function (box) {
        var bx = toX(box.x), by = toY(box.y);
        var bw = Math.max(1.25, box.w * fit.scale);
        var bh = Math.max(1.25, box.h * fit.scale);
        if (box.depth === 0) ctx.fillStyle = clay;
        else if (box.depth === 1) ctx.fillStyle = hexToRgba(box.color || clay, dark ? 0.8 : 0.7);
        else ctx.fillStyle = dark ? 'rgba(237,231,220,.42)' : 'rgba(85,73,61,.28)';
        ctx.fillRect(bx, by, bw, bh);
      });

      if (viewport) {
        var borderX = toX(viewport.x), borderY = toY(viewport.y);
        var borderW = viewport.w * fit.scale, borderH = viewport.h * fit.scale;
        ctx.fillStyle = hexToRgba(clay, dark ? 0.045 : 0.025);
        ctx.fillRect(borderX, borderY, borderW, borderH);
        ctx.strokeStyle = clay;
        ctx.lineWidth = 1.4;
        ctx.strokeRect(borderX, borderY, borderW, borderH);
      }
      ctx.restore();

      state.miniBox = {
        ox: fit.ox,
        oy: fit.oy,
        scale: fit.scale,
        minX: fit.minX,
        minY: fit.minY,
        maxX: fit.minX + fit.contentW,
        maxY: fit.minY + fit.contentH,
        contentScale: contentScale,
        reference: {
          x: boxes[0].x,
          y: boxes[0].y,
          screenX: boxes[0].screenX,
          screenY: boxes[0].screenY
        }
      };
    }, 32);
  }

  function minimapJump(e) {
    var box = state.miniBox;
    if (!box || !state.mei) return;
    var rect = els.minimap.getBoundingClientRect();
    var chartRect = els.chart.getBoundingClientRect();
    var x = e.clientX - rect.left;
    var y = e.clientY - rect.top;
    var contentX = Math.max(box.minX, Math.min(box.maxX, (x - box.ox) / box.scale + box.minX));
    var contentY = Math.max(box.minY, Math.min(box.maxY, (y - box.oy) / box.scale + box.minY));
    var currentScale = state.mei.scaleVal || box.contentScale || 1;
    var screenX = box.reference.screenX + (contentX - box.reference.x) * currentScale;
    var screenY = box.reference.screenY + (contentY - box.reference.y) * currentScale;
    var dx = chartRect.left + chartRect.width / 2 - screenX;
    var dy = chartRect.top + chartRect.height / 2 - screenY;
    state.mei.move(dx, dy);
    // Keep drag mapping current between the throttled minimap redraws.
    box.reference.screenX += dx;
    box.reference.screenY += dy;
  }

  function bindMinimap() {
    if (!els.minimap) return;
    var dragging = false;
    function start(e) {
      if (e.button != null && e.button !== 0) return;
      dragging = true;
      els.minimap.classList.add('is-dragging');
      try { els.minimap.setPointerCapture(e.pointerId); } catch (err) {}
      minimapJump(e);
    }
    function move(e) {
      if (!dragging) return;
      minimapJump(e);
    }
    function stop(e) {
      if (!dragging) return;
      dragging = false;
      els.minimap.classList.remove('is-dragging');
      try { els.minimap.releasePointerCapture(e.pointerId); } catch (err) {}
      updateMinimap();
    }
    els.minimap.addEventListener('pointerdown', start);
    els.minimap.addEventListener('pointermove', move);
    els.minimap.addEventListener('pointerup', stop);
    els.minimap.addEventListener('pointercancel', stop);
  }

  // ── hover peek ───────────────────────────────────────────────

  function bindPeek() {
    if (!els.chart) return;
    els.chart.addEventListener('pointerover', function (e) {
      var tpc = e.target.closest && e.target.closest('.me-tpc');
      if (!tpc) return;
      var id = (tpc.getAttribute('data-nodeid') || '').slice(2);
      if (!id) return;
      clearTimeout(peekTimer);
      peekTimer = setTimeout(function () { showPeek(id, tpc); }, 350);
    });
    els.chart.addEventListener('pointerout', function (e) {
      var tpc = e.target.closest && e.target.closest('.me-tpc');
      if (!tpc) return;
      clearTimeout(peekTimer);
      hidePeek();
    });
    els.chart.addEventListener('pointerdown', function () { clearTimeout(peekTimer); hidePeek(); });
  }

  function showPeek(id, tpc) {
    var node = state.adapter.getNode(id);
    if (!node || state.editing) return;
    if (els.searchPanel && els.searchPanel.classList.contains('open')) return;
    var light = state.adapter.lightById.get(id) || {};
    var peek = $('ktmPeek');
    if (!peek) {
      peek = document.createElement('div');
      peek.id = 'ktmPeek';
      peek.className = 'ktm-peek';
      document.body.appendChild(peek);
    }
    var summary = light.summary ? String(light.summary).replace(/\s+/g, ' ').slice(0, 160) : '';
    var exams = Array.isArray(light.exam_points) ? light.exam_points.slice(0, 3) : [];
    var html = '<div class="ktm-peek-title">' + esc(node.title) + '</div>';
    if (summary) html += '<div class="ktm-peek-summary">' + esc(summary) + (summary.length >= 160 ? '…' : '') + '</div>';
    if (exams.length) {
      html += '<div class="ktm-peek-exams">' + exams.map(function (ep) {
        return '<span><i>' + esc(ep.frequency || '') + '</i>' + esc(ep.point || '') + '</span>';
      }).join('') + '</div>';
    }
    if (node.children && node.children.length) html += '<div class="ktm-peek-foot">' + node.children.length + ' 个子节点 · 点击查看详情</div>';
    peek.innerHTML = html;
    peek.classList.add('show');
    var r = tpc.getBoundingClientRect();
    var pw = 320, ph = peek.offsetHeight || 120;
    var left = Math.min(window.innerWidth - pw - 12, Math.max(12, r.left + r.width / 2 - pw / 2));
    var top = r.bottom + 10;
    if (top + ph > window.innerHeight - 12) top = r.top - ph - 10;
    peek.style.left = left + 'px';
    peek.style.top = Math.max(12, top) + 'px';
  }

  function hidePeek() {
    var peek = $('ktmPeek');
    if (peek) peek.classList.remove('show');
  }

  // ── context menu ─────────────────────────────────────────────

  function closeContextMenu() {
    if (ctxMenu && ctxMenu.parentNode) ctxMenu.parentNode.removeChild(ctxMenu);
    ctxMenu = null;
  }

  function openContextMenu(x, y, id) {
    closeContextMenu();
    var node = state.adapter.getNode(id);
    if (!node) return;
    var items = [
      { key: 'detail', label: '查看详情' },
      { key: 'focus', label: state.focusId === id ? '取消聚焦' : '聚焦此分支' },
      { key: 'expand', label: '展开/收起' },
      { key: 'copy', label: '复制路径' },
      { key: 'ask', label: '在此提问' },
      { key: 'review', label: '复习此节点' }
    ];
    if (state.editing) {
      items = [
        { key: 'rename', label: '重命名', keyHint: 'F2' },
        { key: 'addChild', label: '添加子节点', keyHint: 'Tab' },
        { key: 'addSibling', label: '添加同级', keyHint: 'Enter' },
        { key: 'content', label: '编辑名称与内容' },
        { key: 'delete', label: '删除', danger: true, keyHint: 'Delete' },
        { sep: true }
      ].concat(items);
    }
    if (id === state.adapter.displayRoot.id) {
      items = items.filter(function (item) {
        return !item.key || ['detail', 'focus', 'expand', 'copy', 'ask', 'review'].indexOf(item.key) >= 0;
      });
    }
    ctxMenu = document.createElement('div');
    ctxMenu.className = 'ktm-context-menu';
    ctxMenu.innerHTML = items.map(function (item) {
      if (item.sep) return '<div class="ktm-ctx-sep"></div>';
      return '<button type="button" data-ktm-ctx="' + item.key + '"' + (item.danger ? ' class="is-danger"' : '') + '>'
        + '<span>' + esc(item.label) + '</span>' + (item.keyHint ? '<kbd>' + esc(item.keyHint) + '</kbd>' : '') + '</button>';
    }).join('');
    document.body.appendChild(ctxMenu);
    var rect = ctxMenu.getBoundingClientRect();
    ctxMenu.style.left = Math.min(window.innerWidth - rect.width - 8, Math.max(8, x)) + 'px';
    ctxMenu.style.top = Math.min(window.innerHeight - rect.height - 8, Math.max(8, y)) + 'px';
    ctxMenu.querySelectorAll('[data-ktm-ctx]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var action = btn.getAttribute('data-ktm-ctx');
        closeContextMenu();
        handleContextAction(action, id);
      });
    });
  }

  function handleContextAction(action, id) {
    var topic = findTopic(id);
    if (action === 'detail') { selectById(id, { center: true }); return; }
    if (action === 'focus') {
      if (state.focusId === id) clearSpotlight();
      else spotlight(id);
      return;
    }
    if (action === 'expand') {
      if (topic && state.mei) state.mei.expandNode(topic);
      return;
    }
    if (action === 'copy') { copyPath(id); return; }
    if (action === 'ask') { askAboutNode(id); return; }
    if (action === 'review') { reviewNode(id); return; }
    if (!state.editing) return;
    if (action === 'rename') {
      if (topic && state.mei) state.mei.beginEdit(topic);
    } else if (action === 'addChild') {
      if (topic && state.mei) state.mei.addChild(topic);
    } else if (action === 'addSibling') {
      if (topic && state.mei) state.mei.insertSibling('after', topic);
    } else if (action === 'delete') {
      if (topic && state.mei) {
        var count = A.countNodes(topic.nodeObj);
        if (count > 1 && !window.confirm('删除这个节点及其 ' + (count - 1) + ' 个子节点？')) return;
        state.mei.removeNodes([topic]);
      }
    } else if (action === 'content') {
      focusDetailField(id, 'title');
    }
  }

  function focusDetailField(id, fieldName) {
    if (!state.adapter.getNode(id)) return;
    selectById(id, { center: false });
    renderDetail(id, { preservePanels: true });
    var field = els.infoContent && els.infoContent.querySelector('[data-ktm-field="' + fieldName + '"]');
    if (field) {
      field.focus();
      if (field.select) field.select();
      if (field.scrollIntoView) field.scrollIntoView({ block: 'nearest' });
    } else if (fieldName === 'title' && state.adapter.displayRoot && id === state.adapter.displayRoot.id) {
      toast('根标题请到资料库重命名');
    } else if (!state.editing) {
      toast('进入编辑模式后可修改名称与内容');
    }
  }

  // ── keyboard ─────────────────────────────────────────────────

  function selectedNodeObj() {
    if (!state.selectedId) return null;
    return state.adapter.getNode(state.selectedId);
  }

  function siblingOf(node) {
    if (!node) return null;
    var parent = parentOf(node.id);
    if (!parent) return null;
    var siblings = parent.children || [];
    var index = siblings.findIndex(function (c) { return c.id === node.id; });
    return { siblings: siblings, index: index };
  }

  function parentOf(id) {
    var found = null;
    (function walk(node) {
      if (found || !node) return;
      (node.children || []).forEach(function (child) {
        if (child.id === id) { found = node; return; }
        walk(child);
      });
    })(state.adapter.displayRoot);
    return found;
  }

  function navigate(direction) {
    var node = selectedNodeObj();
    if (!node) {
      var first = state.adapter.displayRoot;
      if (first) selectById(first.id, { center: true });
      return;
    }
    var target = null;
    if (direction === 'left') target = parentOf(node.id);
    else if (direction === 'right') {
      if (node.children && node.children.length) target = node.children[0];
      else {
        var sib = siblingOf(node);
        if (sib && sib.index < sib.siblings.length - 1) target = sib.siblings[sib.index + 1];
      }
    } else if (direction === 'up' || direction === 'down') {
      var s = siblingOf(node);
      if (s) {
        var next = direction === 'up' ? s.index - 1 : s.index + 1;
        if (next >= 0 && next < s.siblings.length) target = s.siblings[next];
      }
    }
    if (target) selectById(target.id, { center: true });
  }

  function handleEscape() {
    closeContextMenu();
    hidePeek();
    if (helpOpen) { closeHelp(); return true; }
    if (els.searchPanel && els.searchPanel.classList.contains('open')) { closeSearch(); return true; }
    if (els.infoPanel && els.infoPanel.classList.contains('show')) { closeDetail(); return true; }
    if (state && state.minimapOn) { toggleMinimap(false); return true; }
    if (state && state.focusId) { clearSpotlight(); return true; }
    return false;
  }

  function bindKeyboard() {
    if (!els.chart) return;
    els.chart.setAttribute('tabindex', '0');
    els.chart.addEventListener('keydown', function (e) {
      var tag = (e.target && e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || (e.target && e.target.isContentEditable)) return;
      var mod = e.metaKey || e.ctrlKey;
      var key = e.key;
      if (mod && key.toLowerCase() === 'f') { e.preventDefault(); openSearch(); return; }
      if (!mod && key === '/') { e.preventDefault(); openSearch(); return; }
      if (key === 'Escape') {
        e.preventDefault();
        handleEscape();
        return;
      }
      if (mod && key.toLowerCase() === 'z') {
        if (!state.editing || !state.mei) return;
        e.preventDefault();
        if (e.shiftKey) state.mei.redo(); else state.mei.undo();
        syncAdapterLive();
        return;
      }
      if (mod && key.toLowerCase() === 'y') {
        if (!state.editing || !state.mei) return;
        e.preventDefault(); state.mei.redo(); syncAdapterLive(); return;
      }
      if (key === '?') { e.preventDefault(); toggleHelp(); return; }
      if (key === 'f' && !mod) { e.preventDefault(); fit(); return; }
      if (key.toLowerCase() === 'm' && !mod) { e.preventDefault(); toggleMinimap(); return; }
      if (key === '0' && !mod) { e.preventDefault(); zoomTo(1); return; }
      if ((key === '+' || key === '=') && !mod) { e.preventDefault(); zoomTo((state.mei.scaleVal || 1) * 1.12); return; }
      if (key === '-' && !mod) { e.preventDefault(); zoomTo((state.mei.scaleVal || 1) / 1.12); return; }
      if (key === 'ArrowLeft') { e.preventDefault(); navigate('left'); return; }
      if (key === 'ArrowRight') { e.preventDefault(); navigate('right'); return; }
      if (key === 'ArrowUp') { e.preventDefault(); navigate('up'); return; }
      if (key === 'ArrowDown') { e.preventDefault(); navigate('down'); return; }
      if (key === 'Enter') {
        e.preventDefault();
        var topic = findTopic(state.selectedId);
        if (!topic || !state.mei) return;
        if (state.editing) state.mei.insertSibling('after', topic);
        else state.mei.expandNode(topic);
        return;
      }
      if (key === 'Tab' && state.editing) {
        e.preventDefault();
        var t = findTopic(state.selectedId);
        if (t && state.mei) state.mei.addChild(t);
        return;
      }
      if (key === 'F2' && state.editing) {
        e.preventDefault();
        var t2 = findTopic(state.selectedId);
        if (t2 && state.mei) state.mei.beginEdit(t2);
        return;
      }
      if ((key === 'Delete' || key === 'Backspace') && state.editing) {
        e.preventDefault();
        var t3 = findTopic(state.selectedId);
        if (t3 && state.mei) {
          var count = A.countNodes(t3.nodeObj);
          if (count > 1 && !window.confirm('删除这个节点及其 ' + (count - 1) + ' 个子节点？')) return;
          state.mei.removeNodes([t3]);
        }
        return;
      }
      if (key === 'i' && !mod) { e.preventDefault(); if (state.selectedId) renderDetail(state.selectedId); return; }
    });
  }

  function toggleHelp() {
    if (helpOpen) { closeHelp(); return; }
    helpOpen = true;
    var help = $('ktmHelp');
    if (!help) {
      help = document.createElement('div');
      help.id = 'ktmHelp';
      help.className = 'ktm-help';
      help.innerHTML = '<div class="ktm-help-card" role="dialog" aria-modal="true" aria-label="快捷键">'
        + '<div class="ktm-help-head"><b>键盘快捷键</b><button type="button" data-ktm-help-close aria-label="关闭">×</button></div>'
        + '<div class="ktm-help-grid">'
        + '<span>方向键</span><span>在节点间移动</span>'
        + '<span>Enter</span><span>只读：展开/收起；编辑：添加同级</span>'
        + '<span>Tab</span><span>编辑：添加子节点</span>'
        + '<span>F2 / 双击</span><span>编辑：重命名节点</span>'
        + '<span>详情面板</span><span>编辑：改名称 / 摘要 / 关键词 / 正文 / 笔记</span>'
        + '<span>Ctrl/Cmd+Enter</span><span>编辑：提交当前输入框</span>'
        + '<span>Ctrl/Cmd+Z</span><span>撤销（编辑模式）</span>'
        + '<span>Ctrl/Cmd+F 或 /</span><span>搜索</span>'
        + '<span>F</span><span>适应窗口</span>'
        + '<span>M</span><span>显示 / 隐藏小地图</span>'
        + '<span>0 / + / -</span><span>重置 / 放大 / 缩小</span>'
        + '<span>Esc</span><span>关闭面板 / 取消聚焦</span>'
        + '</div></div>';
      document.body.appendChild(help);
      help.querySelector('[data-ktm-help-close]').addEventListener('click', closeHelp);
      help.addEventListener('click', function (e) { if (e.target === help) closeHelp(); });
    }
    help.classList.add('show');
  }
  function closeHelp() {
    helpOpen = false;
    var help = $('ktmHelp');
    if (help) help.classList.remove('show');
  }

  // ── ARIA ─────────────────────────────────────────────────────

  function decorateAria() {
    if (!els.chart || !state.mei) return;
    els.chart.setAttribute('role', 'tree');
    els.chart.setAttribute('aria-label', '知识树');
    Array.prototype.forEach.call(els.chart.querySelectorAll('.me-tpc'), function (tpc) {
      var id = (tpc.getAttribute('data-nodeid') || '').slice(2);
      var node = state.adapter.getNode(id);
      if (!node) return;
      var depth = state.adapter.getPath(id).length - 1;
      var meNode = A.findMeNode(state.mei.nodeData, id);
      tpc.setAttribute('role', 'treeitem');
      tpc.setAttribute('aria-level', String(depth + 1));
      tpc.setAttribute('aria-label', node.title || '未命名');
      if (node.children && node.children.length) tpc.setAttribute('aria-expanded', String(!meNode || meNode.expanded !== false));
      tpc.setAttribute('aria-selected', String(id === state.selectedId));
    });
  }

  // ── postMessage / events ─────────────────────────────────────

  function postSelection() {
    if (!state.embed || !window.parent || window.parent === window) return;
    try {
      window.parent.postMessage({
        type: 'kt-map-selection',
        file: state.key,
        chapter: state.selectedId ? chapterOf(state.selectedId) : '',
        nodeId: state.selectedId || '',
        path: state.selectedId ? state.adapter.getPath(state.selectedId) : []
      }, window.location.origin);
    } catch (err) {}
  }

  function bindWindowEvents() {
    window.addEventListener('message', function (event) {
      if (event.origin !== window.location.origin || !event.data) return;
      if (event.data.type === 'kt-map-focus') {
        if (event.data.nodeId) selectById(event.data.nodeId, { center: true, spotlight: true });
        else if (event.data.chapter) focusByTitle(event.data.chapter);
      } else if (event.data.type === 'kt-resize') {
        updateMinimap();
      }
    });
    window.addEventListener('kt-theme-change', setTheme);
    window.addEventListener('resize', function () { updateMinimap(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        handleEscape();
        return;
      }
      if (e.defaultPrevented) return;
      var tag = (e.target && e.target.tagName || '').toLowerCase();
      var typing = tag === 'input' || tag === 'textarea' || (e.target && e.target.isContentEditable);
      var mod = e.metaKey || e.ctrlKey;
      if (!mod || typing) return;
      var key = e.key.toLowerCase();
      if (key === 'z' && state && state.editing && state.mei) {
        e.preventDefault();
        if (e.shiftKey) state.mei.redo(); else state.mei.undo();
        syncAdapterLive();
      } else if (key === 'y' && state && state.editing && state.mei) {
        e.preventDefault();
        state.mei.redo();
        syncAdapterLive();
      } else if (key === 'f') {
        e.preventDefault();
        openSearch();
      }
    });
  }

  function focusByTitle(title) {
    if (!title) return;
    var found = null;
    (function walk(node) {
      if (found) return;
      if ((node.title || node.name) === title) { found = node; return; }
      (node.children || []).forEach(walk);
    })(state.adapter.displayRoot);
    if (!found) return;
    var topic = findTopic(found.id);
    if (topic && state.mei) state.mei.expandNode(topic, true);
    selectById(found.id, { center: true, spotlight: true });
  }

  // ── toast ────────────────────────────────────────────────────

  function toast(message) {
    if (global.KT && KT.showToast) { KT.showToast(message); return; }
    var el = $('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove('show'); }, 2200);
  }

  // ── edit mode / save (M2, wired here for the controller) ─────

  function setEditable(enabled) {
    if (state.readonly && enabled) { toast('当前资料为只读'); return; }
    var wasEditing = state.editing;
    if (!enabled && wasEditing) flushSave();
    state.editing = !!enabled;
    if (state.mei) state.mei.editable = !!enabled;
    document.body.classList.toggle('ktm-editing', state.editing);
    var editBtn = $('btnEdit');
    if (editBtn) {
      editBtn.classList.toggle('is-active', state.editing);
      editBtn.classList.remove('is-primary');
      editBtn.setAttribute('aria-pressed', String(state.editing));
      editBtn.setAttribute('aria-label', state.editing ? '结束编辑' : '进入编辑模式');
      editBtn.title = state.editing ? '结束编辑并保存（当前可改名、改内容、增删与拖拽）' : '编辑模式：改名 / 改内容 / 增删 / 拖拽 / 撤销';
      var label = editBtn.querySelector('.label');
      if (label) label.textContent = state.editing ? '编辑中' : '编辑';
    }
    var editBar = $('ktmEditBar');
    if (editBar) editBar.hidden = !state.editing;
    if (state.editing) {
      toast('编辑模式：可改名、改内容、增删与拖拽；Ctrl+Z 撤销');
      if (state.selectedId) renderDetail(state.selectedId, { preservePanels: true });
      saveView();
      warnReviewImpact();
    } else if (state.selectedId) {
      renderDetail(state.selectedId, { preservePanels: true, onlyIfOpen: true });
    }
    refreshOutline();
    decorateAria();
  }

  function warnReviewImpact() {
    if (state.reviewWarned || !state.key || state.offline) return;
    state.reviewWarned = true;
    fetch((state.apiBase || '') + '/api/review/progress?file=' + encodeURIComponent(state.key))
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data) return;
        var count = Number(data.unit_count) || 0;
        if (count > 0) {
          toast('提示：重命名 / 移动 / 删除节点可能使该节点的复习记录失去关联');
        }
      })
      .catch(function () {});
  }

  function scheduleSave() {
    if (!state.editing || state.readonly || state.saveConflict) return;
    clearTimeout(saveTimer);
    setSaveState('dirty');
    saveTimer = setTimeout(flushSave, 800);
  }

  function flushSave() {
    if (!state.editing || state.readonly || !state.mei) return;
    if (state.saving) { state.pendingSave = true; return; }
    clearTimeout(saveTimer);
    state.saving = true;
    setSaveState('saving');
    var tree;
    try { tree = state.adapter.fromMindElixirData(state.mei.getData()); }
    catch (err) { state.saving = false; setSaveState('error'); console.error(err); return; }
    var serialized = JSON.stringify(tree);
    if (state.lastSavedJson && serialized === state.lastSavedJson) {
      state.saving = false;
      setSaveState('saved');
      return;
    }
    fetch((state.apiBase || '') + '/api/tree/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: state.key, base_rev: state.rev, tree: tree, reason: 'edit' })
    }).then(function (res) {
      return res.json().then(function (body) { return { ok: res.ok, status: res.status, body: body }; });
    }).then(function (result) {
      state.saving = false;
      if (!result.ok) {
        if (result.status === 409) {
          state.saveConflict = true;
          setSaveState('conflict');
          if (window.confirm('保存冲突：这份资料已在别处修改。\n\n确定：重新加载（会丢弃本地修改）\n取消：保留本地修改，不再自动保存')) {
            window.location.reload();
          }
        } else {
          setSaveState('error');
          toast('保存失败：' + (result.body && result.body.error || result.status));
        }
        return;
      }
      state.rev = result.body.rev || state.rev;
      state.lastSavedJson = serialized;
      state.saveConflict = false;
      setSaveState('saved');
      emit('save', result.body);
      if (state.pendingSave) {
        state.pendingSave = false;
        setTimeout(flushSave, 40);
      }
    }).catch(function (err) {
      state.saving = false;
      setSaveState('error');
      toast('保存失败：' + (err && err.message || err));
    });
  }

  function setSaveState(status) {
    state.saveState = status;
    var el = $('ktmSaveStatus');
    if (!el) return;
    var labels = { dirty: '未保存', saving: '保存中…', saved: '已保存', error: '保存失败', conflict: '保存冲突' };
    el.textContent = labels[status] || '';
    el.className = 'ktm-save-status is-' + status;
  }

  function openOutline(el) {
    if (!el || !state.mei) return;
    if (!global.KTME || !global.KTME.Outliner) { toast('大纲组件未加载'); return; }
    if (state.outlineEl === el && state.outliner) return;
    try {
      el.innerHTML = '';
      el.classList.toggle('dark', themeMode() === 'dark');
      state.outliner = new global.KTME.Outliner({
        el: el,
        mei: state.mei,
        readonly: !state.editing,
        fileName: state.adapter.title || '知识树',
        onChange: function () {
          syncAdapterLive();
          if (state.editing) scheduleSave();
        }
      });
      state.outlineEl = el;
    } catch (err) {
      console.error(err);
      el.innerHTML = '<div class="ktm-empty">大纲加载失败：' + esc(err && err.message || err) + '</div>';
    }
  }

  function refreshOutline() {
    if (state.outlineEl) {
      var el = state.outlineEl;
      try { if (state.outliner && state.outliner.destroy) state.outliner.destroy(); } catch (err) {}
      state.outliner = null;
      state.outlineEl = null;
      openOutline(el);
    }
  }

  function toggleHelpPublic() {
    toggleHelp();
  }

  function exportPNG() {
    if (!state.mei || !global.ExportMindmap || !global.ExportMindmap.exportImage) {
      return Promise.reject(new Error('PNG 导出组件未加载'));
    }
    return global.ExportMindmap.exportImage(state.mei, 'png', { watermarkEnabled: false })
      .then(function (url) {
        var a = document.createElement('a');
        a.href = url;
        a.download = sanitizeFileName(state.adapter.title) + '_导图.png';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        toast('已导出 PNG 导图');
        return url;
      });
  }

  function sanitizeFileName(name) {
    return String(name || '知识树').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 60) || '知识树';
  }

  // ── init ─────────────────────────────────────────────────────

  function init(opts) {
    opts = opts || {};
    if (!global.KTME || !global.KTME.MindElixir) throw new Error('Mind Elixir 未加载');
    if (!A) throw new Error('KTMapAdapter 未加载');
    var tree = opts.tree;
    var adapter = new A.MapAdapter(tree);
    var saved = loadViewFor(opts.key);
    state = {
      adapter: adapter,
      mei: null,
      key: opts.key || '',
      rev: opts.rev || '',
      apiBase: opts.apiBase || '',
      readonly: !!opts.readonly,
      embed: !!opts.embed,
      offline: !!opts.offline,
      layout: saved && saved.layout ? A.legacyLayoutName(saved.layout) : 'SIDE',
      expandLevel: saved && saved.expandLevel ? saved.expandLevel : adaptiveExpandLevel(adapter),
      overrides: saved && saved.overrides && typeof saved.overrides === 'object' ? saved.overrides : {},
      selectedId: saved && saved.selectedId || '',
      focusId: saved && saved.focusId || '',
      editing: false,
      saving: false,
      pendingSave: false,
      saveState: 'saved',
      lastSavedJson: '',
      saveConflict: false,
      reviewWarned: false,
      minimapOn: false,
      miniBox: null,
      chart: null
    };
    // `loadView` uses state.key; re-read with the local state now.
    els = {
      chart: $('chart'),
      chartContainer: $('chartContainer'),
      infoPanel: $('infoPanel'),
      infoContent: $('infoContent'),
      breadcrumb: $('breadcrumb'),
      searchPanel: $('searchPanel'),
      searchInput: $('searchInput'),
      searchResults: $('searchResults'),
      searchStatus: $('searchStatus'),
      chapterSidebar: $('chapterSidebar'),
      chapterList: $('chapterList'),
      zoomRange: $('zoomRange'),
      zoomLabel: $('zoomLabel'),
      minimap: $('minimap')
    };
    if (!els.chart) throw new Error('缺少 #chart 容器');
    state.chart = els.chart;

    var theme = themeObject();
    var ME = global.KTME.MindElixir;
    var mei = new ME({
      el: els.chart,
      allowUndo: true,
      editable: false,
      keypress: false,
      contextMenu: false,
      toolBar: false,
      scaleMin: 0.12,
      scaleMax: 2.5,
      alignment: 'root',
      newTopicName: '新节点',
      theme: theme,
      mouseSelectionButton: 2,
      before: {
        addChild: function () { return state.editing; },
        insertSibling: function () { return state.editing; },
        insertParent: function () { return state.editing; },
        removeNodes: function () { return state.editing; },
        moveNodesIn: function () { return state.editing; },
        moveNodesBefore: function () { return state.editing; },
        moveNodesAfter: function () { return state.editing; },
        moveUpNode: function () { return state.editing; },
        moveDownNode: function () { return state.editing; },
        beginEdit: function (el) {
          return state.editing && (!el || !el.nodeObj || !!el.nodeObj.parent);
        },
        setNodeTopic: function (el) {
          return state.editing && (!el || !el.nodeObj || !!el.nodeObj.parent);
        }
      },
      handleWheel: function (e) {
        e.preventDefault();
        if (e.ctrlKey || e.metaKey) {
          var delta = e.deltaY > 0 ? 0.92 : 1.08;
          var rect = els.chart.getBoundingClientRect();
          state.mei.scale((state.mei.scaleVal || 1) * delta, {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top
          });
          updateZoomUI();
        } else {
          state.mei.move(0, -e.deltaY);
        }
      }
    });
    state.mei = mei;

    var data = buildData();
    var initPromise;
    try { initPromise = mei.init(data); }
    catch (err) { initPromise = Promise.reject(err); }
    var timeout = new Promise(function (_, reject) {
      setTimeout(function () { reject(new Error('导图引擎初始化超时')); }, 3000);
    });

    return Promise.race([initPromise, timeout]).then(function () {
      // 引擎 init() 会对 data（含 theme）做 JSON 序列化，theme 上的函数会丢失，
      // 实例的 generateSubBranch 被重置为内置版本；这里补回并重画连线。
      mei.generateSubBranch = generateSubBranch;
      if (typeof mei.linkDiv === 'function') {
        try { mei.linkDiv(); } catch (err) {}
      }
      bindEngineEvents();
      bindDomEvents();
      bindKeyboard();
      bindPeek();
      bindMinimap();
      bindWindowEvents();
      if (saved && saved.scale) {
        try { mei.scale(saved.scale); } catch (err) {}
      } else {
        try { mei.scaleFit(); } catch (err) {}
      }
      if (opts.nodeId) selectById(opts.nodeId, { center: true, spotlight: true });
      else if (opts.chapter) focusByTitle(opts.chapter);
      else if (state.selectedId) selectById(state.selectedId, { center: true, spotlight: false });
      else {
        renderChapterList();
        updateBreadcrumb();
      }
      updateZoomUI();
      decorateAria();
      try {
        state.lastSavedJson = JSON.stringify(state.adapter.fromMindElixirData(mei.getData()));
      } catch (err) {}
      if (state.embed && window.parent && window.parent !== window) {
        try { window.parent.postMessage({ type: 'kt-mindmap-ready' }, window.location.origin); } catch (err) {}
      }
      emit('ready', api);
      return api;
    }).catch(function (err) {
      showFallback(err);
      throw err;
    });
  }

  function syncAdapterLive() {
    if (!state || !state.mei) return;
    try {
      state.adapter.syncFromMindElixir(state.mei.getData());
      if (state.selectedId && !state.adapter.getNode(state.selectedId)) {
        state.selectedId = '';
        closeDetail();
        clearSpotlight();
        updateBreadcrumb();
      }
      if (state.focusId && !state.adapter.getNode(state.focusId)) clearSpotlight();
      renderChapterList();
      updateChapterActive();
      if (state.selectedId) renderDetail(state.selectedId, { preservePanels: true, onlyIfOpen: true, skipIfTyping: true });
      updateBreadcrumb();
      emit('change', state.adapter);
    } catch (err) {
      console.error('[KTMap] adapter sync failed', err);
    }
  }

  function bindEngineEvents() {
    var mei = state.mei;
    function syncLive() { syncAdapterLive(); }
    function handleSelectedNode(node) {
      if (!node || !node.id) return;
      state.selectedId = node.id;
      renderDetail(node.id);
      updateBreadcrumb();
      updateChapterActive();
      postSelection();
      saveView();
      decorateAria();
      emit('select', state.adapter.getNode(node.id));
    }
    mei.bus.addListener('selectNodes', function (nodes) {
      if (!nodes || !nodes.length) return;
      handleSelectedNode(nodes[0]);
    });
    mei.bus.addListener('selectNewNode', handleSelectedNode);
    mei.bus.addListener('unselectNodes', function () {
      decorateAria();
    });
    mei.bus.addListener('operation', function (op) {
      if (op && (op.name === 'expandNode' || op.name === 'collapseNode') && op.target && op.target.id) {
        state.overrides[op.target.id] = op.target.expanded !== false;
      }
      syncLive();
      if (state.editing && op && !op.silent && op.name !== 'expandNode' && op.name !== 'collapseNode') {
        scheduleSave();
      }
      decorateAria();
    });
    mei.bus.addListener('refresh', function () {
      syncLive();
      updateZoomUI();
      decorateAria();
      if (state.editing) scheduleSave();
    });
    mei.bus.addListener('move', function () { updateMinimap(); });
    mei.bus.addListener('scale', function () { updateZoomUI(); });
  }

  function bindDomEvents() {
    var container = els.chartContainer;
    if (container) {
      var downPoint = null;
      container.addEventListener('pointerdown', function (e) {
        closeContextMenu();
        if (e.button === 0) downPoint = { x: e.clientX, y: e.clientY, target: e.target };
        else downPoint = null;
      });
      container.addEventListener('pointerup', function (e) {
        if (!downPoint || e.button !== 0) { downPoint = null; return; }
        var moved = Math.abs(e.clientX - downPoint.x) + Math.abs(e.clientY - downPoint.y);
        var target = downPoint.target;
        downPoint = null;
        if (moved > 6) return;
        if (!target || !target.closest) return;
        if (target.closest('.me-epd') || target.closest('.ktm-context-menu')) return;
        var tpc = target.closest('.me-tpc');
        if (!tpc) return;
        clearSpotlight();
        state.mei.selectNode(tpc);
        els.chart.focus();
      });
      container.addEventListener('click', function (e) {
        if (e.target.closest && (e.target.closest('.me-tpc') || e.target.closest('.ktm-context-menu'))) return;
        closeDetail();
        clearSpotlight();
        els.chart.focus();
      });
      container.addEventListener('contextmenu', function (e) {
        var tpc = e.target.closest && e.target.closest('.me-tpc');
        if (!tpc) return;
        e.preventDefault();
        var id = (tpc.getAttribute('data-nodeid') || '').slice(2);
        if (!id) return;
        state.mei.selectNode(tpc);
        openContextMenu(e.clientX, e.clientY, id);
      });
    }
    var searchInput = els.searchInput;
    if (searchInput) {
      var searchTimer = 0;
      searchInput.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () { renderSearch(searchInput.value.trim()); }, 120);
      });
      searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); moveSearchActive(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); moveSearchActive(-1); }
        else if (e.key === 'Enter') { e.preventDefault(); if (searchActive >= 0) jumpToResult(searchActive); else if (searchResults[0]) jumpToResult(0); }
        else if (e.key === 'Escape') { e.preventDefault(); closeSearch(); }
      });
    }
    var closeSearchBtn = $('searchCloseBtn');
    if (closeSearchBtn) closeSearchBtn.addEventListener('click', closeSearch);
    var drawerClose = $('chapterCloseBtn');
    if (drawerClose) drawerClose.addEventListener('click', function () { toggleDrawer(false); });
    if (els.zoomRange) {
      els.zoomRange.addEventListener('input', function () { zoomTo(Number(els.zoomRange.value) / 100); });
    }
    var zoomIn = $('zoomInBtn'), zoomOut = $('zoomOutBtn');
    if (zoomIn) zoomIn.addEventListener('click', function () { zoomTo((state.mei.scaleVal || 1) * 1.12); });
    if (zoomOut) zoomOut.addEventListener('click', function () { zoomTo((state.mei.scaleVal || 1) / 1.12); });
    var centerBtn = $('centerViewBtn');
    if (centerBtn) centerBtn.addEventListener('click', function () { state.mei.toCenter(); updateZoomUI(); saveView(); });
    var editBar = $('ktmEditBar');
    if (editBar) {
      editBar.querySelectorAll('[data-ktm-edit]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var action = btn.getAttribute('data-ktm-edit');
          if (action === 'done') { setEditable(false); return; }
          if (!state.editing || !state.mei) return;
          var topic = findTopic(state.selectedId);
          if (action === 'undo') { state.mei.undo(); syncAdapterLive(); return; }
          if (action === 'redo') { state.mei.redo(); syncAdapterLive(); return; }
          if (!topic) { toast('先选中一个节点'); return; }
          if (action === 'content') { focusDetailField(state.selectedId, 'content'); return; }
          if (action === 'addChild') state.mei.addChild(topic);
          else if (action === 'addSibling') state.mei.insertSibling('after', topic);
          else if (action === 'delete') {
            var count = A.countNodes(topic.nodeObj);
            if (count > 1 && !window.confirm('删除这个节点及其 ' + (count - 1) + ' 个子节点？')) return;
            state.mei.removeNodes([topic]);
          }
        });
      });
    }
    if (global.KT && KT.Panels) {
      KT.Panels.register('search', { isOpen: function () { return els.searchPanel && els.searchPanel.classList.contains('open'); }, close: closeSearch });
      KT.Panels.register('chapters', { isOpen: function () { return els.chapterSidebar && els.chapterSidebar.classList.contains('open'); }, close: function () { toggleDrawer(false); } });
      KT.Panels.register('info', { isOpen: function () { return els.infoPanel && els.infoPanel.classList.contains('show'); }, close: closeDetail });
    }
    renderChapterList();
  }

  function showFallback(err) {
    console.error('[KTMap] init failed', err);
    if (!els.chart) return;
    els.chart.innerHTML = '<div class="ktm-fallback"><h2>导图引擎加载失败</h2><p>'
      + esc(err && err.message || err) + '</p><p>已切换为大纲视图，可继续浏览；编辑暂不可用。</p>'
      + '<div class="ktm-fallback-outline">' + fallbackOutline(state.adapter.displayRoot) + '</div></div>';
  }

  function fallbackOutline(node) {
    if (!node) return '';
    var kids = node.children || [];
    return '<div class="ktm-fallback-node"><b>' + esc(node.title) + '</b>'
      + (kids.length ? '<div class="ktm-fallback-children">' + kids.map(fallbackOutline).join('') + '</div>' : '')
      + '</div>';
  }

  function loadViewFor(key) {
    if (!key) return null;
    try {
      var raw = localStorage.getItem('kt_map_view_' + key);
      return raw ? JSON.parse(raw) : null;
    } catch (err) { return null; }
  }

  // ── public API ───────────────────────────────────────────────

  var api = {
    init: init,
    on: on,
    off: off,
    getAdapter: function () { return state && state.adapter; },
    getMei: function () { return state && state.mei; },
    getState: function () { return state; },
    getDisplayTree: function () { return state && state.adapter.getDisplayTree(); },
    getExportTree: function (focusId) { return state && state.adapter.getExportTree(focusId); },
    getSelectedId: function () { return state && state.selectedId; },
    getFocusId: function () { return state && state.focusId; },
    getNode: function (id) { return state && state.adapter.getNode(id); },
    getPath: function (id) { return state && state.adapter.getPath(id); },
    select: selectById,
    focusByTitle: focusByTitle,
    spotlight: spotlight,
    clearSpotlight: clearSpotlight,
    setLayout: setLayout,
    setExpandLevel: setExpandLevel,
    expandAll: expandAll,
    collapseAll: collapseAll,
    fit: fit,
    zoomTo: zoomTo,
    setTheme: setTheme,
    search: function (query) { return state && state.adapter.search(query); },
    openSearch: openSearch,
    closeSearch: closeSearch,
    toggleSearch: toggleSearch,
    toggleDrawer: toggleDrawer,
    toggleMinimap: toggleMinimap,
    openOutline: openOutline,
    closeDetail: closeDetail,
    toggleHelp: toggleHelpPublic,
    setEditable: setEditable,
    isEditing: function () { return state && state.editing; },
    save: flushSave,
    exportPNG: exportPNG,
    sanitizeFileName: sanitizeFileName,
    undo: function () {
      if (state && state.mei && state.editing) { state.mei.undo(); syncAdapterLive(); }
    },
    redo: function () {
      if (state && state.mei && state.editing) { state.mei.redo(); syncAdapterLive(); }
    },
    toast: toast,
    destroy: function () {
      if (state && state.mei && state.mei.destroy) state.mei.destroy();
      state = null;
    }
  };

  global.KTMap = api;
})(window);
