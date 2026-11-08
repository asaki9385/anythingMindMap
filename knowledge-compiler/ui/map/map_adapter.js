/* ════════════════════════════════════════════════════════════════
   AnythingMindmap — knowledge tree ⇄ Mind Elixir adapter
   Pure data layer. No DOM, no engine, no network.
   Loaded as a classic script in the browser (window.KTMapAdapter)
   and via require() in Node tests.
   ════════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  var HEAVY_FIELDS = {
    content: true,
    tables: true,
    raw_content: true,
    rawContent: true,
    analysis: true,
    markdown: true
  };

  // View-only fields must never be persisted back into the knowledge tree.
  var VIEW_ONLY_FIELDS = {
    parent: true,
    style: true,
    tags: true,
    icons: true,
    branchColor: true,
    expanded: true,
    direction: true,
    dangerouslySetInnerHTML: true
  };

  var MAX_CONTENT_INDEX_CHARS = 2000;
  var MAX_SEARCH_RESULTS = 50;

  // ── small utilities ──────────────────────────────────────────

  function clone(value) {
    if (value == null) return value;
    return JSON.parse(JSON.stringify(value));
  }

  function normTitle(value) {
    return String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  }

  function isObject(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
  }

  function hashString(input) {
    // FNV-1a 32-bit, base36. Deterministic across reloads and cheap.
    var hash = 0x811c9dc5;
    var text = String(input);
    for (var i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(36);
  }

  function validId(value) {
    return typeof value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value);
  }

  function stableId(pathIndexes, title) {
    return 'n_' + hashString(pathIndexes.join('.') + '|' + normTitle(title));
  }

  function stripHtml(value) {
    return String(value == null ? '' : value)
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/&lt;/gi, '<')
      .replace(/&gt;/gi, '>')
      .replace(/&quot;/gi, '"')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function keywordText(keywords) {
    if (!Array.isArray(keywords)) return '';
    return keywords.map(function (kw) {
      if (kw && typeof kw === 'object') {
        return [kw.term, kw.context].filter(Boolean).join(' ');
      }
      return String(kw == null ? '' : kw);
    }).filter(Boolean).join(' ');
  }

  function examText(points) {
    if (!Array.isArray(points)) return '';
    return points.map(function (ep) {
      if (ep && typeof ep === 'object') {
        return [ep.point, ep.type, ep.frequency].filter(Boolean).join(' ');
      }
      return String(ep == null ? '' : ep);
    }).filter(Boolean).join(' ');
  }

  function captionText(captions) {
    if (!Array.isArray(captions)) return '';
    return captions.map(function (cap) {
      if (cap && typeof cap === 'object') return [cap.label, cap.text].filter(Boolean).join(' ');
      return String(cap == null ? '' : cap);
    }).filter(Boolean).join(' ');
  }

  function hasChildren(node) {
    return !!(node && Array.isArray(node.children) && node.children.length);
  }

  function isPromotable(root) {
    var top = root && Array.isArray(root.children) ? root.children : [];
    return top.length === 1 && hasChildren(top[0]);
  }

  // ── Mind Elixir data helpers (pure) ──────────────────────────

  function walkMe(node, visitor, depth, parent) {
    if (!node) return;
    visitor(node, depth || 0, parent || null);
    var kids = Array.isArray(node.children) ? node.children : [];
    for (var i = 0; i < kids.length; i++) walkMe(kids[i], visitor, (depth || 0) + 1, node);
  }

  function countNodes(node) {
    var count = 0;
    walkMe(node, function () { count++; });
    return count;
  }

  function findMeNode(root, id) {
    var found = null;
    walkMe(root, function (node) {
      if (!found && node.id === id) found = node;
    });
    return found;
  }

  function findMePath(root, id) {
    var found = null;
    (function walk(node, path) {
      if (found || !node) return;
      var next = path.concat([node]);
      if (node.id === id) { found = next; return; }
      var kids = Array.isArray(node.children) ? node.children : [];
      for (var i = 0; i < kids.length && !found; i++) walk(kids[i], next);
    })(root, []);
    return found;
  }

  function applyExpandLevel(root, level, overrides) {
    var limit = Number(level);
    if (!isFinite(limit)) limit = 2;
    overrides = overrides || {};
    walkMe(root, function (node, depth) {
      var has = Object.prototype.hasOwnProperty.call(overrides, node.id);
      if (has) node.expanded = !!overrides[node.id];
      else node.expanded = depth < limit;
    });
    // Root is always expanded.
    if (root) root.expanded = true;
  }

  function expandAncestors(root, id) {
    var path = findMePath(root, id);
    if (!path) return false;
    for (var i = 0; i < path.length - 1; i++) path[i].expanded = true;
    return true;
  }

  function branchWeight(node) {
    // Leaf count is a better proxy for rendered height than total node count.
    if (!hasChildren(node)) return 1;
    var total = 0;
    for (var i = 0; i < node.children.length; i++) total += branchWeight(node.children[i]);
    return Math.max(1, total);
  }

  function assignBalancedDirections(root) {
    if (!root || !hasChildren(root)) return root;
    var kids = root.children;
    if (kids.length === 1) {
      kids[0].direction = 1; // RIGHT
      return root;
    }
    var weights = kids.map(branchWeight);
    var total = weights.reduce(function (a, b) { return a + b; }, 0);
    var best = 1;
    var bestDiff = Infinity;
    var running = 0;
    for (var i = 1; i < kids.length; i++) {
      running += weights[i - 1];
      var diff = Math.abs(running - (total - running));
      if (diff < bestDiff) { bestDiff = diff; best = i; }
    }
    for (var j = 0; j < kids.length; j++) {
      kids[j].direction = j < best ? 0 : 1; // LEFT then RIGHT
    }
    return root;
  }

  function legacyLayoutName(name) {
    switch (String(name || '')) {
      case 'LR_ALL': return 'RIGHT';
      case 'RL_ALL': return 'LEFT';
      case 'TB':
      case 'BT': return 'DOWN';
      case 'LR':
      case 'RL':
      case 'SIDE': return 'SIDE';
      case 'RIGHT':
      case 'LEFT':
      case 'DOWN': return String(name);
      default: return 'SIDE';
    }
  }

  function layoutDirection(layout) {
    switch (legacyLayoutName(layout)) {
      case 'RIGHT': return 1;
      case 'LEFT': return 0;
      case 'DOWN': return 3;
      default: return 2;
    }
  }

  // Fit a collection of content-space boxes into a mini canvas. Kept pure so
  // the minimap transform can be regression-tested without a browser layout.
  function fitBoxesToCanvas(boxes, width, height, padding) {
    boxes = Array.isArray(boxes) ? boxes : [];
    if (!boxes.length) return null;
    var pad = Math.max(0, Number(padding) || 0);
    var w = Math.max(1, Number(width) || 1);
    var h = Math.max(1, Number(height) || 1);
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    boxes.forEach(function (box) {
      var x = Number(box.x) || 0;
      var y = Number(box.y) || 0;
      var bw = Math.max(1, Number(box.w) || 1);
      var bh = Math.max(1, Number(box.h) || 1);
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x + bw);
      maxY = Math.max(maxY, y + bh);
    });
    if (!isFinite(minX)) return null;
    var contentW = Math.max(1, maxX - minX);
    var contentH = Math.max(1, maxY - minY);
    var innerW = Math.max(1, w - pad * 2);
    var innerH = Math.max(1, h - pad * 2);
    var scale = Math.min(innerW / contentW, innerH / contentH);
    var ox = pad + (innerW - contentW * scale) / 2 - minX * scale;
    var oy = pad + (innerH - contentH * scale) / 2 - minY * scale;
    return {
      minX: minX,
      minY: minY,
      contentW: contentW,
      contentH: contentH,
      scale: scale,
      ox: ox,
      oy: oy,
      width: w,
      height: h,
      padding: pad
    };
  }

  // Convert the visible chart viewport into the same content-space coordinate
  // system used by the minimap boxes. A reference topic gives the content →
  // screen transform, so this stays correct across pan, zoom and engine swaps.
  function minimapViewport(chartRect, referenceBox, contentScale) {
    if (!chartRect || !referenceBox) return null;
    var scale = Math.max(0.0001, Number(contentScale) || 1);
    var screenX = Number(referenceBox.screenX);
    var screenY = Number(referenceBox.screenY);
    if (!isFinite(screenX)) screenX = Number(referenceBox.left) || 0;
    if (!isFinite(screenY)) screenY = Number(referenceBox.top) || 0;
    var offsetX = screenX - (Number(referenceBox.x) || 0) * scale;
    var offsetY = screenY - (Number(referenceBox.y) || 0) * scale;
    return {
      x: ((Number(chartRect.left) || 0) - offsetX) / scale,
      y: ((Number(chartRect.top) || 0) - offsetY) / scale,
      w: (Number(chartRect.width) || 1) / scale,
      h: (Number(chartRect.height) || 1) / scale
    };
  }

  // ── Adapter ──────────────────────────────────────────────────

  function MapAdapter(rawTree) {
    if (!isObject(rawTree)) throw new Error('MapAdapter: tree must be an object');
    this.raw = clone(rawTree);
    this.promoted = isPromotable(this.raw);
    this.displayRoot = this.promoted ? this.raw.children[0] : this.raw;
    this.containerRoot = this.promoted ? this.raw : null;

    this.byId = new Map();
    this.pathById = new Map();
    this.reviewPathById = new Map();
    this.lightById = new Map();
    this.heavyById = new Map();
    this.hadChildrenById = new Map();
    this.branchIndexById = new Map();
    this.usedIds = new Set();
    this.links = Array.isArray(this.raw.links) ? clone(this.raw.links) : [];
    this._searchIndex = null;

    if (this.promoted) {
      var rootId = validId(this.raw.id) && !this.usedIds.has(this.raw.id)
        ? this.raw.id : 'n_root';
      while (this.usedIds.has(rootId)) rootId += '_';
      this.raw.id = rootId;
      this.usedIds.add(rootId);
    }

    this._index(this.displayRoot, [], true);
    this.title = normTitle(this.displayRoot.title || this.displayRoot.name || this.raw.title) || '知识树';
  }

  MapAdapter.prototype._index = function (node, pathIndexes, isDisplayRoot) {
    var title = normTitle(node.title || node.name) || '未命名';
    var id = validId(node.id) && !this.usedIds.has(node.id)
      ? node.id : stableId(pathIndexes, title);
    while (this.usedIds.has(id)) id += '_';
    this.usedIds.add(id);

    node.id = id;
    node.title = title;
    if (Object.prototype.hasOwnProperty.call(node, 'name')) node.name = title;

    var light = {};
    var heavy = {};
    Object.keys(node).forEach(function (key) {
      if (key === 'title' || key === 'name' || key === 'children' || key === 'id') return;
      if (VIEW_ONLY_FIELDS[key]) return;
      if (HEAVY_FIELDS[key]) heavy[key] = node[key];
      else light[key] = node[key];
    });
    this.lightById.set(id, light);
    this.heavyById.set(id, heavy);
    this.hadChildrenById.set(id, Object.prototype.hasOwnProperty.call(node, 'children'));

    // Build the display path from the parent's path stored below.
    if (isDisplayRoot) {
      this.pathById.set(id, [title]);
      this.reviewPathById.set(id, this.promoted ? [title] : []);
    } else {
      // Parent path is resolved by the caller after recursion; use a second pass.
      this.pathById.set(id, null);
    }

    var kids = Array.isArray(node.children) ? node.children : [];
    for (var i = 0; i < kids.length; i++) {
      this._index(kids[i], pathIndexes.concat([i]), false);
    }

    this.byId.set(id, node);
  };

  // Fill display/review paths in one DFS (after ids exist).
  MapAdapter.prototype._fillPaths = function () {
    var self = this;
    (function walk(node, displayPath, reviewPath, isRoot, branchIndex) {
      self.pathById.set(node.id, displayPath.concat([node.title]));
      self.reviewPathById.set(node.id, isRoot && !self.promoted ? [] : reviewPath.concat([node.title]));
      self.branchIndexById.set(node.id, branchIndex);
      var kids = Array.isArray(node.children) ? node.children : [];
      for (var i = 0; i < kids.length; i++) {
        walk(kids[i], displayPath.concat([node.title]),
          isRoot && !self.promoted ? [] : reviewPath.concat([node.title]), false,
          isRoot ? i : branchIndex);
      }
    })(this.displayRoot, [], [], true, -1);
  };

  MapAdapter.prototype.getNode = function (id) {
    return this.byId.get(id) || null;
  };

  MapAdapter.prototype.getPath = function (id) {
    return (this.pathById.get(id) || []).slice();
  };

  MapAdapter.prototype.getReviewPath = function (id) {
    return (this.reviewPathById.get(id) || []).slice();
  };

  MapAdapter.prototype.getTitle = function (id) {
    var node = this.getNode(id);
    return node ? (node.title || node.name || '') : '';
  };

  MapAdapter.prototype.getDisplayTree = function () {
    return clone(this.displayRoot);
  };

  MapAdapter.prototype.getContainerTree = function () {
    if (!this.promoted) return clone(this.displayRoot);
    var root = clone(this.containerRoot);
    root.children = [clone(this.displayRoot)];
    return root;
  };

  MapAdapter.prototype.getExportTree = function (focusId) {
    if (!focusId) return clone(this.displayRoot);
    var node = this.getNode(focusId);
    return node ? clone(node) : clone(this.displayRoot);
  };

  MapAdapter.prototype.count = function () {
    return this.byId.size;
  };

  // ── tree → Mind Elixir ───────────────────────────────────────

  MapAdapter.prototype._toMeNode = function (node, depth, opts) {
    var self = this;
    var light = this.lightById.get(node.id) || {};
    var me = {
      id: node.id,
      topic: node.title || node.name || '未命名',
      metadata: { kt: clone(light) }
    };
    if (hasChildren(node)) {
      me.children = node.children.map(function (child) {
        return self._toMeNode(child, depth + 1, opts);
      });
    }
    if (opts && typeof opts.branchColorFor === 'function') {
      var color = opts.branchColorFor(node, depth);
      if (color) me.branchColor = color;
    }
    if (opts && typeof opts.styleFor === 'function') {
      var style = opts.styleFor(node, depth);
      if (style) me.style = style;
    }
    return me;
  };

  MapAdapter.prototype.toMindElixirData = function (opts) {
    opts = opts || {};
    var layout = legacyLayoutName(opts.layout || 'SIDE');
    var data = {
      nodeData: this._toMeNode(this.displayRoot, 0, opts),
      arrows: this._toArrows(),
      direction: layoutDirection(layout)
    };
    if (layout === 'SIDE') assignBalancedDirections(data.nodeData);
    applyExpandLevel(data.nodeData, opts.expandLevel == null ? 2 : opts.expandLevel,
      opts.expandedOverrides || {});
    return data;
  };

  MapAdapter.prototype._toArrows = function () {
    var self = this;
    return this.links.map(function (link, index) {
      if (!link || !link.from || !link.to) return null;
      if (!self.byId.has(link.from) || !self.byId.has(link.to)) return null;
      return {
        id: link.id || ('l_' + index),
        from: link.from,
        to: link.to,
        label: link.label || '',
        bidirectional: !!link.bidirectional,
        style: link.style || undefined
      };
    }).filter(Boolean);
  };

  // ── Mind Elixir → tree ───────────────────────────────────────

  MapAdapter.prototype._fromMeNode = function (me) {
    var light = clone((me.metadata && me.metadata.kt) || {});
    var heavy = clone(this.heavyById.get(me.id) || {});
    var node = { id: me.id, title: normTitle(me.topic) || '未命名' };
    if (Object.prototype.hasOwnProperty.call(light, 'name')) node.name = node.title;
    Object.keys(light).forEach(function (key) {
      if (key !== 'name') node[key] = light[key];
    });
    // Heavy fields normally win, but an explicitly edited heavy field is copied
    // into metadata.kt first (see KTMap.applyNodePatch) so the engine's undo
    // history can restore it — in that case the light value is the newer one.
    Object.keys(heavy).forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(light, key)) node[key] = heavy[key];
    });
    if (Array.isArray(me.children) && me.children.length) {
      node.children = me.children.map(this._fromMeNode, this);
    } else if (this.hadChildrenById.get(me.id)) {
      node.children = [];
    }
    return node;
  };

  MapAdapter.prototype.fromMindElixirData = function (data) {
    if (!data || !data.nodeData) throw new Error('MapAdapter: missing nodeData');
    var display = this._fromMeNode(data.nodeData);
    var links = (data.arrows || []).map(function (arrow) {
      return {
        id: arrow.id,
        from: arrow.from,
        to: arrow.to,
        label: arrow.label || '',
        bidirectional: !!arrow.bidirectional
      };
    });
    if (this.promoted) {
      var root = clone(this.containerRoot);
      root.children = [display];
      root.links = links;
      return root;
    }
    display.links = links;
    return display;
  };

  /**
   * Adopt the live Mind Elixir document after an edit. Existing nodes keep
   * their heavy fields (content/tables/...) from `heavyById`; new nodes start
   * with whatever metadata the editor has. Rebuilds the id/path/search maps.
   */
  MapAdapter.prototype.syncFromMindElixir = function (data) {
    if (!data || !data.nodeData) return;
    var display = this._fromMeNode(data.nodeData);
    var links = (data.arrows || []).map(function (arrow) {
      return {
        id: arrow.id,
        from: arrow.from,
        to: arrow.to,
        label: arrow.label || '',
        bidirectional: !!arrow.bidirectional
      };
    });
    this.links = links;
    this.byId = new Map();
    this.pathById = new Map();
    this.reviewPathById = new Map();
    this.lightById = new Map();
    this.heavyById = new Map();
    this.hadChildrenById = new Map();
    this.branchIndexById = new Map();
    this.usedIds = new Set();
    this._searchIndex = null;
    this.displayRoot = display;
    if (this.promoted) {
      this.containerRoot.children = [display];
      this.containerRoot.links = links;
      if (this.containerRoot.id) this.usedIds.add(this.containerRoot.id);
    } else {
      display.links = links;
      this.raw = display;
      this.containerRoot = null;
    }
    this._index(display, [], true);
    this.title = normTitle(display.title || display.name || this.title) || '知识树';
  };

  // ── search ───────────────────────────────────────────────────

  MapAdapter.prototype._buildSearchIndex = function () {
    if (this._searchIndex) return this._searchIndex;
    var self = this;
    var entries = [];
    (function walk(node) {
      var light = self.lightById.get(node.id) || {};
      var heavy = self.heavyById.get(node.id) || {};
      var content = stripHtml(heavy.content || heavy.raw_content || '').slice(0, MAX_CONTENT_INDEX_CHARS);
      entries.push({
        id: node.id,
        title: node.title || node.name || '',
        path: (self.pathById.get(node.id) || []).join(' › '),
        summary: stripHtml(light.summary || ''),
        keywords: keywordText(light.keywords),
        exam: examText(light.exam_points),
        captions: captionText(light.captions),
        content: content
      });
      var kids = Array.isArray(node.children) ? node.children : [];
      for (var i = 0; i < kids.length; i++) walk(kids[i]);
    })(this.displayRoot);
    this._searchIndex = entries;
    return entries;
  };

  MapAdapter.prototype.search = function (query, limit) {
    var q = normTitle(query).toLowerCase();
    if (!q) return [];
    var entries = this._buildSearchIndex();
    var max = Math.max(1, Math.min(MAX_SEARCH_RESULTS, Number(limit) || MAX_SEARCH_RESULTS));
    var results = [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var title = entry.title.toLowerCase();
      var score = 0;
      var field = '';
      var snippet = '';
      if (title === q) { score = 120; field = 'title'; snippet = entry.title; }
      else if (title.indexOf(q) === 0) { score = 100; field = 'title'; snippet = entry.title; }
      else if (title.indexOf(q) !== -1) { score = 80; field = 'title'; snippet = entry.title; }
      else if (entry.summary.toLowerCase().indexOf(q) !== -1) { score = 40; field = 'summary'; snippet = entry.summary; }
      else if (entry.keywords.toLowerCase().indexOf(q) !== -1) { score = 35; field = 'keywords'; snippet = entry.keywords; }
      else if (entry.exam.toLowerCase().indexOf(q) !== -1) { score = 35; field = 'exam'; snippet = entry.exam; }
      else if (entry.captions.toLowerCase().indexOf(q) !== -1) { score = 25; field = 'captions'; snippet = entry.captions; }
      else if (entry.content.toLowerCase().indexOf(q) !== -1) { score = 15; field = 'content'; snippet = entry.content; }
      if (!score) continue;
      results.push({
        id: entry.id,
        title: entry.title,
        path: entry.path,
        field: field,
        snippet: makeSnippet(snippet, q),
        score: score
      });
    }
    results.sort(function (a, b) {
      return b.score - a.score || a.title.length - b.title.length;
    });
    return results.slice(0, max);
  };

  function makeSnippet(text, query) {
    var source = String(text || '');
    var lower = source.toLowerCase();
    var index = lower.indexOf(query);
    if (index < 0) return source.slice(0, 80);
    var start = Math.max(0, index - 28);
    var end = Math.min(source.length, index + query.length + 48);
    return (start > 0 ? '…' : '') + source.slice(start, end) + (end < source.length ? '…' : '');
  }

  // ── export ───────────────────────────────────────────────────

  var api = {
    MapAdapter: MapAdapter,
    clone: clone,
    normTitle: normTitle,
    hashString: hashString,
    stableId: stableId,
    validId: validId,
    stripHtml: stripHtml,
    keywordText: keywordText,
    examText: examText,
    captionText: captionText,
    walkMe: walkMe,
    countNodes: countNodes,
    findMeNode: findMeNode,
    findMePath: findMePath,
    applyExpandLevel: applyExpandLevel,
    expandAncestors: expandAncestors,
    branchWeight: branchWeight,
    assignBalancedDirections: assignBalancedDirections,
    legacyLayoutName: legacyLayoutName,
    layoutDirection: layoutDirection,
    fitBoxesToCanvas: fitBoxesToCanvas,
    minimapViewport: minimapViewport,
    isPromotable: isPromotable,
    HEAVY_FIELDS: HEAVY_FIELDS,
    MAX_SEARCH_RESULTS: MAX_SEARCH_RESULTS
  };

  // Fill paths lazily after construction (needs all ids first).
  var OriginalInit = MapAdapter.prototype._index;
  MapAdapter.prototype._index = function (node, pathIndexes, isDisplayRoot) {
    OriginalInit.call(this, node, pathIndexes, isDisplayRoot);
    if (isDisplayRoot) this._fillPaths();
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.KTMapAdapter = api;
})(typeof window !== 'undefined' ? window : globalThis);
