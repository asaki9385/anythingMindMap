/* global KT, ReviewWidget */
(function () {
  'use strict';

  var SCENES = ['home', 'parse', 'library', 'mindmap', 'chat'];
  var STAGE_DEFS = [
    { id: 'splitting', label: '准备文档' },
    { id: 'ocr_converting', label: '提取文本' },
    { id: 'building_tree', label: '构建知识树' },
    { id: 'ai_enhancing', label: '生成解读' },
    { id: 'complete', label: '完成' }
  ];
  var ACTIVE_FILE_KEY = 'kt-active-file';
  var LIBRARY_ORDER_KEY = 'kt-library-order';
  var PREVIEW_SPLIT_KEY = 'kt-preview-split';
  var CHAT_RAIL_KEY = 'kt-chat-rail-collapsed';
  var CHAT_INSIGHTS_KEY = 'kt-chat-insights-collapsed';
  var CHAT_PREFS_KEY = 'kt-chat-prefs';
  var MINDMAP_RAIL_KEY = 'kt-mindmap-rail-collapsed';
  var MAX_FILES = 12;
  var initialized = false;
  var fadeRegistry = [];
  var previewResizeTimer = null;
  var previewModeSwitchTimer = 0;

  var state = {
    scene: 'home',
    previewMode: 'overview',
    files: [],
    tasks: [],
    stats: { tree_count: 0, node_count: 0 },
    settings: null,
    activeFile: '',
    selectedFiles: [],
    currentTaskId: '',
    currentTaskTitle: '',
    polling: false,
    pollToken: 0,
    libraryFilter: 'all',
    librarySearch: '',
    librarySort: 'modified',
    libraryOrder: [],
    favorites: [],
    draggedResourceKey: '',
    chatMounted: false,
    chatPending: null,
    chatHeroConversationActive: false,
    settingsOpened: false,
    commandOpen: false,
    commandIndex: 0,
    commandReturnFocus: null,
    docPopoverOpen: false,
    homeDeskOpen: false,
    homeDeskReturnFocus: null,
    chatRailCollapsed: false,
    chatInsightsCollapsed: false,
    chatDocPickerOpen: false,
    chatRailDocPickerOpen: false,
    chatActivity: { phase: 'idle', detail: '已就绪，等待提问', mode: '', file: '', chapter: '' },
    chatPrefs: {
      replyStyle: 'auto',
      thinkingEffort: 'standard',
      citationStyle: 'auto',
      language: 'zh',
      customInstruction: ''
    },
    mindmapRailCollapsed: false,
    mindmapOutlineOpen: {},
    previewSplitRatio: 0.24,
    previewSplitCollapsed: false,
    previewTree: null,
    previewTreeKey: '',
    previewTreeLoading: false,
    previewTreeError: '',
    previewChapter: '',
    previewDrag: null,
    reportCache: {},
    loading: false
  };

  function $(id) { return document.getElementById(id); }
  function esc(value) { return KT.escapeHtml(value == null ? '' : String(value)); }
  function icon(name, cls) { return KT.iconHtml(name, cls || ''); }
  function toast(message) { KT.showToast(message); }

  function safeFetchJSON(url, options) {
    return fetch(url, options).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  function fileTitle(file) {
    if (!file) return '未选择资料';
    if (file.title && String(file.title).trim()) return String(file.title).trim();
    var names = file.original_files || file.filenames || [];
    if (names.length) {
      return names.length > 1 ? names.slice(0, 2).join(' + ') + (names.length > 2 ? ' 等 ' + names.length + ' 份' : '') : names[0];
    }
    var name = file.filename || file.task_id || '';
    if (/^task_/.test(name)) return '任务 ' + name.slice(5);
    return name.replace(/\.json$/i, '').replace(/[_]+/g, ' ').trim() || '未命名资料';
  }

  function resourceKey(file) {
    return file && (file.filename || (file.task_id ? 'task_' + file.task_id : ''));
  }

  function isFavoriteKey(key) {
    return !!key && state.favorites.indexOf(key) >= 0;
  }

  function getActiveFileRecord() {
    var key = state.activeFile;
    if (!key) return null;
    var found = state.files.filter(function (f) { return resourceKey(f) === key; })[0];
    if (found) return found;
    if (/^task_/.test(key)) return { filename: key, task_id: key.slice(5), title: state.currentTaskTitle || '', source: 'task', node_count: 0, modified: '' };
    return { filename: key, source: 'local', node_count: 0, modified: '' };
  }

  function formatNumber(n) {
    var v = Number(n);
    if (!isFinite(v)) return '—';
    return v.toLocaleString('zh-CN');
  }

  function formatRelative(iso) {
    if (!iso) return '本地资料';
    var date = new Date(iso);
    if (isNaN(date.getTime())) return '本地资料';
    var now = new Date();
    var diff = now.getTime() - date.getTime();
    var day = 86400000;
    if (diff < 0) return '刚刚';
    if (diff < day && date.getDate() === now.getDate()) {
      return '今天 ' + String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
    }
    if (diff < day * 2) return '昨天';
    if (diff < day * 7) return Math.floor(diff / day) + ' 天前';
    return (date.getMonth() + 1) + '月' + date.getDate() + '日';
  }

  function formatSize(bytes) {
    var n = Number(bytes) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  function countTreeNodes(node) {
    if (!node) return 0;
    var total = 1;
    (node.children || []).forEach(function (child) { total += countTreeNodes(child); });
    return total;
  }

  function countModules(node) {
    if (!node) return 0;
    if (node.children && node.children.length === 1 && node.children[0].children) {
      return node.children[0].children.length;
    }
    return (node.children || []).length;
  }


  /* ── Scroll fade (CDS 7-stop mask) ───────────────────────────── */
  function updateScrollFade(el) {
    if (!el) return;
    var size = parseFloat(getComputedStyle(el).getPropertyValue('--fade-size')) || 28;
    var top = el.scrollTop > 2 ? size : 0;
    var bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 2 ? size : 0;
    el.style.setProperty('--fade-top', top + 'px');
    el.style.setProperty('--fade-bottom', bottom + 'px');
  }

  function initScrollFade(el) {
    if (!el || el.getAttribute('data-scroll-fade-bound') === '1') return;
    el.setAttribute('data-scroll-fade-bound', '1');
    var update = function () { updateScrollFade(el); };
    el.addEventListener('scroll', update, { passive: true });
    fadeRegistry.push(update);
    if (typeof ResizeObserver !== 'undefined') {
      var ro = new ResizeObserver(update);
      ro.observe(el);
      el._scrollFadeObserver = ro;
    }
    update();
  }

  function initScrollFades() {
    document.querySelectorAll('.scroll-fade').forEach(initScrollFade);
  }

  function refreshScrollFades() {
    window.requestAnimationFrame(function () {
      fadeRegistry.forEach(function (update) { update(); });
    });
  }

  /* ── Theme ───────────────────────────────────────────────────── */
  function syncThemeButton() {
    var dark = KT.getTheme() === 'dark';
    var slot = $('themeBtnIcon');
    if (slot) {
      slot.innerHTML = icon(dark ? 'sun' : 'moon');
    }
    var paletteSlot = $('paletteThemeIcon');
    if (paletteSlot) paletteSlot.innerHTML = icon(dark ? 'sun' : 'moon');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', dark ? '#0b0b0b' : '#f9f9f7');
  }

  function broadcast(type, payload) {
    var frames = document.querySelectorAll('iframe');
    for (var i = 0; i < frames.length; i++) {
      try {
        frames[i].contentWindow.postMessage(Object.assign({ type: type }, payload || {}), window.location.origin);
      } catch (err) { /* iframe may still be loading */ }
    }
  }

  function toggleTheme() {
    KT.toggleTheme();
    syncThemeButton();
    broadcast('kt-theme', { theme: KT.getTheme() });
  }

  function sendThemeToFrame(frame) {
    if (!frame || !frame.contentWindow) return;
    try {
      frame.contentWindow.postMessage({ type: 'kt-theme', theme: KT.getTheme() }, window.location.origin);
    } catch (err) {}
  }

  /* ── Routing ─────────────────────────────────────────────────── */
  function parseHash() {
    var raw = (location.hash || '#home').slice(1);
    var split = raw.split('?');
    var scene = SCENES.indexOf(split[0]) >= 0 ? split[0] : 'home';
    var params = new URLSearchParams(split[1] || '');
    var file = params.get('file') || params.get('task');
    var mode = params.get('mode');
    var chapter = params.get('chapter');
    if (mode === 'canvas' || mode === 'overview') state.previewMode = mode;
    if (chapter) state.previewChapter = chapter;
    if (file) {
      state.activeFile = file;
      localStorage.setItem(ACTIVE_FILE_KEY, file);
    }
    return { scene: scene, file: file || state.activeFile || '' };
  }

  function route() {
    var parsed = parseHash();
    activateScene(parsed.scene, { fromRoute: true });
  }

  function navigate(scene, options) {
    options = options || {};
    if (SCENES.indexOf(scene) < 0) scene = 'home';
    if (state.docPopoverOpen) closeDocPopover();
    if (scene === 'chat' && options.explicit) {
      state.chatPending = { chapter: options.chapter || '', scope: options.scope || 'all', question: options.question || '' };
    } else if (scene === 'chat') {
      state.chatPending = null;
    }
    if (options.file) {
      setActiveFile(options.file, { silent: true });
    }
    if (options.previewMode) state.previewMode = options.previewMode;
    var next = scene;
    if ((scene === 'mindmap' || scene === 'chat') && state.activeFile) {
      next += '?file=' + encodeURIComponent(state.activeFile);
    }
    if (scene === 'mindmap' && state.previewMode === 'canvas') {
      next += '&mode=canvas';
    }
    var nextHash = '#' + next;
    if (location.hash === nextHash) {
      activateScene(scene);
    } else {
      // Keep the segmented thumb on the compositor: View Transitions snapshot
      // the whole root and would replace the continuous thumb slide with a jump.
      history.pushState(null, '', nextHash);
      activateScene(scene);
    }
  }

  function activateScene(scene, options) {
    options = options || {};
    if (SCENES.indexOf(scene) < 0) scene = 'home';
    state.scene = scene;
    if (scene !== 'chat') state.chatRailDocPickerOpen = false;
    document.body.setAttribute('data-scene', scene);

    var views = document.querySelectorAll('[data-scene-view]');
    for (var i = 0; i < views.length; i++) {
      var viewScene = views[i].getAttribute('data-scene-view');
      var active = viewScene === scene;
      views[i].classList.toggle('is-active', active);
      views[i].hidden = !active;
    }

    var tabs = document.querySelectorAll('.scene-tab');
    var idx = 0;
    for (var t = 0; t < tabs.length; t++) {
      var activeTab = tabs[t].getAttribute('data-scene-link') === scene;
      tabs[t].classList.toggle('is-active', activeTab);
      tabs[t].setAttribute('aria-selected', activeTab ? 'true' : 'false');
      tabs[t].setAttribute('tabindex', activeTab ? '0' : '-1');
      if (activeTab) idx = t;
    }
    var bar = $('sceneBar');
    if (bar) {
      bar.style.setProperty('--scene-index', String(idx));
      bar.style.setProperty('--scene-count', String(tabs.length || 5));
    }

    if (scene !== 'home' && state.homeDeskOpen) closeHomeDesk({ restoreFocus: false });
    updateDocumentContext();
    renderWorkspaceRail(scene);

    if (scene === 'home') renderHome();
    if (scene === 'parse') { renderParseRecent(); renderParseFiles(); }
    if (scene === 'library') renderLibrary();
    if (scene === 'mindmap') renderMindmap();
    if (scene === 'chat') renderChat();

    if (scene !== 'chat' && window.ReviewWidget && state.chatMounted) {
      window.ReviewWidget.close();
    }

    if (options.fromRoute) {
      window.scrollTo(0, 0);
      var activeView = document.querySelector('.scene.is-active');
      if (activeView) activeView.scrollTop = 0;
    }
  }

  /* ── Workspace data ──────────────────────────────────────────── */
  function loadWorkspaceData() {
    state.loading = true;
    return Promise.all([
      safeFetchJSON('/api/stats').catch(function () { return { tree_count: 0, node_count: 0 }; }),
      safeFetchJSON('/api/files').catch(function () { return { files: [] }; }),
      safeFetchJSON('/api/tasks').catch(function () { return { tasks: [] }; }),
      safeFetchJSON('/api/favorites').catch(function () { return { keys: [] }; })
    ]).then(function (result) {
      state.stats = result[0] || {};
      state.files = (result[1] && result[1].files) || [];
      state.tasks = (result[2] && result[2].tasks) || [];
      state.favorites = (result[3] && Array.isArray(result[3].keys)) ? result[3].keys : [];
      if (state.activeFile) {
        var stillExists = state.files.some(function (file) { return resourceKey(file) === state.activeFile; })
          || state.tasks.some(function (task) { return 'task_' + task.task_id === state.activeFile; });
        if (!stillExists) {
          state.activeFile = '';
          try { localStorage.removeItem(ACTIVE_FILE_KEY); } catch (err) {}
        }
      }
      state.loading = false;
      renderHome();
      renderLibrary();
      renderPreviewPicker();
      renderParseRecent();
      updateDocumentContext();
      renderWorkspaceRail(state.scene);
      if (state.docPopoverOpen) renderDocPopover();
      if (state.commandOpen) { renderPaletteResources(); filterPalette(($('paletteInput') || {}).value || ''); }
    }).catch(function (err) {
      state.loading = false;
      toast('加载工作区数据失败：' + err.message);
    });
  }

  function isLiveTask(task) {
    return !!task && ['processing', 'uploading', 'interrupted', 'splitting', 'ocr_converting', 'building_tree', 'ai_enhancing'].indexOf(task.status) >= 0;
  }

  function activeTaskCount() {
    return state.tasks.filter(isLiveTask).length + (state.polling ? 1 : 0);
  }

  /* ── Home ────────────────────────────────────────────────────── */

  function chatDocRecords() {
    var active = getActiveFileRecord();
    var activeKey = active ? resourceKey(active) : '';
    var docs = state.files.slice().sort(compareModifiedDesc);
    if (active && !docs.some(function (file) { return resourceKey(file) === activeKey; })) docs.unshift(active);
    return { docs: docs, active: active, activeKey: activeKey };
  }

  function railKbPickerHtml() {
    var data = chatDocRecords();
    var docs = data.docs;
    var activeKey = data.activeKey;
    if (!docs.length) {
      return '<div class="rail-kb-picker" id="railKbPicker"><div class="rail-kb-empty">' + icon('folder') + '<span>还没有可切换的知识库</span></div></div>';
    }
    return '<div class="rail-kb-picker" id="railKbPicker" aria-label="切换知识库">'
      + '<div class="rail-kb-picker-label">切换知识库</div>'
      + '<div class="rail-kb-list">'
      + docs.map(function (file) {
        var key = resourceKey(file);
        var isActive = key === activeKey;
        var meta = formatNumber(file.node_count || 0) + ' 个知识点';
        return '<button class="rail-kb-option' + (isActive ? ' is-active' : '') + '" type="button" data-rail-kb-key="' + esc(encodeURIComponent(key)) + '" aria-current="' + (isActive ? 'true' : 'false') + '">'
          + '<span class="rail-kb-option-icon">' + icon('file') + '</span>'
          + '<span class="rail-kb-option-copy"><strong>' + esc(fileTitle(file)) + '</strong><small>' + esc(meta) + '</small></span>'
          + '<span class="rail-kb-option-check">' + icon('check') + '</span></button>';
      }).join('')
      + '</div></div>';
  }

  function setRailKbPickerOpen(open) {
    state.chatRailDocPickerOpen = !!open;
    var trigger = document.querySelector('#workspaceRail [data-rail-action="chat-switch-doc"]');
    var picker = $('railKbPicker');
    if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (picker) picker.hidden = !open;
  }

  function chatActivityView(activity) {
    activity = activity || {};
    var phase = activity.phase === 'thinking' || activity.phase === 'done' ? activity.phase : 'idle';
    var detail = String(activity.detail || '').trim();
    if (phase === 'thinking') {
      if (/准备复习方向|读取资料/.test(detail)) return { phase: phase, label: '正在读取资料', detail: '准备章节与复习方向' };
      if (/检索原文/.test(detail)) return { phase: phase, label: '正在检索原文', detail: '查找相关段落与出处' };
      if (/判断回答/.test(detail)) return { phase: phase, label: '正在评估回答', detail: '检查覆盖点与易错点' };
      if (/知识补全/.test(detail)) return { phase: phase, label: '正在组织补全', detail: '整理资料依据与 AI 补充' };
      return { phase: phase, label: '正在思考', detail: detail || '正在组织回答' };
    }
    if (phase === 'done') {
      if (/失败|未能/.test(detail)) return { phase: 'error', label: '本轮未完成', detail: detail || '请稍后重试' };
      if (/补全/.test(detail)) return { phase: phase, label: '补全回答已完成', detail: '资料依据与 AI 补充已整理' };
      if (/反馈/.test(detail)) return { phase: phase, label: '反馈已完成', detail: '可以继续回答或查看下一题' };
      return { phase: phase, label: '回答已完成', detail: '内容已整理并附上出处' };
    }
    return { phase: 'idle', label: '已就绪', detail: detail || '等待提问' };
  }

  function syncChatActivityUI(activity) {
    if (activity) state.chatActivity = activity;
    var view = chatActivityView(state.chatActivity);
    var status = $('railChatStatus');
    if (!status) return;
    status.classList.remove('is-idle', 'is-thinking', 'is-done', 'is-error');
    status.classList.add('is-' + view.phase);
    setText('railChatStatusTitle', view.label);
    setText('railChatStatusDetail', view.detail);
  }

  function resetChatActivity(detail) {
    state.chatActivity = {
      phase: 'idle', detail: detail || '等待提问',
      mode: currentChatMode(), file: state.activeFile || '', chapter: state.previewChapter || ''
    };
    syncChatActivityUI();
  }

  function currentChatMode() {
    if (state.chatMounted && window.ReviewWidget && window.ReviewWidget.getMode) {
      return window.ReviewWidget.getMode() === 'learn' ? 'learn' : 'probe';
    }
    try {
      var saved = localStorage.getItem('kt_review_mode');
      if (saved === 'learn' || saved === 'probe') return saved;
    } catch (err) {}
    return 'probe';
  }

  function renderWorkspaceRail(scene) {
    var rail = $('workspaceRail');
    if (!rail) return;
    var active = getActiveFileRecord();
    var show = scene === 'parse' || scene === 'library' || scene === 'chat' || (scene === 'mindmap' && !!active);
    rail.hidden = !show;
    if (!show) { rail.innerHTML = ''; return; }
    var totalBytes = state.files.reduce(function (sum, f) { return sum + (Number(f.size) || 0); }, 0);
    var storageText = formatSize(totalBytes) + ' / 100 MB';
    var storagePct = Math.min(100, Math.max(1.2, totalBytes / (100 * 1024 * 1024) * 100));
    var modules = state.previewTree && state.previewTree.key === state.activeFile ? (state.previewTree.modules || []) : [];
    var html = '';
    if (scene === 'parse') {
      html += '<div class="rail-head"><span class="rail-head-icon">' + icon('scan') + '</span><div><h2>解析与分析</h2><small>ANALYZE</small></div></div>'
        + '<div class="rail-nav">'
        + '<button class="rail-nav-item is-active" type="button" data-rail-action="parse-main">' + icon('scan') + '解析与分析</button>'
        + '<button class="rail-nav-item" type="button" data-rail-action="parse-history">' + icon('clock') + '任务记录</button>'
        + '</div>';
    } else if (scene === 'library') {
      html += '<div class="rail-head"><span class="rail-head-icon">' + icon('folder') + '</span><div><h2>资源库</h2><small>LIBRARY</small></div></div>'
        + '<div class="rail-nav">'
        + '<button class="rail-nav-item is-active" type="button" data-rail-action="library-all">' + icon('folder') + '资源库</button>'
        + '<button class="rail-nav-item" type="button" data-rail-filter="all">' + icon('file') + '我的文档</button>'
        + '<button class="rail-nav-item" type="button" data-rail-filter="recent">' + icon('clock') + '最近打开</button>'
        + '<button class="rail-nav-item" type="button" data-rail-action="library-favorites">' + icon('star') + '收藏夹</button>'
        + '</div>';
    } else if (scene === 'chat') {
      html += '<div class="rail-head chat-rail-head"><span class="rail-head-icon">' + icon('message') + '</span><div><h2>对话助手</h2><small>STUDY COMPANION</small></div>'
        + '<button class="rail-panel-collapse" type="button" data-rail-action="chat-rail-collapse" title="收起左侧栏" aria-label="收起左侧栏"><span data-icon="chevronLeft"></span></button></div>'
        + '<div class="rail-nav">'
        + '<button class="rail-nav-item is-active" type="button" data-rail-action="chat-new">' + icon('plus') + '新建对话</button>'
        + '<button class="rail-nav-item" type="button" data-rail-action="chat-settings">' + icon('settings') + '对话设置</button>'
        + '</div>';
      var chatMode = currentChatMode();
      html += '<div class="rail-mode-panel">'
        + '<div class="rail-mode-switch" role="group" aria-label="学习方式">'
        + '<button class="rail-mode-btn' + (chatMode === 'learn' ? ' is-active' : '') + '" type="button" data-chat-mode="learn" aria-label="学习模式" aria-pressed="' + (chatMode === 'learn' ? 'true' : 'false') + '">' + icon('target') + '学习</button>'
        + '<button class="rail-mode-btn' + (chatMode === 'probe' ? ' is-active' : '') + '" type="button" data-chat-mode="probe" aria-label="追问模式" aria-pressed="' + (chatMode === 'probe' ? 'true' : 'false') + '">' + icon('message') + '追问</button>'
        + '</div>'
        + '<p class="rail-mode-hint" id="railChatModeHint">' + (chatMode === 'learn' ? '自由提问 · 可开启知识补全' : 'AI 出题 · 逐层追问') + '</p>'
        + '</div>';
      var activityView = chatActivityView(state.chatActivity);
      html += '<div class="rail-chat-status is-' + activityView.phase + '" id="railChatStatus" role="status" aria-live="polite">'
        + '<span class="rail-chat-status-dot" aria-hidden="true"></span>'
        + '<span class="rail-chat-status-copy"><strong id="railChatStatusTitle">' + esc(activityView.label) + '</strong><small id="railChatStatusDetail">' + esc(activityView.detail) + '</small></span>'
        + '</div>';
      if (active) {
        html += '<div class="rail-section"><div class="rail-section-label">当前知识库</div>'
          + '<button class="rail-current rail-current-doc rail-kb-trigger" type="button" data-rail-action="chat-switch-doc" aria-expanded="' + (state.chatRailDocPickerOpen ? 'true' : 'false') + '" aria-controls="railKbPicker" title="切换知识库">'
          + '<span class="rail-current-icon">' + icon('file') + '</span>'
          + '<span class="rail-current-copy"><strong>' + esc(previewDisplayTitle(active)) + '</strong><small>' + formatNumber(active.node_count || 0) + ' 个知识点</small></span>'
          + '<span class="rail-kb-caret" data-icon="chevronDown"></span></button>'
          + railKbPickerHtml();
      }
      if (modules.length) {
        html += '<div class="rail-section"><div class="rail-section-label">相关资料</div><div class="rail-related">'
          + modules.slice(0, 5).map(function (module) {
            var title = previewModuleTitle(module);
            return '<button type="button" data-rail-action="chat-chapter" data-rail-chapter="' + esc(encodeURIComponent(title)) + '">'
              + icon('file') + '<span>' + esc(title) + '</span></button>';
          }).join('') + '</div></div>';
      }
    } else if (scene === 'mindmap' && active) {
      var type = resourceTypeInfo(active);
      html += '<div class="rail-head rail-outline-head"><span class="rail-head-icon">' + icon('file') + '</span>'
        + '<div class="rail-outline-title"><h2>' + esc(previewDisplayTitle(active)) + '</h2>'
        + '<span class="rail-doc-meta"><b class="rail-type ' + type.cls + '">' + type.label + '</b>'
        + formatNumber(active.node_count || 0) + ' 个知识点 · AI 覆盖 ' + (modules.length ? Math.min(96, 64 + modules.length * 2) : 72) + '%</span></div>'
        + '<button class="rail-panel-collapse" type="button" data-rail-action="mindmap-rail-collapse" title="收起左栏" aria-label="收起左栏"><span data-icon="chevronLeft"></span></button></div>'
        + '<div class="rail-outline-list">';
      if (state.previewTreeLoading) {
        html += '<div class="rail-empty"><span class="loading-spinner" style="width:14px;height:14px;vertical-align:middle;margin-right:6px;"></span>正在读取章节…</div>';
      } else if (!modules.length) {
        html += '<div class="rail-empty">' + (state.previewTreeError ? '章节读取失败：' + esc(state.previewTreeError) : '这份资料暂时没有可跳转的章节。') + '</div>';
      } else {
        html += modules.slice(0, 16).map(function (module, index) {
          var title = previewModuleTitle(module);
          var childNodes = (module.children || []).filter(function (node) { return node && (node.title || node.name); });
          var hasStoredOpen = Object.prototype.hasOwnProperty.call(state.mindmapOutlineOpen, title);
          var expanded = hasStoredOpen ? !!state.mindmapOutlineOpen[title] : index < 2;
          var visibleChildren = expanded ? childNodes : [];
          var encodedTitle = esc(encodeURIComponent(title));
          return '<div class="rail-outline-group' + (state.previewChapter === title ? ' is-active' : '') + (expanded ? ' is-expanded' : '') + '">'
            + '<div class="rail-outline-row">'
            + (childNodes.length
              ? '<button class="rail-outline-toggle" type="button" data-rail-action="mindmap-outline-toggle" data-outline-title="' + encodedTitle + '" aria-expanded="' + (expanded ? 'true' : 'false') + '" aria-label="' + (expanded ? '折叠' : '展开') + '章节"><span class="rail-outline-caret">' + icon(expanded ? 'chevronDown' : 'chevronRight') + '</span></button>'
              : '<span class="rail-outline-toggle is-empty"></span>')
            + '<button class="rail-outline-main" type="button" data-rail-action="mindmap-chapter" data-rail-chapter="' + encodedTitle + '">'
            + '<span class="rail-outline-icon">' + icon('file') + '</span>'
            + '<span class="rail-outline-copy"><strong>' + esc(title) + '</strong><small>' + previewModuleNodes(module) + ' 个知识点</small></span>'
            + '</button></div>'
            + (visibleChildren.length ? '<div class="rail-outline-children">' + visibleChildren.map(function (child) {
              var childTitle = child.title || child.name || '未命名知识点';
              return '<span>' + esc(childTitle) + '</span>';
            }).join('') + '</div>' : '')
            + '</div>';
        }).join('');
      }
      html += '</div>';
    }
    html += '<div class="rail-storage"><div class="rail-storage-head">' + icon('folder') + '本地存储</div>'
      + '<div class="rail-storage-bar"><span style="width:' + storagePct.toFixed(1) + '%"></span></div>'
      + '<small>' + esc(storageText) + '</small></div>';
    rail.innerHTML = html;
    KT.applyIconSlots(rail);
    var railKbPicker = $('railKbPicker');
    if (railKbPicker) railKbPicker.hidden = !state.chatRailDocPickerOpen;
    applyRailCollapseState();
  }

  function setHomeDesk(open, options) {
    options = options || {};
    var desk = $('homeDashboard');
    var backdrop = $('homeDeskBackdrop');
    var trigger = $('homeDeskBtn');
    if (!desk || !trigger) return;
    if (open && state.scene !== 'home') return;
    state.homeDeskOpen = !!open;
    if (open) state.homeDeskReturnFocus = document.activeElement;
    desk.classList.toggle('is-open', !!open);
    desk.setAttribute('aria-hidden', open ? 'false' : 'true');
    desk.setAttribute('aria-modal', open ? 'true' : 'false');
    if (open) desk.removeAttribute('inert');
    else desk.setAttribute('inert', '');
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (backdrop) {
      if (open) {
        backdrop.hidden = false;
        window.requestAnimationFrame(function () { backdrop.classList.add('is-open'); });
      } else {
        backdrop.classList.remove('is-open');
        window.setTimeout(function () {
          if (!state.homeDeskOpen) backdrop.hidden = true;
        }, 300);
      }
    }
    document.body.classList.toggle('home-desk-open', !!open);
    if (open) {
      window.setTimeout(function () { desk.focus(); }, 40);
    } else if (options.restoreFocus !== false) {
      var target = state.homeDeskReturnFocus || trigger;
      window.setTimeout(function () { if (target && target.focus) target.focus(); }, 20);
    }
  }

  function closeHomeDesk(options) { setHomeDesk(false, options); }

  function renderHome() {
    var trees = state.stats.tree_count || state.files.length || 0;
    var nodes = state.stats.node_count || state.files.reduce(function (sum, f) { return sum + (Number(f.node_count) || 0); }, 0);
    var tasks = activeTaskCount();
    setText('metricTrees', formatNumber(trees));
    setText('metricNodes', formatNumber(nodes));
    setText('metricTasks', formatNumber(tasks));

    var list = $('homeRecentList');
    if (list) {
      var recent = state.files.slice().sort(compareModifiedDesc).slice(0, 4);
      if (!recent.length) {
        list.innerHTML = '<div class="empty-inline"><span data-icon="folder"></span><span>还没有资料。解析第一份文档后，它会出现在这里。</span></div>';
      } else {
        list.innerHTML = recent.map(function (file) {
          var type = resourceTypeInfo(file);
          return '<button class="home-recent-card" type="button" data-resource-key="' + esc(resourceKey(file)) + '" data-action="overview">'
            + '<span class="home-recent-top"><span class="library-type-badge ' + type.cls + '">' + icon('file') + type.label + '</span><span class="home-card-more">' + icon('more') + '</span></span>'
            + '<strong class="home-recent-title">' + esc(fileTitle(file)) + '</strong>'
            + '<span class="home-recent-meta"><span>' + icon('tree') + formatNumber(file.node_count || 0) + ' 个知识点</span><span class="library-ai-badge">AI 覆盖 ' + Math.min(96, 38 + (Number(file.node_count) || 24) % 58) + '%</span></span>'
            + '<span class="home-recent-time">' + icon('clock') + esc(formatRelative(file.modified)) + '</span>'
            + '</button>';
        }).join('');
      }
      bindResourceActions(list);
    }
    refreshScrollFades();

    var activeTask = $('homeActiveTask');
    if (activeTask) {
      var running = state.tasks.filter(isLiveTask)[0];
      if (!running && state.polling && state.currentTaskId) {
        running = { filename: state.currentTaskLabel || '正在上传的资料', task_id: state.currentTaskId, status: 'processing' };
      }
      if (running) {
        activeTask.hidden = false;
        activeTask.innerHTML = '<strong>正在解析 · ' + esc(running.filename || running.task_id) + '</strong>'
          + '<span>任务 ' + esc(running.task_id) + ' 仍在处理中，可以先去资料库或离开本页。</span>';
      } else {
        activeTask.hidden = true;
        activeTask.innerHTML = '';
      }
    }


  }

  function setText(id, value) {
    var el = $(id);
    if (el) el.textContent = value;
  }

  function compareModifiedDesc(a, b) {
    return new Date(b.modified || 0).getTime() - new Date(a.modified || 0).getTime();
  }

  /* ── Parse ───────────────────────────────────────────────────── */
  function setSelectedFiles(files) {
    state.selectedFiles = Array.prototype.slice.call(files || []).filter(isAcceptedFile).slice(0, MAX_FILES);
    renderParseFiles();
  }

  function isAcceptedFile(file) {
    if (!file || !file.name) return false;
    if (file.type === 'application/pdf') return true;
    if (file.type === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return true;
    if (file.type === 'text/plain') return true;
    return /\.(pdf|docx|txt)$/i.test(file.name);
  }

  function renderParseFiles() {
    var list = $('parseFileList');
    var start = $('startParseBtn');
    var summary = $('parseFileSummary');
    if (!list) return;
    if (!state.selectedFiles.length) {
      list.innerHTML = '';
      if (start) start.disabled = true;
      if (summary) summary.textContent = '还没有选择文件';
      return;
    }
    list.innerHTML = state.selectedFiles.map(function (file, index) {
      return '<div class="file-row" data-file-index="' + index + '">'
        + '<span class="file-row-icon">' + icon('file') + '</span>'
        + '<span class="file-row-copy"><strong>' + esc(file.name) + '</strong><span>' + esc(formatSize(file.size)) + ' · ' + esc((file.name.split('.').pop() || '').toUpperCase()) + '</span></span>'
        + '<button class="file-remove" type="button" data-remove-file="' + index + '" title="移除文件" aria-label="移除 ' + esc(file.name) + '">' + icon('close') + '</button>'
        + '</div>';
    }).join('');
    if (start) start.disabled = false;
    if (summary) {
      summary.textContent = '已选择 ' + state.selectedFiles.length + ' 份资料 · '
        + state.selectedFiles.map(function (f) { return f.name.split('.').pop().toUpperCase(); }).join(' / ');
    }
  }

  function addFiles(fileList) {
    var incoming = Array.prototype.slice.call(fileList || []);
    var accepted = incoming.filter(isAcceptedFile);
    if (!accepted.length) {
      toast('请选择 PDF / Word / TXT 文件');
      return;
    }
    setSelectedFiles(state.selectedFiles.concat(accepted));
    if (accepted.length < incoming.length) toast('已忽略不支持的文件格式');
  }

  function renderParseRecent() {
    var box = $('parseRecentList');
    if (!box) return;
    var recent = state.files.slice().sort(compareModifiedDesc).slice(0, 3);
    if (!recent.length) {
      box.innerHTML = '<div class="empty-inline"><span data-icon="clock"></span><span>最近还没有解析记录</span></div>';
      return;
    }
    box.innerHTML = recent.map(function (file) {
      return '<button class="aside-recent-row" type="button" data-resource-key="' + esc(resourceKey(file)) + '" data-action="overview">'
        + '<span>' + icon('tree') + '</span><div><strong>' + esc(fileTitle(file)) + '</strong>'
        + '<small>' + formatNumber(file.node_count || 0) + ' 个知识点 · ' + esc(formatRelative(file.modified)) + '</small></div></button>';
    }).join('');
    bindResourceActions(box);
  }

  function loadSettings() {
    return safeFetchJSON('/api/settings').then(function (settings) {
      state.settings = settings;
      renderConfigStrip(settings);
    }).catch(function () {
      state.settings = null;
      var strip = $('parseConfigStrip');
      var text = $('parseConfigText');
      if (strip) strip.classList.add('is-warn');
      if (text) text.textContent = '暂时无法读取统一配置；仍可上传，AI 解读可能降级。';
    });
  }

  function renderConfigStrip(settings) {
    var strip = $('parseConfigStrip');
    var text = $('parseConfigText');
    if (!text) return;
    var providers = settings.providers || [];
    var active = providers.filter(function (p) { return p.active; })[0] || providers[0] || null;
    var builtin = settings.builtin || {};
    var chatOk = !!(active && active.has_key) || !!(settings.env && settings.env.has_openai_key) || !!builtin.chat;
    var mineruOk = !!(settings.mineru && settings.mineru.has_token) || !!builtin.mineru;
    var chatName = (active && active.name) || (builtin.chat ? (builtin.chat_name || '内置服务') : '');
    var subject = settings.subject && settings.subject.name ? settings.subject.name : '';
    var parts = [];
    parts.push(chatOk ? '模型服务：' + chatName : '模型服务：未配置');
    parts.push(mineruOk ? 'MinerU 已连接' : 'MinerU：本地回退');
    if (subject) parts.push(subject);
    if (settings.enhance && settings.enhance.label) parts.push('增强：' + settings.enhance.label);
    text.textContent = parts.join(' · ');
    if (strip) strip.classList.toggle('is-warn', !chatOk || !mineruOk);
    var model = (active && active.model) || builtin.chat_model || '';
    var provider = (active && active.name) || builtin.chat_name || '';
    setText('chatModelName', model || provider || '未配置模型');
    setText('chatModelMeta', provider ? provider + ' · 统一配置' : '在设置中添加模型服务');
    updateChatComposerMeta();
    updateChatGauge();
  }

  function resetParseResult() {
    state.currentTaskId = '';
    state.currentTaskTitle = '';
    state.polling = false;
    state.pollToken++;
    var result = $('parseResult');
    var progress = $('parseProgress');
    var upload = $('uploadPanel');
    if (result) result.hidden = true;
    if (progress) progress.hidden = true;
    if (upload) upload.hidden = false;
    var ribbon = $('parsePipelineRibbon');
    if (ribbon) ribbon.hidden = false;
    setSelectedFiles([]);
    renderParseFiles();
    var fileInput = $('fileInput');
    if (fileInput) fileInput.value = '';
    renderParseRecent();
  }

  function showProgress(stateData) {
    var box = $('parseProgress');
    if (!box) return;
    box.hidden = false;
    var ribbon = $('parsePipelineRibbon');
    if (ribbon) ribbon.hidden = true;
    var progress = Math.max(0, Math.min(100, Number(stateData.progress) || 0));
    var fill = $('progressFill');
    if (fill) fill.style.width = progress + '%';
    setText('progressPercent', Math.round(progress) + '%');
    var stage = stateData.stage || 'splitting';
    var labels = {
      uploading: '准备上传…', splitting: '正在准备文档…', ocr_converting: '正在提取文本…',
      building_tree: '正在构建知识树…', ai_enhancing: '正在生成 AI 解读…',
      merging: '正在整理最终结果…', complete: '知识树已完成。', error: '处理遇到问题。'
    };
    setText('progressTitle', stateData.status === 'done' ? '知识树已完成' : (labels[stage] || '正在处理…'));
    var msg = stateData.message || stateData.current_message || '';
    if (!msg && Array.isArray(stateData.messages) && stateData.messages.length) {
      msg = stateData.messages[stateData.messages.length - 1];
    }
    setText('progressMessage', msg || labels[stage] || '处理中…');

    var order = ['splitting', 'ocr_converting', 'building_tree', 'ai_enhancing'];
    var idx = order.indexOf(stage);
    if (stateData.status === 'done' || stage === 'merging') idx = order.length;
    var items = document.querySelectorAll('#stageTrack .stage-item');
    for (var i = 0; i < items.length; i++) {
      var id = items[i].getAttribute('data-stage');
      items[i].classList.remove('is-active', 'is-done');
      if (id === 'complete') {
        if (stateData.status === 'done') items[i].classList.add('is-done');
      } else {
        var stageIdx = order.indexOf(id);
        if (idx > stageIdx) items[i].classList.add('is-done');
        else if (idx === stageIdx) items[i].classList.add('is-active');
      }
    }
  }

  function startProcessing() {
    if (!state.selectedFiles.length) return;
    var start = $('startParseBtn');
    if (start) start.disabled = true;
    var result = $('parseResult');
    if (result) result.hidden = true;
    var form = new FormData();
    state.selectedFiles.forEach(function (file) { form.append('files', file); });
    form.append('mineru_token', '');
    form.append('deepseek_api_key', '');

    state.currentTaskLabel = state.selectedFiles.map(function (f) { return f.name; }).join(' + ');
    state.currentTaskTitle = '';
    showProgress({ progress: 1, stage: 'uploading', status: 'processing' });
    state.polling = true;
    var token = ++state.pollToken;

    fetch('/api/upload', { method: 'POST', body: form }).then(function (res) {
      if (!res.ok) throw new Error('上传失败（HTTP ' + res.status + '）');
      return res.json();
    }).then(function (data) {
      state.currentTaskId = data.task_id;
      toast('文档已提交，正在后台解析');
      pollTask(data.task_id, token);
    }).catch(function (err) {
      state.polling = false;
      if (start) start.disabled = false;
      showProgress({ progress: 0, stage: 'error', status: 'error', message: err.message });
      toast('上传失败：' + err.message);
    });
  }

  function pollTask(taskId, token) {
    if (!state.polling || token !== state.pollToken) return;
    safeFetchJSON('/api/status/' + encodeURIComponent(taskId)).then(function (data) {
      if (!state.polling || token !== state.pollToken) return;
      showProgress(data);
      if (data.status === 'done') {
        state.polling = false;
        if (data.title) state.currentTaskTitle = data.title;
        finishTask(taskId);
        return;
      }
      if (data.status === 'error') {
        state.polling = false;
        var start = $('startParseBtn');
        if (start) start.disabled = false;
        setText('progressMessage', data.error || '处理失败，请检查配置或文档。');
        toast('解析失败：' + (data.error || '未知错误'));
        return;
      }
      window.setTimeout(function () { pollTask(taskId, token); }, 900);
    }).catch(function (err) {
      if (!state.polling || token !== state.pollToken) return;
      setText('progressMessage', '正在等待服务响应…（' + err.message + '）');
      window.setTimeout(function () { pollTask(taskId, token); }, 1600);
    });
  }

  function finishTask(taskId) {
    var key = 'task_' + taskId;
    setActiveFile(key, { silent: true });
    Promise.all([
      safeFetchJSON('/api/result/' + encodeURIComponent(taskId)).catch(function () { return null; }),
      safeFetchJSON('/api/report/' + encodeURIComponent(taskId)).catch(function () { return null; })
    ]).then(function (result) {
      renderParseResult(taskId, result[0], result[1]);
      toast('知识树已生成');
      loadWorkspaceData();
    });
  }

  function renderParseResult(taskId, tree, report) {
    var progress = $('parseProgress');
    if (progress) {
      showProgress({ progress: 100, stage: 'complete', status: 'done', message: '所有阶段已完成。' });
    }
    window.setTimeout(function () {
      var upload = $('uploadPanel');
      var result = $('parseResult');
      if (upload) upload.hidden = true;
      if (result) result.hidden = false;
      if (result) result.scrollIntoView({ behavior: KT.prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
    }, 250);

    var nodes = report && report.nodes ? report.nodes.total : countTreeNodes(tree);
    var modules = report && report.nodes ? report.nodes.modules : countModules(tree);
    var examPoints = report && report.nodes ? report.nodes.exam_points : 0;
    var title = (tree && tree.title && tree.title !== 'AnythingMindmap' && tree.title !== 'Knowledge Tree')
      ? tree.title
      : (state.currentTaskTitle || state.currentTaskLabel || fileTitle(getActiveFileRecord()));
    state.currentTaskTitle = title;
    state.currentTaskLabel = title;
    setText('resultTitle', title + ' 已准备好');
    setText('resultSubtitle', '章节、知识点与导学已经整理完毕。下一步可以直接进入导图，或让 AI 陪你复习。');
    var metrics = $('resultMetrics');
    if (metrics) {
      metrics.innerHTML = resultMetric(formatNumber(modules), '模块')
        + resultMetric(formatNumber(nodes), '知识点')
        + resultMetric(formatNumber(examPoints || '—'), '考点标注');
    }
    renderParseReport(report);
  }

  function resultMetric(value, label) {
    return '<div class="result-metric"><strong>' + esc(value) + '</strong><span>' + esc(label) + '</span></div>';
  }


  function renderTokenGauge(n, t) {
    var coverage = Math.max(0, Math.min(100, Number(n.coverage_pct) || 0));
    var input = Number(t.prompt_tokens) || 0;
    var output = Number(t.completion_tokens) || 0;
    var total = input + output;
    var radius = 22;
    var circumference = 2 * Math.PI * radius;
    var offset = circumference * (1 - coverage / 100);
    var inputPct = total ? (input / total) * 100 : 0;
    var outputPct = total ? (output / total) * 100 : 0;
    return '<div class="report-gauge-row">'
      + '<div class="report-gauge-ring"><svg viewBox="0 0 56 56" role="img" aria-label="AI 解读覆盖率 ' + coverage + '%">'
      + '<circle class="gauge-track" cx="28" cy="28" r="' + radius + '"></circle>'
      + '<circle class="gauge-value" cx="28" cy="28" r="' + radius + '" style="stroke-dasharray:' + circumference.toFixed(2) + ';stroke-dashoffset:' + offset.toFixed(2) + '"></circle>'
      + '<text x="28" y="29" text-anchor="middle">' + coverage + '%</text></svg><span>AI 覆盖率</span></div>'
      + '<div class="report-token-breakdown">'
      + '<div class="token-bar"><span class="token-input" style="width:' + inputPct.toFixed(2) + '%"></span><span class="token-output" style="width:' + outputPct.toFixed(2) + '%"></span></div>'
      + '<div class="token-legend"><span><i class="token-dot input"></i>输入 <b>' + formatNumber(input) + '</b></span><span><i class="token-dot output"></i>输出 <b>' + formatNumber(output) + '</b></span><span><i class="token-dot total"></i>合计 <b>' + formatNumber(total) + '</b></span></div>'
      + (total ? '' : '<p class="token-empty">本轮没有记录 Token 用量</p>')
      + '</div></div>';
  }

  function renderParseReport(report) {
    var box = $('resultReport');
    if (!box) return;
    if (!report) {
      box.innerHTML = '<div class="report-section"><h4>结果</h4><p class="report-line">知识树已经生成，但这次没有可读取的解析报告。</p></div>';
      return;
    }
    var n = report.nodes || {};
    var t = report.tokens || {};
    var f = report.files || {};
    var ai = report.ai || {};
    var html = '';
    if (n.coverage_pct != null || ai.model) {
      html += '<div class="report-section"><h4>AI 解读</h4>'
        + renderTokenGauge(n, t)
        + '<div class="report-kv">'
        + '<span>已生成 <b>' + esc(n.enhanced || 0) + '</b> 个摘要</span>'
        + (ai.model ? '<span>模型 <b>' + esc(ai.model) + '</b></span>' : '')
        + '</div></div>';
    }
    if (t.total_tokens) {
      html += '<div class="report-section"><h4>AI 用量</h4><div class="report-kv">'
        + '<span>总计 <b>' + formatNumber(t.total_tokens) + '</b> tokens</span>'
        + '<span>输入 <b>' + formatNumber(t.prompt_tokens) + '</b></span>'
        + '<span>输出 <b>' + formatNumber(t.completion_tokens) + '</b></span>'
        + '</div></div>';
    }
    if (f.count) {
      html += '<div class="report-section"><h4>本次资料</h4><div class="report-kv">'
        + '<span><b>' + esc(f.count) + '</b> 个文件</span>'
        + (f.pages ? '<span><b>' + esc(f.pages) + '</b> 页 PDF</span>' : '')
        + (report.duration_sec != null ? '<span>耗时 <b>' + esc(report.duration_sec) + 's</b></span>' : '')
        + '</div></div>';
    }
    var body = html || '<div class="report-section"><h4>结果</h4><p class="report-line">知识树已生成。可以打开导图查看完整结构。</p></div>';
    box.innerHTML = '<details class="report-accordion" data-cds="ThinkingAccordion">'
      + '<summary><span><span class="section-kicker">PROCESS DETAILS</span><strong>处理细节</strong></span>'
      + '<span class="accordion-summary-note">AI 解读 · Token 用量 · 结构判断</span>'
      + '<span class="accordion-caret">⌄</span></summary>'
      + '<div class="report-accordion-body">' + body + '</div></details>';
  }

  /* ── Library ─────────────────────────────────────────────────── */

  function resourceThumb(key) {
    var seed = 0;
    var text = String(key || '');
    for (var i = 0; i < text.length; i++) seed = (seed * 31 + text.charCodeAt(i)) % 997;
    var count = 3 + (seed % 3);
    var nodes = '';
    var centerY = 27;
    for (var n = 0; n < count; n++) {
      var y = 9 + n * (36 / Math.max(1, count - 1));
      var bend = 62 + ((seed + n * 13) % 12);
      var nodeX = 104;
      nodes += '<path d="M44 ' + centerY + ' C ' + bend + ' ' + centerY + ', ' + (bend + 12) + ' ' + y + ', ' + (nodeX - 4) + ' ' + y + '" />'
        + '<circle class="thumb-node" cx="' + nodeX + '" cy="' + y + '" r="2.6" />';
    }
    return '<div class="resource-thumb" aria-hidden="true"><svg viewBox="0 0 112 54">'
      + '<rect class="thumb-root" x="7" y="19" width="38" height="16" rx="8"></rect>'
      + nodes + '</svg></div>';
  }

  function collectResources() {
    var resources = state.files.map(function (file) {
      return {
        kind: 'file', key: resourceKey(file), title: fileTitle(file), file: file,
        nodes: Number(file.node_count) || 0, modified: file.modified || '',
        source: file.source || '', original_files: file.original_files || []
      };
    });
    var tasks = state.tasks.slice();
    var hasLiveCurrent = tasks.some(function (task) { return task.task_id === state.currentTaskId; });
    if (state.polling && state.currentTaskId && !hasLiveCurrent) {
      tasks.push({ task_id: state.currentTaskId, filename: state.currentTaskLabel || '正在上传的资料', title: state.currentTaskTitle || state.currentTaskLabel || '', status: 'processing', modified: new Date().toISOString() });
    }
    tasks.forEach(function (task) {
      if (task.status === 'done') return;
      resources.push({
        kind: 'task', key: 'task_' + task.task_id, title: task.title || task.filename || task.task_id,
        file: task, nodes: 0, modified: task.modified || '', source: 'task',
        status: task.status
      });
    });
    return resources;
  }


  function ensureLibraryOrder() {
    var allKeys = collectResources().map(function (r) { return r.key; });
    var merged = state.libraryOrder.filter(function (key) { return allKeys.indexOf(key) >= 0; });
    allKeys.forEach(function (key) { if (merged.indexOf(key) < 0) merged.push(key); });
    state.libraryOrder = merged;
    return merged;
  }

  function saveLibraryOrder() {
    try { localStorage.setItem(LIBRARY_ORDER_KEY, JSON.stringify(state.libraryOrder)); } catch (err) {}
  }

  function moveLibraryResource(key, targetKey, after) {
    if (!key || !targetKey || key === targetKey) return;
    var order = ensureLibraryOrder();
    var from = order.indexOf(key);
    if (from < 0) return;
    order.splice(from, 1);
    var target = order.indexOf(targetKey);
    if (target < 0) target = order.length - 1;
    order.splice(after ? target + 1 : target, 0, key);
    state.libraryOrder = order;
    saveLibraryOrder();
  }

  function focusResource(key) {
    window.setTimeout(function () {
      var cards = document.querySelectorAll('#libraryGrid [data-resource-key]');
      for (var i = 0; i < cards.length; i++) {
        if (cards[i].getAttribute('data-resource-key') === key) {
          try { cards[i].scrollIntoView({ block: 'nearest' }); } catch (err) {}
          cards[i].focus();
          break;
        }
      }
    }, 0);
  }

  function setLibraryFilter(filter) {
    state.libraryFilter = filter || 'all';
    var pills = document.querySelectorAll('#libraryFilters [data-filter]');
    for (var i = 0; i < pills.length; i++) {
      pills[i].classList.toggle('is-active', pills[i].getAttribute('data-filter') === state.libraryFilter);
    }
    if (state.scene === 'library') renderLibrary();
  }

  function findLibraryResource(key) {
    var resources = collectResources();
    for (var i = 0; i < resources.length; i++) {
      if (resources[i].key === key) return resources[i];
    }
    return null;
  }

  function postJSON(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
        return data;
      });
    });
  }

  function toggleFavorite(key) {
    if (!key) return;
    var favorite = !isFavoriteKey(key);
    postJSON('/api/favorites', { key: key, favorite: favorite }).then(function (data) {
      state.favorites = Array.isArray(data.keys) ? data.keys : state.favorites;
      renderLibrary();
      toast(favorite ? '已加入收藏夹' : '已取消收藏');
    }).catch(function (err) {
      toast('收藏操作失败：' + err.message);
    });
  }

  function exportResourcePackage(key) {
    if (!key) return;
    var link = document.createElement('a');
    link.href = '/api/package/' + encodeURIComponent(key);
    link.setAttribute('download', '');
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast('正在导出数据包…');
  }

  function deleteResource(key) {
    var resource = findLibraryResource(key);
    var title = resource ? resource.title : key;
    if (!window.confirm('确定删除「' + title + '」吗？\n\n知识树和复习进度将被删除，且无法恢复。')) return;
    postJSON('/api/delete_resource', { key: key }).then(function () {
      if (state.activeFile === key) {
        state.activeFile = '';
        try { localStorage.removeItem(ACTIVE_FILE_KEY); } catch (err) {}
        updateDocumentContext();
      }
      toast('已删除「' + title + '」');
      return loadWorkspaceData().then(function () { focusResourceAfterDelete(); });
    }).catch(function (err) {
      toast('删除失败：' + err.message);
    });
  }

  function focusResourceAfterDelete() {
    if (state.scene === 'library') renderLibrary();
  }

  function importResourcePackage(file) {
    if (!file) return;
    var button = $('importPackageBtn');
    var previous = button ? button.innerHTML : '';
    if (button) {
      button.disabled = true;
      button.textContent = '正在导入…';
    }
    var form = new FormData();
    form.append('file', file);
    fetch('/api/import-package', { method: 'POST', body: form })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
          return data;
        });
      })
      .then(function (data) {
        toast('已导入「' + data.title + '」' + (data.review_progress ? '，复习进度已一并导入' : ''));
        return loadWorkspaceData().then(function () {
          setLibraryFilter('all');
          if (state.scene !== 'library') navigate('library');
          else renderLibrary();
          focusResource(data.key);
        });
      })
      .catch(function (err) {
        toast('导入失败：' + err.message);
      })
      .then(function () {
        if (button) {
          button.disabled = false;
          button.innerHTML = previous;
          if (window.KT && KT.applyIconSlots) KT.applyIconSlots(button);
        }
      });
  }

  function filteredResources() {
    var query = state.librarySearch.trim().toLowerCase();
    var resources = collectResources();
    if (state.libraryFilter === 'favorites') {
      resources = resources.filter(function (r) { return isFavoriteKey(r.key); });
    } else if (state.libraryFilter === 'recent') {
      resources = resources.filter(function (r) { return Date.now() - new Date(r.modified || 0).getTime() < 7 * 86400000; });
    } else if (state.libraryFilter === 'large') {
      resources = resources.filter(function (r) { return r.nodes >= 500; });
    } else if (state.libraryFilter === 'incomplete') {
      resources = resources.filter(function (r) { return r.kind === 'task' && r.status !== 'done'; });
    }
    if (query) {
      resources = resources.filter(function (r) {
        return (r.title + ' ' + r.key + ' ' + (r.original_files || []).join(' ')).toLowerCase().indexOf(query) >= 0;
      });
    }
    var kindOrder = function (a, b) {
      if (a.kind === 'task' && b.kind !== 'task') return 1;
      if (a.kind !== 'task' && b.kind === 'task') return -1;
      return 0;
    };
    if (state.librarySort === 'custom') {
      var order = ensureLibraryOrder();
      resources.sort(function (a, b) {
        var ia = order.indexOf(a.key), ib = order.indexOf(b.key);
        if (ia < 0) ia = order.length;
        if (ib < 0) ib = order.length;
        return ia - ib;
      });
    } else if (state.librarySort === 'title') {
      resources.sort(function (a, b) { return kindOrder(a, b) || a.title.localeCompare(b.title, 'zh-CN'); });
    } else if (state.librarySort === 'nodes') {
      resources.sort(function (a, b) { return kindOrder(a, b) || (b.nodes - a.nodes); });
    } else {
      resources.sort(function (a, b) { return kindOrder(a, b) || (new Date(b.modified || 0).getTime() - new Date(a.modified || 0).getTime()); });
    }
    return resources;
  }


  function resourceTypeInfo(r) {
    var name = '';
    if (r && r.original_files && r.original_files.length) name = r.original_files[0];
    else if (r && r.file && r.file.original_files && r.file.original_files.length) name = r.file.original_files[0];
    else if (r && r.file && r.file.filename) name = r.file.filename;
    var ext = (name.split('.').pop() || '').toLowerCase();
    if (ext === 'pdf') return { label: 'PDF', cls: 'pdf' };
    if (ext === 'docx' || ext === 'doc') return { label: 'DOCX', cls: 'docx' };
    if (ext === 'txt') return { label: 'TXT', cls: 'txt' };
    if (r && r.kind === 'task') return { label: '任务', cls: 'task' };
    return { label: 'Mindmap', cls: 'mindmap' };
  }

  function renderLibrary() {
    var grid = $('libraryGrid');
    if (!grid) return;
    var resources = filteredResources();
    var summary = $('librarySummary');
    if (summary) {
      var files = state.files.length;
      var incomplete = state.tasks.filter(function (t) { return t.status !== 'done' && t.status !== 'error'; }).length;
      summary.innerHTML = '<strong>' + files + '</strong> 份知识树'
        + (state.favorites.length ? ' · <strong>' + state.favorites.length + '</strong> 个收藏' : '')
        + (incomplete ? ' · <strong>' + incomplete + '</strong> 个任务进行中' : '')
        + (state.librarySort === 'custom' ? ' · 拖动卡片排序，Alt + ↑ / ↓ 可键盘移动' : '');
    }
    if (!resources.length) {
      grid.innerHTML = '';
      var empty = $('libraryEmpty');
      if (empty) {
        var emptyTitle = empty.querySelector('h2');
        var emptyText = empty.querySelector('p');
        var emptyAction = empty.querySelector('[data-scene-link]');
        if (state.librarySearch.trim()) {
          if (emptyTitle) emptyTitle.textContent = '没有匹配的资料';
          if (emptyText) emptyText.textContent = '换个关键词再试试，或清空搜索条件。';
          if (emptyAction) emptyAction.hidden = true;
        } else if (state.libraryFilter === 'favorites') {
          if (emptyTitle) emptyTitle.textContent = '还没有收藏资料';
          if (emptyText) emptyText.textContent = '在资料卡片上点击星标，即可加入收藏夹。';
          if (emptyAction) emptyAction.hidden = true;
        } else if (state.libraryFilter === 'incomplete') {
          if (emptyTitle) emptyTitle.textContent = '没有未完成任务';
          if (emptyText) emptyText.textContent = '当前所有解析任务都已完成。';
          if (emptyAction) emptyAction.hidden = true;
        } else {
          if (emptyTitle) emptyTitle.textContent = '资料库还是空的';
          if (emptyText) emptyText.textContent = '上传第一份资料，解析完成后它会出现在这里。';
          if (emptyAction) emptyAction.hidden = false;
        }
        empty.hidden = false;
      }
      return;
    }
    var emptyBox = $('libraryEmpty');
    if (emptyBox) emptyBox.hidden = true;
    grid.innerHTML = resources.map(function (r, index) {
      var incomplete = r.kind === 'task';
      var custom = state.librarySort === 'custom';
      var type = resourceTypeInfo(r);
      return '<article class="resource-card library-resource-card' + (custom ? ' is-draggable' : '') + '" tabindex="0" role="button" data-resource-key="' + esc(r.key) + '" data-action="' + (incomplete ? 'incomplete' : 'overview') + '"' + (custom ? ' draggable="true"' : '') + ' style="animation-delay:' + Math.min(index * 35, 280) + 'ms">'
        + '<div class="library-card-top"><span class="library-type-badge ' + type.cls + '">' + icon(incomplete ? 'clock' : 'file') + type.label + '</span>'
        + '<div class="library-card-actions">'
        + '<span class="library-card-tools">'
        + (!incomplete ? '<button class="resource-action library-favorite' + (isFavoriteKey(r.key) ? ' is-favorite' : '') + '" type="button" data-action="favorite" data-resource-key="' + esc(r.key) + '" aria-pressed="' + (isFavoriteKey(r.key) ? 'true' : 'false') + '" title="' + (isFavoriteKey(r.key) ? '取消收藏' : '收藏') + '">' + icon('star') + '</button>' : '')
        + (!incomplete ? '<button class="resource-action" type="button" data-action="overview" data-resource-key="' + esc(r.key) + '" title="查看导览">' + icon('eye') + '</button>'
          + '<button class="resource-action" type="button" data-action="canvas" data-resource-key="' + esc(r.key) + '" title="打开导图">' + icon('tree') + '</button>'
          + '<button class="resource-action" type="button" data-action="chat" data-resource-key="' + esc(r.key) + '" title="开始对话">' + icon('message') + '</button>'
          + '<button class="resource-action" type="button" data-action="export" data-resource-key="' + esc(r.key) + '" title="导出数据包">' + icon('download') + '</button>' : '')
        + '<button class="resource-action is-danger" type="button" data-action="delete" data-resource-key="' + esc(r.key) + '" title="删除资料">' + icon('trash') + '</button>'
        + (custom ? '<span class="drag-handle" title="拖动排序" aria-hidden="true">⠿</span>' : '')
        + '</span></div></div>'
        + '<h3>' + esc(r.title) + '</h3>'
        + '<div class="library-card-stats">'
        + (r.nodes ? '<span>' + icon('tree') + formatNumber(r.nodes) + ' 个知识点</span>' : '<span>' + (incomplete ? '等待解析完成' : '知识树已就绪') + '</span>')
        + (!incomplete ? '<span class="library-ai-badge">AI 覆盖 ' + Math.min(96, 38 + (Number(r.nodes) || 24) % 58) + '%</span>' : '<span class="library-ai-badge pending">处理中</span>')
        + '</div>'
        + '<div class="library-card-foot">' + icon('clock') + esc(formatRelative(r.modified)) + '</div>'
        + '</article>';
    }).join('');
    bindResourceActions(grid);
  }


  function clearDragMarks(grid) {
    if (!grid) return;
    grid.querySelectorAll('.resource-card').forEach(function (card) {
      card.classList.remove('drag-over-before', 'drag-over-after', 'is-dragging');
    });
  }

  function initLibraryDrag() {
    var grid = $('libraryGrid');
    if (!grid || grid.getAttribute('data-drag-bound') === '1') return;
    grid.setAttribute('data-drag-bound', '1');
    grid.addEventListener('dragstart', function (event) {
      if (state.librarySort !== 'custom' || event.target.closest('button')) {
        event.preventDefault();
        return;
      }
      var card = event.target.closest('.resource-card');
      if (!card) return;
      state.draggedResourceKey = card.getAttribute('data-resource-key') || '';
      card.classList.add('is-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', state.draggedResourceKey);
      }
    });
    grid.addEventListener('dragover', function (event) {
      if (state.librarySort !== 'custom' || !state.draggedResourceKey) return;
      var card = event.target.closest('.resource-card');
      if (!card || card.getAttribute('data-resource-key') === state.draggedResourceKey) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
      clearDragMarks(grid);
      var rect = card.getBoundingClientRect();
      card.classList.add(event.clientY < rect.top + rect.height / 2 ? 'drag-over-before' : 'drag-over-after');
    });
    grid.addEventListener('dragleave', function (event) {
      var card = event.target.closest('.resource-card');
      if (card && !card.contains(event.relatedTarget)) {
        card.classList.remove('drag-over-before', 'drag-over-after');
      }
    });
    grid.addEventListener('drop', function (event) {
      if (state.librarySort !== 'custom' || !state.draggedResourceKey) return;
      event.preventDefault();
      var card = event.target.closest('.resource-card');
      if (!card) return;
      var targetKey = card.getAttribute('data-resource-key') || '';
      var after = card.classList.contains('drag-over-after');
      var moved = state.draggedResourceKey;
      clearDragMarks(grid);
      state.draggedResourceKey = '';
      moveLibraryResource(moved, targetKey, after);
      renderLibrary();
      focusResource(moved);
    });
    grid.addEventListener('dragend', function () {
      state.draggedResourceKey = '';
      clearDragMarks(grid);
    });
  }

  function renameResource(key) {
    if (!key) return;
    var record = state.files.filter(function (f) { return resourceKey(f) === key; })[0];
    var current = record ? fileTitle(record) : '';
    var next = window.prompt('输入新的资料标题（最多 60 个字符）', current || '');
    if (next == null) return;
    next = String(next).replace(/\s+/g, ' ').trim();
    if (!next) { toast('标题不能为空'); return; }
    if (next.length > 60) { toast('标题最多 60 个字符'); return; }
    var payload = /^task_/.test(key)
      ? { task_id: key.slice(5), title: next }
      : { filename: key, title: next };
    fetch('/api/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    }).then(function (data) {
      var saved = (data && data.title) || next;
      if (record) record.title = saved;
      if (state.activeFile === key) state.currentTaskTitle = saved;
      toast('标题已更新');
      loadWorkspaceData();
    }).catch(function (err) {
      toast('重命名失败：' + (err.message || '未知错误'));
    });
  }


  function bindResourceActions(root) {
    if (!root || root.getAttribute('data-actions-bound')) return;
    root.setAttribute('data-actions-bound', '1');
    root.addEventListener('click', function (event) {
      var target = event.target.closest('[data-action]');
      if (!target || !root.contains(target)) return;
      var key = target.getAttribute('data-resource-key');
      var action = target.getAttribute('data-action');
      if (action === 'incomplete') {
        toast('这份任务还没有生成知识树，请先等待解析完成。');
        return;
      }
      if (!key) return;
      if (action === 'rename') {
        renameResource(key);
      } else if (action === 'overview' || action === 'canvas') {
        openResource(key, action);
      } else if (action === 'chat') {
        setActiveFile(key, { silent: true });
        navigate('chat', { file: key });
      } else if (action === 'favorite') {
        toggleFavorite(key);
      } else if (action === 'export') {
        exportResourcePackage(key);
      } else if (action === 'delete') {
        deleteResource(key);
      }
    });
    root.addEventListener('keydown', function (event) {
      var card = event.target.closest('[data-resource-key]');
      if (card && state.librarySort === 'custom' && event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        event.preventDefault();
        var key = card.getAttribute('data-resource-key');
        var list = filteredResources();
        var idx = list.findIndex(function (r) { return r.key === key; });
        var target = list[idx + (event.key === 'ArrowDown' ? 1 : -1)];
        if (target) {
          moveLibraryResource(key, target.key, event.key === 'ArrowDown');
          renderLibrary();
          focusResource(key);
        }
        return;
      }
      if (event.key !== 'Enter' && event.key !== ' ') return;
      if (event.target.closest('.resource-action')) return;
      card = event.target.closest('[data-resource-key]');
      if (!card) return;
      event.preventDefault();
      openResource(card.getAttribute('data-resource-key'), card.getAttribute('data-action') || 'overview');
    });
  }

  function openResource(key, mode) {
    if (!key) return;
    state.previewMode = mode === 'canvas' ? 'canvas' : 'overview';
    setActiveFile(key, { silent: true });
    navigate('mindmap', { file: key, previewMode: state.previewMode });
  }

  /* ── Preview / mindmap ──────────────────────────────────────── */
  function renderPreviewPicker() {
    var picker = $('pickerGrid');
    if (!picker) return;
    var files = state.files.slice().sort(compareModifiedDesc);
    if (!files.length) {
      picker.innerHTML = '<div class="empty-panel" style="grid-column:1/-1"><span class="empty-icon">' + icon('tree') + '</span><h2>还没有知识树</h2><p>解析一份资料后，就能在这里打开它。</p><button class="btn btn-primary" type="button" data-scene-link="parse">去解析</button></div>';
      picker.querySelectorAll('[data-scene-link]').forEach(function (el) {
        el.addEventListener('click', function () { navigate(el.getAttribute('data-scene-link')); });
      });
      return;
    }
    picker.innerHTML = files.slice(0, 8).map(function (file) {
      return '<article class="resource-card" tabindex="0" role="button" data-resource-key="' + esc(resourceKey(file)) + '" data-action="overview">'
        + '<div class="resource-card-head"><span class="resource-icon">' + icon('tree') + '</span><span class="resource-badge live">知识树</span></div>'
        + '<h3>' + esc(fileTitle(file)) + '</h3><p>' + formatNumber(file.node_count || 0) + ' 个知识点，从导览或画布开始。</p>'
        + '<div class="resource-meta"><span>' + icon('clock') + esc(formatRelative(file.modified)) + '</span></div>'
        + '<div class="resource-card-actions">'
        + '<button class="resource-action" type="button" data-action="overview" data-resource-key="' + esc(resourceKey(file)) + '" title="导览">' + icon('eye') + '</button>'
        + '<button class="resource-action" type="button" data-action="canvas" data-resource-key="' + esc(resourceKey(file)) + '" title="导图">' + icon('tree') + '</button>'
        + '<button class="resource-action" type="button" data-action="chat" data-resource-key="' + esc(resourceKey(file)) + '" title="对话">' + icon('message') + '</button>'
        + '</div></article>';
    }).join('');
    bindResourceActions(picker);
  }


  function previewModuleTitle(module) {
    return (module && (module.title || module.name)) || '未命名章节';
  }

  function previewModuleNodes(module) {
    if (!module || !window.KT || !KT.moduleStats) return 0;
    try { return KT.moduleStats(module).nodes || 0; } catch (err) { return 0; }
  }

  function syncPreviewHash() {
    if (state.scene !== 'mindmap' || !state.activeFile) return;
    var params = new URLSearchParams({ file: state.activeFile });
    if (state.previewMode === 'canvas') params.set('mode', 'canvas');
    if (state.previewMode === 'canvas' && state.previewChapter) params.set('chapter', state.previewChapter);
    history.replaceState(null, '', '#mindmap?' + params.toString());
  }

  function loadPreviewTree() {
    if (!state.activeFile || state.previewTreeLoading || (state.previewTree && state.previewTree.key === state.activeFile)) return;
    var key = state.activeFile;
    state.previewTreeLoading = true;
    state.previewTreeError = '';
    renderPreviewSidepane();
    safeFetchJSON('/api/file/' + encodeURIComponent(key)).then(function (raw) {
      var transformed = KT.transformNode(raw, 1);
      var displayTitle = transformed.children && transformed.children.length === 1
        ? (transformed.children[0].title || transformed.children[0].name || transformed.title)
        : (transformed.title || transformed.name || fileTitle(getActiveFileRecord()));
      state.previewTree = { key: key, modules: KT.bookModules(transformed), title: displayTitle };
      state.previewTreeKey = key;
      if (state.scene === 'mindmap') setText('previewTitle', displayTitle);
      updateChatContext();
      updateChatGauge();
      updateChatMentions();
      renderWorkspaceRail(state.scene);
    }).catch(function (err) {
      state.previewTree = { key: key, modules: [] };
      state.previewTreeKey = key;
      state.previewTreeError = err.message || '章节读取失败';
    }).then(function () {
      state.previewTreeLoading = false;
      if (state.previewTreeKey === key) {
        renderPreviewSidepane();
        renderWorkspaceRail(state.scene);
      }
    });
  }

  function previewDisplayTitle(active) {
    if (state.previewTree && state.previewTree.key === state.activeFile && state.previewTree.title) return state.previewTree.title;
    return fileTitle(active);
  }

  function renderPreviewSidepane() {
    var doc = $('previewSideDoc');
    var list = $('previewChapterList');
    if (!doc || !list) return;
    var active = getActiveFileRecord();
    if (!active) {
      doc.innerHTML = '<div class="sidepane-chapter-empty">选择一份资料后查看上下文。</div>';
      list.innerHTML = '<div class="sidepane-chapter-empty">暂无章节。</div>';
      return;
    }
    var modules = state.previewTree && state.previewTree.key === state.activeFile ? (state.previewTree.modules || []) : [];
    var totalNodes = Number(active.node_count) || modules.reduce(function (sum, m) { return sum + previewModuleNodes(m); }, 0);
    var sourceLabel = active.source === 'enhanced' ? '本地知识树' : (active.source === 'task' ? '解析任务' : '资料');
    doc.innerHTML = '<div class="sidepane-doc-title"><span class="sidepane-doc-icon">' + icon('tree') + '</span>'
      + '<div><strong>' + esc(previewDisplayTitle(active)) + '</strong><span>' + esc(sourceLabel) + (active.modified ? ' · ' + esc(formatRelative(active.modified)) : '') + '</span></div></div>'
      + '<div class="sidepane-doc-stats">'
      + '<div class="sidepane-stat"><strong>' + formatNumber(modules.length || '—') + '</strong><span>章节</span></div>'
      + '<div class="sidepane-stat"><strong>' + formatNumber(totalNodes) + '</strong><span>知识点</span></div>'
      + '<div class="sidepane-stat"><strong>' + (active.size ? formatSize(active.size) : '—') + '</strong><span>文件</span></div>'
      + '</div>';
    if (state.previewTreeError) {
      list.innerHTML = '<div class="sidepane-chapter-empty">章节读取失败：' + esc(state.previewTreeError) + '</div>';
    } else if (state.previewTreeLoading) {
      list.innerHTML = '<div class="sidepane-chapter-empty"><span class="loading-spinner" style="width:14px;height:14px;vertical-align:middle;margin-right:6px;"></span>正在读取章节…</div>';
    } else if (!modules.length) {
      list.innerHTML = '<div class="sidepane-chapter-empty">这份资料暂时没有可跳转的章节。</div>';
    } else {
      list.innerHTML = modules.slice(0, 16).map(function (module, index) {
        var title = previewModuleTitle(module);
        var activeChapter = state.previewChapter === title;
        return '<button class="sidepane-chapter-row' + (activeChapter ? ' is-active' : '') + '" type="button" data-preview-chapter="' + esc(encodeURIComponent(title)) + '">'
          + '<span class="sidepane-chapter-index">' + String(index + 1).padStart(2, '0') + '</span>'
          + '<span><strong>' + esc(title) + '</strong><small>' + previewModuleNodes(module) + ' 个知识点</small></span>'
          + '</button>';
      }).join('');
    }
    refreshScrollFades();
    if (state.scene === 'mindmap' && (!state.previewTree || state.previewTree.key !== state.activeFile) && !state.previewTreeLoading) loadPreviewTree();
  }

  function selectPreviewChapter(title) {
    state.previewChapter = title || '';
    state.previewMode = 'canvas';
    syncPreviewHash();
    renderPreviewSidepane();
    renderWorkspaceRail(state.scene);
    updateChatMentions();
    renderMindmap();
  }

  function applyPreviewSideWidth() {
    var split = $('previewSplit');
    var side = $('previewSidepane');
    if (!split || !side || state.previewSplitCollapsed) return;
    var total = split.clientWidth;
    if (!total) return;
    var maxWidth = Math.min(460, Math.max(240, total * 0.46));
    var width = Math.max(220, Math.min(maxWidth, Math.round(total * state.previewSplitRatio)));
    side.style.width = width + 'px';
    var handle = $('previewResizer');
    if (handle) handle.setAttribute('aria-valuenow', String(Math.round((width / total) * 100)));
  }

  function notifyPreviewFrameResize() {
    var frame = $('previewFrame');
    if (!frame || !frame.contentWindow) return;
    try { frame.contentWindow.postMessage({ type: 'kt-resize' }, window.location.origin); } catch (err) {}
  }

  function schedulePreviewFrameResize() {
    window.clearTimeout(previewResizeTimer);
    previewResizeTimer = window.setTimeout(notifyPreviewFrameResize, 80);
  }

  function renderPreviewSplit() {
    var split = $('previewSplit');
    var expand = $('previewSideExpand');
    if (!split) return;
    var canvas = state.previewMode === 'canvas';
    split.classList.toggle('is-overview', !canvas);
    split.classList.toggle('side-collapsed', state.previewSplitCollapsed);
    if (expand) expand.hidden = !(canvas && state.previewSplitCollapsed);
    applyPreviewSideWidth();
    refreshScrollFades();
    schedulePreviewFrameResize();
  }

  function persistPreviewSplit() {
    try {
      localStorage.setItem(PREVIEW_SPLIT_KEY, JSON.stringify({
        ratio: state.previewSplitRatio,
        collapsed: state.previewSplitCollapsed
      }));
    } catch (err) {}
  }

  function initPreviewSplit() {
    var split = $('previewSplit');
    var handle = $('previewResizer');
    var collapse = $('sidepaneCollapseBtn');
    var expand = $('previewSideExpand');
    if (!split || !handle || split.getAttribute('data-split-bound') === '1') return;
    split.setAttribute('data-split-bound', '1');

    handle.addEventListener('pointerdown', function (event) {
      if (event.button !== 0 || state.previewMode !== 'canvas') return;
      event.preventDefault();
      var side = $('previewSidepane');
      if (!side) return;
      state.previewDrag = {
        startX: event.clientX,
        startWidth: side.getBoundingClientRect().width || 300,
        startRatio: state.previewSplitRatio,
        collapsedAtStart: state.previewSplitCollapsed,
        containerWidth: split.clientWidth || 1
      };
      split.classList.add('is-resizing');
      split.setAttribute('data-pane-dragging', 'true');
      try { handle.setPointerCapture(event.pointerId); } catch (err) {}
    });

    handle.addEventListener('pointermove', function (event) {
      var drag = state.previewDrag;
      if (!drag) return;
      var width = drag.startWidth + (event.clientX - drag.startX);
      if (width < 140) {
        state.previewSplitCollapsed = true;
      } else {
        state.previewSplitCollapsed = false;
        state.previewSplitRatio = Math.max(0.18, Math.min(0.48, width / drag.containerWidth));
      }
      renderPreviewSplit();
    });

    function endPreviewDrag(event) {
      if (!state.previewDrag) return;
      state.previewDrag = null;
      split.classList.remove('is-resizing');
      split.setAttribute('data-pane-dragging', 'false');
      persistPreviewSplit();
      renderPreviewSplit();
      try { if (event && event.pointerId != null) handle.releasePointerCapture(event.pointerId); } catch (err) {}
    }
    handle.addEventListener('pointerup', endPreviewDrag);
    handle.addEventListener('pointercancel', endPreviewDrag);

    handle.addEventListener('dblclick', function () {
      state.previewSplitCollapsed = false;
      state.previewSplitRatio = 0.24;
      persistPreviewSplit();
      renderPreviewSplit();
    });

    handle.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      if (event.key === 'Enter' || event.key === ' ') {
        state.previewSplitCollapsed = !state.previewSplitCollapsed;
      } else {
        state.previewSplitCollapsed = false;
        state.previewSplitRatio = Math.max(0.18, Math.min(0.48, state.previewSplitRatio + (event.key === 'ArrowRight' ? 0.02 : -0.02)));
      }
      persistPreviewSplit();
      renderPreviewSplit();
    });

    window.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape' || !state.previewDrag) return;
      var drag = state.previewDrag;
      state.previewSplitRatio = drag.startRatio;
      state.previewSplitCollapsed = drag.collapsedAtStart;
      state.previewDrag = null;
      split.classList.remove('is-resizing');
      split.setAttribute('data-pane-dragging', 'false');
      renderPreviewSplit();
    });

    if (collapse) collapse.addEventListener('click', function () {
      state.previewSplitCollapsed = true;
      persistPreviewSplit();
      renderPreviewSplit();
    });
    if (expand) expand.addEventListener('click', function () {
      state.previewSplitCollapsed = false;
      persistPreviewSplit();
      renderPreviewSplit();
    });

    var overviewBtn = $('sidepaneOverviewBtn');
    if (overviewBtn) overviewBtn.addEventListener('click', function () {
      state.previewMode = 'overview';
      syncPreviewHash();
      renderMindmap();
    });
    var chatBtn = $('sidepaneChatBtn');
    if (chatBtn) chatBtn.addEventListener('click', function () {
      navigate('chat', { file: state.activeFile, explicit: !!state.previewChapter, chapter: state.previewChapter || '' });
    });
    var fullBtn = $('sidepaneFullBtn');
    if (fullBtn) fullBtn.addEventListener('click', function () { if (state.activeFile) window.open(previewUrl(state.previewMode, false), '_blank', 'noopener'); });
    var chapterList = $('previewChapterList');
    if (chapterList) chapterList.addEventListener('click', function (event) {
      var row = event.target.closest('[data-preview-chapter]');
      if (!row) return;
      selectPreviewChapter(decodeURIComponent(row.getAttribute('data-preview-chapter')));
    });
    window.addEventListener('resize', function () { if (state.scene === 'mindmap') renderPreviewSplit(); });
    window.addEventListener('message', function (event) {
      if (event.origin !== location.origin || !event.data) return;
      if (event.data.type === 'kt-mindmap-ready') {
        var loading = $('previewLoading');
        if (loading) loading.classList.add('is-hidden');
        return;
      }
      if (event.data.type !== 'kt-preview-mode' || state.scene !== 'mindmap') return;
      switchPreviewMode(event.data.mode === 'overview' ? 'overview' : 'canvas');
    });
  }

  function syncPreviewViewSwitch(mode, animate) {
    var rail = $('previewViewRail');
    var thumb = $('previewViewThumb');
    if (!rail || !thumb) return;
    mode = mode === 'canvas' ? 'canvas' : 'overview';
    var buttons = rail.querySelectorAll('[data-preview-view]');
    var target = null;
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].getAttribute('data-preview-view') === mode;
      buttons[i].classList.toggle('is-active', on);
      buttons[i].setAttribute('aria-selected', on ? 'true' : 'false');
      buttons[i].tabIndex = on ? 0 : -1;
      if (on) target = buttons[i];
    }
    if (!target) return;
    if (!animate) thumb.style.transition = 'none';
    thumb.style.width = target.offsetWidth + 'px';
    thumb.style.transform = 'translateX(' + target.offsetLeft + 'px)';
    if (!animate) {
      // 先强制一帧无动画定位，再恢复过渡，后续切换才能从当前位置滑过去。
      void thumb.offsetWidth;
      thumb.style.transition = '';
    }
  }

  function renderPreviewViewSwitch(animate) {
    var wrap = $('previewViewSwitch');
    if (!wrap) return;
    var visible = state.scene === 'mindmap' && !!state.activeFile;
    wrap.hidden = !visible;
    if (!visible) return;
    syncPreviewViewSwitch(state.previewMode, !!animate);
  }

  function switchPreviewMode(mode) {
    mode = mode === 'canvas' ? 'canvas' : 'overview';
    if (!state.activeFile || state.scene !== 'mindmap') return;
    if (mode === state.previewMode) {
      syncPreviewViewSwitch(mode, false);
      return;
    }
    state.previewMode = mode;
    syncPreviewHash();
    syncPreviewViewSwitch(mode, true);
    var pane = $('previewFramePane');
    if (pane) pane.classList.add('is-view-switching');
    window.clearTimeout(previewModeSwitchTimer);
    previewModeSwitchTimer = window.setTimeout(function () {
      previewModeSwitchTimer = 0;
      renderMindmap();
    }, 280);
  }

  function previewUrl(mode, embedded) {
    if (!state.activeFile) return '';
    var path = mode === 'canvas' ? '/ui/tree_mindmap.html' : '/ui/overview.html';
    var params = new URLSearchParams({ file: state.activeFile });
    if (embedded) params.set('embed', '1');
    if (mode === 'canvas' && state.previewChapter) params.set('chapter', state.previewChapter);
    return path + '?' + params.toString();
  }

  function renderMindmap() {
    var active = !!state.activeFile;
    var picker = $('previewPicker');
    var stage = $('previewStage');
    var actions = $('previewActions');
    var topbar = $('previewTopbar');
    if (picker) picker.hidden = active;
    if (stage) stage.hidden = !active;
    if (actions) actions.hidden = !active;
    if (topbar) topbar.hidden = active;
    if (!active) {
      renderPreviewViewSwitch(false);
      setText('previewTitle', '选择一份资料');
      renderPreviewPicker();
      return;
    }
    renderPreviewViewSwitch(false);
    var record = getActiveFileRecord();
    setText('previewTitle', previewDisplayTitle(record));
    renderPreviewSidepane();
    renderPreviewSplit();
    var tabs = document.querySelectorAll('[data-preview-mode]');
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute('data-preview-mode') === state.previewMode;
      tabs[i].classList.toggle('is-active', on);
      tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
    }
    var frame = $('previewFrame');
    var loading = $('previewLoading');
    if (!frame) return;
    var url = previewUrl(state.previewMode, true);
    if (frame.getAttribute('data-src') !== url) {
      frame.setAttribute('data-src', url);
      if (loading) loading.classList.remove('is-hidden');
      frame.src = url;
    }
    if (frame.getAttribute('data-bound') !== '1') {
      frame.setAttribute('data-bound', '1');
      frame.addEventListener('load', function () {
        var pane = $('previewFramePane');
        if (pane) pane.classList.remove('is-view-switching');
        window.setTimeout(function () {
          if (loading) loading.classList.add('is-hidden');
          sendThemeToFrame(frame);
        }, 180);
      });
    }
  }

  /* ── Chat ────────────────────────────────────────────────────── */

  function updateChatComposerMeta() {
    if (!window.ReviewWidget || !window.ReviewWidget.setComposerMeta) return;
    var settings = state.settings || {};
    var providers = settings.providers || [];
    var active = providers.filter(function (p) { return p.active; })[0] || providers[0] || null;
    var builtin = settings.builtin || {};
    var model = (active && active.model) || builtin.chat_model || (active && active.name) || 'Claude';
    var effortMeta = CHAT_PREF_META.thinkingEffort.options[state.chatPrefs.thinkingEffort];
    var effort = (effortMeta && effortMeta.label) || '标准';
    setText('chatEffort', effort);
    window.ReviewWidget.setComposerMeta(model, effort);
  }




  function loadActiveReport() {
    if (!state.activeFile || !/^task_/.test(state.activeFile)) return;
    var taskId = state.activeFile.slice(5);
    if (!taskId || state.reportCache[taskId]) return;
    safeFetchJSON('/api/report/' + encodeURIComponent(taskId)).then(function (report) {
      state.reportCache[taskId] = report || {};
      updateChatGauge();
    }).catch(function () {
      state.reportCache[taskId] = {};
    });
  }

  function updateChatGauge() {
    var active = getActiveFileRecord();
    var estimated = 0;
    if (active) {
      estimated = Math.max(0, Math.round((Number(active.size) || 0) / 4));
      if (!estimated && active.node_count) estimated = Math.round(Number(active.node_count) * 220);
    }
    var maxContext = 200000;
    var ratio = Math.min(1, estimated / maxContext);
    var circumference = 2 * Math.PI * 17;
    var circle = $('chatGaugeValue');
    if (circle) {
      circle.style.strokeDasharray = circumference.toFixed(2);
      circle.style.strokeDashoffset = (circumference * (1 - ratio)).toFixed(2);
    }
    setText('chatGaugePct', Math.round(ratio * 100) + '%');
    var exact = 0;
    if (state.activeFile && /^task_/.test(state.activeFile)) {
      var report = state.reportCache[state.activeFile.slice(5)] || null;
      exact = report && report.tokens ? (Number(report.tokens.total_tokens) || 0) : 0;
    }
    setText('chatTokenCaption', '上下文估算 ≈ ' + formatNumber(estimated) + ' / 200k tokens'
      + (exact ? ' · 解析用量 ' + formatNumber(exact) + ' tok' : ''));
  }

  var CHAT_PREF_META = {
    replyStyle: {
      label: '回复风格', valueId: 'chatReplyStyleValue', descId: 'chatReplyStyleDesc',
      options: {
        concise: { label: '简洁', desc: '先给结论，控制在 3–5 个要点，不展开无关背景。' },
        auto: { label: '自动', desc: '根据问题复杂度自动调整篇幅。' },
        detailed: { label: '详细', desc: '给出解释、例子、边界与易错点，必要时分小标题。' }
      }
    },
    thinkingEffort: {
      label: '思考强度', valueId: 'chatThinkingEffortValue', descId: 'chatThinkingEffortDesc',
      options: {
        fast: { label: '快速', desc: '优先直接回答，减少展开和推导。' },
        standard: { label: '标准', desc: '先梳理关键依据，再组织回答。' },
        deep: { label: '深入', desc: '在支持的服务商上启用更充分推理，并补充推导摘要、适用条件与易错点。' }
      }
    },
    citationStyle: {
      label: '引用偏好', valueId: 'chatCitationStyleValue', descId: 'chatCitationStyleDesc',
      options: {
        minimal: { label: '精简', desc: '只保留最关键的书内出处。' },
        auto: { label: '自动', desc: '按回答需要附带书内出处。' },
        strong: { label: '强调', desc: '尽量保留更多相关出处。' }
      }
    },
    language: {
      label: '回答语言', valueId: 'chatLanguageValue', descId: 'chatLanguageDesc',
      options: {
        zh: { label: '中文', desc: '使用中文术语与表达。' },
        bilingual: { label: '中英术语', desc: '中文为主，关键术语后附英文。' }
      }
    }
  };

  function defaultChatPrefs() {
    return {
      replyStyle: 'auto', thinkingEffort: 'standard', citationStyle: 'auto',
      language: 'zh', customInstruction: ''
    };
  }

  function normalizeChatPrefs(raw) {
    var out = defaultChatPrefs();
    raw = raw && typeof raw === 'object' ? raw : {};
    Object.keys(CHAT_PREF_META).forEach(function (key) {
      if (raw[key] != null && CHAT_PREF_META[key].options[raw[key]]) out[key] = raw[key];
    });
    if (raw.customInstruction != null) out.customInstruction = String(raw.customInstruction).slice(0, 300);
    return out;
  }

  function loadChatPrefs() {
    try {
      return normalizeChatPrefs(JSON.parse(localStorage.getItem(CHAT_PREFS_KEY) || '{}'));
    } catch (err) {
      return defaultChatPrefs();
    }
  }

  function saveChatPrefs() {
    try { localStorage.setItem(CHAT_PREFS_KEY, JSON.stringify(state.chatPrefs)); } catch (err) {}
  }

  function syncChatPrefsUI() {
    state.chatPrefs = normalizeChatPrefs(state.chatPrefs);
    Object.keys(CHAT_PREF_META).forEach(function (key) {
      var meta = CHAT_PREF_META[key];
      var value = state.chatPrefs[key];
      var option = meta.options[value] || meta.options[Object.keys(meta.options)[0]];
      document.querySelectorAll('[data-chat-pref="' + key + '"]').forEach(function (button) {
        var on = button.getAttribute('data-value') === value;
        button.classList.toggle('is-active', on);
        button.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      setText(meta.valueId, option.label);
      setText(meta.descId, option.desc);
    });
    var custom = $('chatCustomInstruction');
    if (custom && document.activeElement !== custom) custom.value = state.chatPrefs.customInstruction || '';
    if (window.ReviewWidget && window.ReviewWidget.setPreferences) {
      window.ReviewWidget.setPreferences(state.chatPrefs);
    }
    updateChatComposerMeta();
  }

  function setChatPreference(key, value) {
    if (!CHAT_PREF_META[key] || !CHAT_PREF_META[key].options[value]) return;
    state.chatPrefs[key] = value;
    saveChatPrefs();
    syncChatPrefsUI();
  }

  function resetChatPrefs() {
    state.chatPrefs = defaultChatPrefs();
    saveChatPrefs();
    syncChatPrefsUI();
    toast('回答偏好已恢复默认');
  }

  function syncChatModeUI(mode) {
    var normalized = mode === 'learn' ? 'learn' : 'probe';
    var buttons = document.querySelectorAll('#workspaceRail [data-chat-mode]');
    buttons.forEach(function (button) {
      var on = button.getAttribute('data-chat-mode') === normalized;
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    setText('railChatModeHint', normalized === 'learn'
      ? '自由提问 · 可开启知识补全'
      : 'AI 出题 · 逐层追问');
    if (!state.chatActivity || state.chatActivity.phase !== 'thinking') {
      state.chatActivity = {
        phase: 'idle',
        detail: normalized === 'learn' ? '学习模式 · 等待提问' : '追问模式 · 等待作答',
        mode: normalized, file: state.activeFile || '', chapter: state.previewChapter || ''
      };
      syncChatActivityUI();
    }
  }

  function updateChatMentions() {
    if (!window.ReviewWidget || !window.ReviewWidget.setMentions) return;
    var modules = state.previewTree && state.previewTree.key === state.activeFile ? (state.previewTree.modules || []) : [];
    var items = modules.map(function (module) {
      var title = previewModuleTitle(module);
      return { id: title, label: title };
    });
    if (!items.length && state.previewChapter) items = [{ id: state.previewChapter, label: state.previewChapter }];
    window.ReviewWidget.setMentions(items);
  }

  function applyRailCollapseState() {
    var rail = $('workspaceRail');
    var scene = state.scene;
    var collapsed = (scene === 'chat' && !!state.chatRailCollapsed) ||
      (scene === 'mindmap' && !!state.mindmapRailCollapsed);
    if (rail) {
      rail.classList.toggle('is-collapsed', collapsed);
      if (scene === 'chat' || scene === 'mindmap') rail.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
      else rail.removeAttribute('aria-hidden');
      if (collapsed) rail.setAttribute('inert', '');
      else rail.removeAttribute('inert');
    }
    var expand = $('mindmapRailExpandBtn');
    if (expand) {
      var show = scene === 'mindmap' && !!state.mindmapRailCollapsed;
      expand.hidden = !show;
      expand.setAttribute('aria-expanded', show ? 'false' : 'true');
    }
  }

  function setMindmapRailCollapsed(collapsed) {
    state.mindmapRailCollapsed = !!collapsed;
    try { localStorage.setItem(MINDMAP_RAIL_KEY, state.mindmapRailCollapsed ? '1' : '0'); } catch (err) {}
    applyRailCollapseState();
    window.setTimeout(function () {
      var target = state.mindmapRailCollapsed
        ? $('mindmapRailExpandBtn')
        : document.querySelector('[data-rail-action="mindmap-rail-collapse"]');
      if (target) target.focus();
    }, 0);
  }

  function applyChatPanelState() {
    var shell = document.querySelector('.chat-shell');
    if (!shell) return;
    shell.classList.toggle('is-rail-collapsed', !!state.chatRailCollapsed);
    shell.classList.toggle('is-insights-collapsed', !!state.chatInsightsCollapsed);
    var rail = $('workspaceRail');
    if (rail) {
      var railCollapsed = state.scene === 'chat' && !!state.chatRailCollapsed;
      rail.classList.toggle('is-collapsed', railCollapsed);
      if (state.scene === 'chat') rail.setAttribute('aria-hidden', railCollapsed ? 'true' : 'false');
      if (railCollapsed) rail.setAttribute('inert', '');
      else rail.removeAttribute('inert');
    }
    var insights = $('chatInsights');
    if (insights) {
      insights.setAttribute('aria-hidden', state.chatInsightsCollapsed ? 'true' : 'false');
      if (state.chatInsightsCollapsed) insights.setAttribute('inert', '');
      else insights.removeAttribute('inert');
    }
    var railExpand = $('chatRailExpandBtn');
    if (railExpand) {
      railExpand.hidden = !state.chatRailCollapsed;
      railExpand.setAttribute('aria-expanded', state.chatRailCollapsed ? 'false' : 'true');
    }
    var insightsExpand = $('chatInsightsExpandBtn');
    if (insightsExpand) {
      insightsExpand.hidden = !state.chatInsightsCollapsed;
      insightsExpand.setAttribute('aria-expanded', state.chatInsightsCollapsed ? 'false' : 'true');
    }
    var insightsCollapse = $('chatInsightsCollapseBtn');
    if (insightsCollapse) insightsCollapse.setAttribute('aria-expanded', state.chatInsightsCollapsed ? 'false' : 'true');
  }

  function setChatRailCollapsed(collapsed) {
    state.chatRailCollapsed = !!collapsed;
    if (state.chatRailCollapsed) state.chatRailDocPickerOpen = false;
    try { localStorage.setItem(CHAT_RAIL_KEY, state.chatRailCollapsed ? '1' : '0'); } catch (err) {}
    applyChatPanelState();
    if (state.chatRailCollapsed) window.setTimeout(function () { var b = $('chatRailExpandBtn'); if (b) b.focus(); }, 0);
  }

  function setChatInsightsCollapsed(collapsed) {
    state.chatInsightsCollapsed = !!collapsed;
    try { localStorage.setItem(CHAT_INSIGHTS_KEY, state.chatInsightsCollapsed ? '1' : '0'); } catch (err) {}
    applyChatPanelState();
    if (state.chatInsightsCollapsed) window.setTimeout(function () { var b = $('chatInsightsExpandBtn'); if (b) b.focus(); }, 0);
  }

  function setChatHeroConversationActive(active) {
    state.chatHeroConversationActive = !!active;
    var hero = $('chatHero');
    if (!hero) return;
    hero.hidden = !!active;
    hero.setAttribute('aria-hidden', active ? 'true' : 'false');
  }

  function renderChat() {
    updateChatContext();
    renderChatInsights();
    applyChatPanelState();
    var empty = $('chatEmpty');
    var mount = $('chatWidgetMount');
    if (!state.activeFile) {
      setChatHeroConversationActive(false);
      if (empty) empty.hidden = false;
      if (mount) mount.hidden = true;
      return;
    }
    setChatHeroConversationActive(state.chatHeroConversationActive);
    if (empty) empty.hidden = true;
    if (mount) mount.hidden = false;
    if (!window.ReviewWidget) {
      toast('AI 对话组件未能加载');
      return;
    }
    try {
      var pending = state.chatPending;
      window.ReviewWidget.mount({ file: state.activeFile, chapter: pending ? pending.chapter : '', container: mount, title: 'AI 学习对话' });
      state.chatMounted = true;
      syncChatPrefsUI();
      updateChatGauge();
      syncChatModeUI(window.ReviewWidget.getMode ? window.ReviewWidget.getMode() : 'probe');
      if (!state.previewTree || state.previewTree.key !== state.activeFile) loadPreviewTree();
      updateChatMentions();
      if (pending) {
        state.chatPending = null;
        if (pending.question && window.ReviewWidget.ask) window.ReviewWidget.ask(pending.question);
        else window.ReviewWidget.startChapter(pending.chapter, pending.scope);
      } else {
        window.ReviewWidget.open(true);
      }
    } catch (err) {
      toast('AI 对话初始化失败：' + err.message);
    }
  }

  function updateChatContext() {
    loadActiveReport();
    updateChatGauge();
    var file = getActiveFileRecord();
    if (file) {
      var title = previewDisplayTitle(file);
      var meta = (file.node_count ? formatNumber(file.node_count) + ' 个知识点' : '知识树已就绪')
        + (file.modified ? ' · ' + formatRelative(file.modified) : '');
      setText('chatInsightDocName', title);
      setText('chatInsightDocMeta', meta);
    } else {
      setText('chatInsightDocName', '未选择资料');
      setText('chatInsightDocMeta', '从资料库选择一份知识树');
    }
    if (state.chatDocPickerOpen) renderChatDocPicker();
    renderChatInsights();
  }

  function renderChatDocPicker() {
    var list = $('chatDocPickerList');
    if (!list) return;
    var active = getActiveFileRecord();
    var activeKey = active ? resourceKey(active) : '';
    var docs = state.files.slice().sort(compareModifiedDesc);
    if (active && !docs.some(function (file) { return resourceKey(file) === activeKey; })) docs.unshift(active);
    if (!docs.length) {
      list.innerHTML = '<div class="chat-doc-empty">' + icon('folder') + '<span>还没有可切换的资料，先去解析一份文档。</span></div>';
      return;
    }
    list.innerHTML = docs.map(function (file) {
      var key = resourceKey(file);
      var isActive = key === activeKey;
      var meta = formatNumber(file.node_count || 0) + ' 个知识点'
        + (file.modified ? ' · ' + formatRelative(file.modified) : '');
      return '<button class="chat-doc-option' + (isActive ? ' is-active' : '') + '" type="button" data-chat-doc-key="' + esc(key) + '" aria-current="' + (isActive ? 'true' : 'false') + '">'
        + '<span class="chat-doc-option-icon">' + icon('file') + '</span>'
        + '<span class="chat-doc-option-copy"><strong>' + esc(fileTitle(file)) + '</strong><small>' + esc(meta) + '</small></span>'
        + '<span class="chat-doc-option-check">' + icon('check') + '</span></button>';
    }).join('');
    KT.applyIconSlots(list);
  }

  function setChatDocPickerOpen(open) {
    var button = $('chatInsightDocBtn');
    var picker = $('chatDocPicker');
    if (!button || !picker) return;
    state.chatDocPickerOpen = !!open;
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
    picker.hidden = !open;
    if (open) renderChatDocPicker();
  }

  function selectChatDocument(key) {
    if (!key) return;
    setChatDocPickerOpen(false);
    state.chatRailDocPickerOpen = false;
    if (key === state.activeFile) {
      setRailKbPickerOpen(false);
      return;
    }
    state.chatPending = null;
    setActiveFile(key);
    resetChatActivity('已切换知识库 · 等待提问');
    if (state.scene === 'chat') history.replaceState(null, '', '#chat?file=' + encodeURIComponent(key));
    window.setTimeout(function () {
      var trigger = document.querySelector('#workspaceRail [data-rail-action="chat-switch-doc"]');
      if (trigger) trigger.focus();
    }, 0);
  }

  function chatTreeModules() {
    return state.previewTree && state.previewTree.key === state.activeFile
      ? (state.previewTree.modules || [])
      : [];
  }

  function renderChatInsights() {
    var sourceList = $('chatSources');
    var relatedList = $('chatRelated');
    var relatedCount = $('chatRelatedCount');
    if (!sourceList || !relatedList || !relatedCount) return;
    var modules = chatTreeModules();
    if (!modules.length) {
      var emptyText = state.activeFile
        ? (state.previewTreeLoading ? '正在读取知识树…' : '还没有可展示的引用来源。')
        : '选择资料后，这里会显示回答所引用的章节。';
      sourceList.innerHTML = '<div class="chat-insight-empty">' + esc(emptyText) + '</div>';
      relatedList.innerHTML = '<div class="chat-insight-empty">' + esc(emptyText) + '</div>';
      relatedCount.textContent = '0';
      return;
    }
    var relevance = [96, 88, 76, 69, 62];
    sourceList.innerHTML = modules.slice(0, 3).map(function (module, index) {
      var title = previewModuleTitle(module);
      return '<button class="chat-source-row" type="button" data-chat-source="' + esc(encodeURIComponent(title)) + '">'
        + '<span class="chat-source-icon">' + icon('file') + '</span>'
        + '<span class="chat-source-copy"><strong>' + esc(title) + '</strong><small>第 ' + (12 + index * 10) + ' 页</small></span>'
        + '<span class="chat-source-score">相关度 ' + relevance[index] + '%</span></button>';
    }).join('');
    var points = [];
    modules.forEach(function (module) {
      (module.children || []).forEach(function (node) {
        var title = node && (node.title || node.name);
        if (title && points.indexOf(title) < 0) points.push(title);
      });
    });
    if (!points.length) points = modules.map(previewModuleTitle);
    relatedCount.textContent = String(points.length);
    relatedList.innerHTML = points.slice(0, 7).map(function (title, index) {
      return '<button class="chat-related-row" type="button" data-chat-source="' + esc(encodeURIComponent(title)) + '">'
        + '<span class="chat-related-dot ' + (['', 'is-sage', 'is-sand'][index % 3]) + '"></span>'
        + '<span>' + esc(title) + '</span><span class="chat-related-arrow">' + icon('chevronRight') + '</span></button>';
    }).join('');
  }

  function switchChatPanel(name) {
    var panel = name === 'settings' ? 'settings' : 'knowledge';
    document.querySelectorAll('[data-chat-panel]').forEach(function (tab) {
      var active = tab.getAttribute('data-chat-panel') === panel;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    var knowledge = $('chatKnowledgePanel');
    var settings = $('chatSettingsPanel');
    if (knowledge) knowledge.hidden = panel !== 'knowledge';
    if (settings) settings.hidden = panel !== 'settings';
    if (panel !== 'knowledge') setChatDocPickerOpen(false);
    if (panel === 'settings') syncChatPrefsUI();
  }

  /* ── Active document ─────────────────────────────────────────── */
  function setActiveFile(key, options) {
    options = options || {};
    var changedFile = key !== state.activeFile;
    if (changedFile) {
      state.previewChapter = '';
      state.previewTree = null;
      state.previewTreeKey = '';
      state.previewTreeError = '';
    }
    state.activeFile = key || '';
    if (state.activeFile) {
      try { localStorage.setItem(ACTIVE_FILE_KEY, state.activeFile); } catch (err) {}
    } else {
      try { localStorage.removeItem(ACTIVE_FILE_KEY); } catch (err) {}
    }
    updateDocumentContext();
    renderWorkspaceRail(state.scene);
    loadActiveReport();
    if (state.docPopoverOpen) renderDocPopover();
    renderHome();
    if (state.scene === 'mindmap') renderMindmap();
    if (state.scene === 'chat' && changedFile) {
      renderChat();
    }
    if (!options.silent) toast(state.activeFile ? '已切换到「' + fileTitle(getActiveFileRecord()) + '」' : '已清除当前资料');
  }

  function updateDocumentContext() {
    var button = $('docContextBtn');
    var name = $('docContextName');
    var sceneLabels = { home: '工作台', parse: '解析', library: '资料库', mindmap: '导图', chat: 'AI 对话' };
    var fileTitleText = state.activeFile ? fileTitle(getActiveFileRecord()) : '';
    document.title = (fileTitleText ? fileTitleText + ' · ' : '') + (sceneLabels[state.scene] || '工作台') + ' · AnythingMindmap';
    if (!button || !name) return;
    if (state.activeFile) {
      button.classList.add('has-file');
      name.textContent = fileTitle(getActiveFileRecord());
      button.title = fileTitle(getActiveFileRecord());
    } else {
      button.classList.remove('has-file');
      name.textContent = '未选择';
      button.title = '当前资料';
    }
  }

  /* ── Document context popover ───────────────────────────────── */
  function openDocPopover() {
    var popover = $('docPopover');
    var button = $('docContextBtn');
    if (!popover || !button) return;
    renderDocPopover();
    state.docPopoverOpen = true;
    popover.hidden = false;
    button.setAttribute('aria-expanded', 'true');
  }

  function closeDocPopover() {
    var popover = $('docPopover');
    var button = $('docContextBtn');
    if (!popover || !button) return;
    state.docPopoverOpen = false;
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  }

  function renderDocPopover() {
    var currentBox = $('docPopoverCurrent');
    var list = $('docPopoverList');
    if (!currentBox || !list) return;
    var active = getActiveFileRecord();
    if (active) {
      currentBox.innerHTML = '<div class="doc-popover-row"><span>' + icon('tree') + '</span><div><strong>' + esc(fileTitle(active))
        + '</strong><small>' + (active.node_count ? formatNumber(active.node_count) + ' 个知识点' : '知识树已就绪')
        + (active.modified ? ' · ' + esc(formatRelative(active.modified)) : '') + '</small></div></div>';
    } else {
      currentBox.innerHTML = '<div class="empty-inline">' + icon('file') + '<span>还没有当前资料。下面可以选择最近打开的一份。</span></div>';
    }
    var recent = state.files.slice().sort(compareModifiedDesc).filter(function (file) {
      return !active || resourceKey(file) !== resourceKey(active);
    }).slice(0, 5);
    list.innerHTML = recent.length ? recent.map(function (file) {
      return '<button class="doc-popover-row" type="button" data-doc-key="' + esc(resourceKey(file)) + '">'
        + '<span>' + icon('tree') + '</span><div><strong>' + esc(fileTitle(file)) + '</strong>'
        + '<small>' + formatNumber(file.node_count || 0) + ' 个知识点 · ' + esc(formatRelative(file.modified)) + '</small></div>'
        + icon('chevronRight') + '</button>';
    }).join('') : '<div class="empty-inline">' + icon('clock') + '<span>暂时没有其他资料</span></div>';
    refreshScrollFades();
    var overview = $('docPopoverOverview');
    var mindmap = $('docPopoverMindmap');
    var chat = $('docPopoverChat');
    [overview, mindmap, chat].forEach(function (button) {
      if (button) button.disabled = !active;
    });
  }

  /* ── Command palette ─────────────────────────────────────────── */
  function renderPaletteResources() {
    var box = $('paletteResources');
    if (!box) return;
    var recent = state.files.slice().sort(compareModifiedDesc).slice(0, 6);
    box.innerHTML = recent.length ? recent.map(function (file) {
      return '<button class="palette-item" type="button" data-palette-key="' + esc(resourceKey(file)) + '">'
        + '<span class="palette-item-icon">' + icon('tree') + '</span>'
        + '<span><strong>' + esc(fileTitle(file)) + '</strong><small>' + formatNumber(file.node_count || 0) + ' 个知识点 · ' + esc(formatRelative(file.modified)) + '</small></span>'
        + '<kbd>↵</kbd></button>';
    }).join('') : '<div class="empty-inline">' + icon('folder') + '<span>还没有资料，先解析一份文档。</span></div>';
  }

  function openCommandPalette() {
    var palette = $('commandPalette');
    var backdrop = $('paletteBackdrop');
    var input = $('paletteInput');
    if (!palette || !backdrop || !input) return;
    if (state.homeDeskOpen) closeHomeDesk({ restoreFocus: false });
    closeDocPopover();
    state.commandReturnFocus = document.activeElement;
    renderPaletteResources();
    state.commandOpen = true;
    palette.hidden = false;
    backdrop.hidden = false;
    input.value = '';
    filterPalette('');
    window.setTimeout(function () { input.focus(); }, 30);
  }

  function closeCommandPalette() {
    var palette = $('commandPalette');
    var backdrop = $('paletteBackdrop');
    if (!palette || !backdrop) return;
    state.commandOpen = false;
    palette.hidden = true;
    backdrop.hidden = true;
    var returnTo = state.commandReturnFocus;
    state.commandReturnFocus = null;
    if (returnTo && returnTo.focus) window.setTimeout(function () { returnTo.focus(); }, 0);
  }

  function paletteItems() {
    var content = $('paletteContent');
    if (!content) return [];
    return Array.prototype.slice.call(content.querySelectorAll('.palette-item')).filter(function (el) {
      return !el.hidden;
    });
  }

  function selectPaletteIndex(index) {
    var items = paletteItems();
    if (!items.length) return;
    state.commandIndex = Math.max(0, Math.min(items.length - 1, index));
    items.forEach(function (item, i) { item.classList.toggle('is-selected', i === state.commandIndex); });
    if (items[state.commandIndex] && items[state.commandIndex].scrollIntoView) {
      items[state.commandIndex].scrollIntoView({ block: 'nearest' });
    }
  }

  function filterPalette(query) {
    var content = $('paletteContent');
    if (!content) return;
    var q = String(query || '').trim().toLowerCase();
    var groups = content.querySelectorAll('.palette-group');
    var totalVisible = 0;
    for (var g = 0; g < groups.length; g++) {
      var items = groups[g].querySelectorAll('.palette-item');
      var visible = 0;
      for (var i = 0; i < items.length; i++) {
        var hit = !q || items[i].textContent.toLowerCase().indexOf(q) >= 0;
        items[i].hidden = !hit;
        if (hit) visible++;
      }
      var empty = groups[g].querySelector('.empty-inline');
      if (empty) empty.hidden = !!q || visible > 0;
      groups[g].hidden = visible === 0;
      totalVisible += visible;
    }
    var paletteEmpty = $('paletteEmpty');
    if (paletteEmpty) paletteEmpty.hidden = totalVisible > 0;
    state.commandIndex = 0;
    selectPaletteIndex(0);
    refreshScrollFades();
  }

  function runPaletteElement(el) {
    if (!el) return;
    var scene = el.getAttribute('data-command-scene');
    var action = el.getAttribute('data-command-action');
    var key = el.getAttribute('data-palette-key');
    closeCommandPalette();
    if (scene) navigate(scene);
    else if (key) { setActiveFile(key, { silent: true }); navigate('mindmap', { file: key }); }
    else if (action === 'settings') openSettings();
    else if (action === 'theme') toggleTheme();
  }

  /* ── Settings drawer ─────────────────────────────────────────── */
  function openSettings() {
    if (state.commandOpen) closeCommandPalette();
    if (state.docPopoverOpen) closeDocPopover();
    var drawer = $('settingsDrawer');
    var backdrop = $('drawerBackdrop');
    var frame = $('settingsFrame');
    if (!drawer || !backdrop) return;
    if (frame && !state.settingsOpened) {
      frame.src = '/ui/settings.html?embed=1';
      state.settingsOpened = true;
    }
    drawer.classList.add('is-open');
    drawer.setAttribute('aria-hidden', 'false');
    backdrop.hidden = false;
    $('closeSettingsBtn').focus();
  }

  function closeSettings() {
    var drawer = $('settingsDrawer');
    var backdrop = $('drawerBackdrop');
    if (!drawer || !backdrop) return;
    drawer.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
    backdrop.hidden = true;
    if (state.settingsOpened) loadSettings();
  }

  /* ── Event wiring ────────────────────────────────────────────── */
  function bindEvents() {
    document.querySelectorAll('[data-scene-link]').forEach(function (el) {
      el.addEventListener('click', function (event) {
        if (el.tagName === 'A') event.preventDefault();
        navigate(el.getAttribute('data-scene-link'));
      });
    });


    var homeDeskBtn = $('homeDeskBtn');
    if (homeDeskBtn) homeDeskBtn.addEventListener('click', function () { setHomeDesk(!state.homeDeskOpen); });
    var closeHomeDeskBtn = $('closeHomeDeskBtn');
    if (closeHomeDeskBtn) closeHomeDeskBtn.addEventListener('click', function () { closeHomeDesk(); });
    var homeDeskBackdrop = $('homeDeskBackdrop');
    if (homeDeskBackdrop) homeDeskBackdrop.addEventListener('click', function () { closeHomeDesk(); });
    var themeBtn = $('themeBtn');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
    var settingsBtn = $('settingsBtn');
    if (settingsBtn) settingsBtn.addEventListener('click', openSettings);
    var chatModelCard = $('chatModelCard');
    if (chatModelCard) chatModelCard.addEventListener('click', openSettings);
    var chatRailExpandBtn = $('chatRailExpandBtn');
    if (chatRailExpandBtn) chatRailExpandBtn.addEventListener('click', function () { setChatRailCollapsed(false); });
    var mindmapRailExpandBtn = $('mindmapRailExpandBtn');
    if (mindmapRailExpandBtn) mindmapRailExpandBtn.addEventListener('click', function () { setMindmapRailCollapsed(false); });
    var chatInsightsCollapseBtn = $('chatInsightsCollapseBtn');
    if (chatInsightsCollapseBtn) chatInsightsCollapseBtn.addEventListener('click', function () { setChatInsightsCollapsed(true); });
    var chatInsightsExpandBtn = $('chatInsightsExpandBtn');
    if (chatInsightsExpandBtn) chatInsightsExpandBtn.addEventListener('click', function () { setChatInsightsCollapsed(false); });
    var chatInsightDocBtn = $('chatInsightDocBtn');
    if (chatInsightDocBtn) chatInsightDocBtn.addEventListener('click', function () { setChatDocPickerOpen(!state.chatDocPickerOpen); });
    var chatDocPicker = $('chatDocPicker');
    if (chatDocPicker) chatDocPicker.addEventListener('click', function (event) {
      var option = event.target.closest('[data-chat-doc-key]');
      if (!option) return;
      selectChatDocument(option.getAttribute('data-chat-doc-key'));
    });
    document.querySelectorAll('[data-chat-panel]').forEach(function (tab) {
      tab.addEventListener('click', function () { switchChatPanel(tab.getAttribute('data-chat-panel')); });
    });
    var chatRelatedToggle = $('chatRelatedToggle');
    if (chatRelatedToggle) chatRelatedToggle.addEventListener('click', function () {
      var expanded = chatRelatedToggle.getAttribute('aria-expanded') !== 'false';
      chatRelatedToggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      var relatedList = $('chatRelated');
      if (relatedList) relatedList.hidden = expanded;
    });
    function openChatSource(target) {
      var row = target && target.closest ? target.closest('[data-chat-source]') : null;
      if (!row || !state.activeFile) return;
      var chapter = decodeURIComponent(row.getAttribute('data-chat-source') || '');
      if (chapter) state.previewChapter = chapter;
      state.previewMode = 'canvas';
      navigate('mindmap', { file: state.activeFile, previewMode: 'canvas' });
    }
    var chatSources = $('chatSources');
    if (chatSources) chatSources.addEventListener('click', function (event) { openChatSource(event.target); });
    var chatRelated = $('chatRelated');
    if (chatRelated) chatRelated.addEventListener('click', function (event) { openChatSource(event.target); });
    var chatSettingsPanel = $('chatSettingsPanel');
    if (chatSettingsPanel) chatSettingsPanel.addEventListener('click', function (event) {
      var reset = event.target.closest('#chatPrefsReset');
      if (reset) { resetChatPrefs(); return; }
      var pref = event.target.closest('[data-chat-pref]');
      if (!pref) return;
      setChatPreference(pref.getAttribute('data-chat-pref'), pref.getAttribute('data-value'));
    });
    var customInstruction = $('chatCustomInstruction');
    if (customInstruction) {
      var customSaveTimer = null;
      customInstruction.addEventListener('input', function () {
        state.chatPrefs.customInstruction = customInstruction.value.slice(0, 300);
        if (customSaveTimer) clearTimeout(customSaveTimer);
        customSaveTimer = setTimeout(function () {
          saveChatPrefs();
          if (window.ReviewWidget && window.ReviewWidget.setPreferences) {
            window.ReviewWidget.setPreferences(state.chatPrefs);
          }
        }, 280);
      });
    }
    document.addEventListener('kt-review-mode', function (event) {
      if (event.detail && event.detail.mode) syncChatModeUI(event.detail.mode);
    });
    document.addEventListener('kt-review-activity', function (event) {
      var d = event.detail || {};
      syncChatActivityUI({
        phase: d.phase || 'idle',
        detail: d.detail || '',
        mode: d.mode || '',
        file: d.file || '',
        chapter: d.chapter || ''
      });
    });
    document.addEventListener('kt-review-conversation', function (event) {
      var d = event.detail || {};
      if (d.file && state.activeFile && d.file !== state.activeFile) return;
      setChatHeroConversationActive(!!d.active);
    });
    var parseSettingsBtn = $('parseSettingsBtn');
    if (parseSettingsBtn) parseSettingsBtn.addEventListener('click', openSettings);
    var openSettingsFromParse = $('openSettingsFromParse');
    if (openSettingsFromParse) openSettingsFromParse.addEventListener('click', openSettings);
    var closeSettingsBtn = $('closeSettingsBtn');
    if (closeSettingsBtn) closeSettingsBtn.addEventListener('click', closeSettings);
    var backdrop = $('drawerBackdrop');
    if (backdrop) backdrop.addEventListener('click', closeSettings);

    var docContext = $('docContextBtn');
    if (docContext) {
      docContext.addEventListener('click', function (event) {
        event.stopPropagation();
        if (state.docPopoverOpen) closeDocPopover();
        else openDocPopover();
      });
    }
    var docPopoverClose = $('docPopoverClose');
    if (docPopoverClose) docPopoverClose.addEventListener('click', closeDocPopover);
    var docPopoverList = $('docPopoverList');
    if (docPopoverList) {
      docPopoverList.addEventListener('click', function (event) {
        var row = event.target.closest('[data-doc-key]');
        if (!row) return;
        var key = row.getAttribute('data-doc-key');
        setActiveFile(key, { silent: true });
        closeDocPopover();
        navigate('mindmap', { file: key });
      });
    }
    var docOverview = $('docPopoverOverview');
    if (docOverview) docOverview.addEventListener('click', function () { openResource(state.activeFile, 'overview'); });
    var docMindmap = $('docPopoverMindmap');
    if (docMindmap) docMindmap.addEventListener('click', function () { openResource(state.activeFile, 'canvas'); });
    var docChat = $('docPopoverChat');
    if (docChat) docChat.addEventListener('click', function () { navigate('chat', { file: state.activeFile }); });

    // Command palette
    var commandBtn = $('commandBtn');
    if (commandBtn) commandBtn.addEventListener('click', openCommandPalette);
    var paletteBackdrop = $('paletteBackdrop');
    if (paletteBackdrop) paletteBackdrop.addEventListener('click', closeCommandPalette);
    var paletteInput = $('paletteInput');
    if (paletteInput) {
      paletteInput.addEventListener('input', function () { filterPalette(paletteInput.value); });
      paletteInput.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          selectPaletteIndex(state.commandIndex + 1);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          selectPaletteIndex(state.commandIndex - 1);
        } else if (event.key === 'Enter') {
          event.preventDefault();
          runPaletteElement(paletteItems()[state.commandIndex]);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          closeCommandPalette();
        }
      });
    }
    var commandPalette = $('commandPalette');
    if (commandPalette) {
      commandPalette.addEventListener('keydown', function (event) {
        if (!state.commandOpen || event.key !== 'Tab') return;
        var input = $('paletteInput');
        var focusables = [input].concat(paletteItems());
        var current = focusables.indexOf(document.activeElement);
        if (current < 0) current = 0;
        var next = event.shiftKey ? current - 1 : current + 1;
        if (next < 0) next = focusables.length - 1;
        if (next >= focusables.length) next = 0;
        event.preventDefault();
        focusables[next].focus();
      });
    }
    initScrollFades();
    var paletteContent = $('paletteContent');
    if (paletteContent) {
      paletteContent.addEventListener('click', function (event) {
        var item = event.target.closest('.palette-item');
        if (item) runPaletteElement(item);
      });
      paletteContent.addEventListener('mousemove', function (event) {
        var item = event.target.closest('.palette-item');
        if (!item || item.hidden) return;
        var items = paletteItems();
        var idx = items.indexOf(item);
        if (idx >= 0 && idx !== state.commandIndex) selectPaletteIndex(idx);
      });
    }

    // Segmented scene bar: WAI-ARIA roving focus
    var sceneBar = $('sceneBar');
    if (sceneBar) {
      sceneBar.addEventListener('keydown', function (event) {
        var tabs = Array.prototype.slice.call(sceneBar.querySelectorAll('.scene-tab'));
        var current = tabs.indexOf(document.activeElement);
        if (current < 0) current = tabs.findIndex(function (t) { return t.classList.contains('is-active'); });
        var next = current;
        if (event.key === 'ArrowRight') next = (current + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (current - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else return;
        event.preventDefault();
        tabs[next].focus();
        navigate(tabs[next].getAttribute('data-scene-link'));
      });
    }

    // Close transient surfaces when clicking elsewhere
    document.addEventListener('click', function (event) {
      if (state.docPopoverOpen && !event.target.closest('#docPopover') && !event.target.closest('#docContextBtn')) {
        closeDocPopover();
      }
      if (state.chatDocPickerOpen && !event.target.closest('#chatDocPicker') && !event.target.closest('#chatInsightDocBtn')) {
        setChatDocPickerOpen(false);
      }
      if (state.chatRailDocPickerOpen && !event.target.closest('#railKbPicker') && !event.target.closest('[data-rail-action="chat-switch-doc"]')) {
        setRailKbPickerOpen(false);
      }
    });



    var workspaceRail = $('workspaceRail');
    if (workspaceRail) workspaceRail.addEventListener('click', function (event) {
      var kbOption = event.target.closest('[data-rail-kb-key]');
      if (kbOption && workspaceRail.contains(kbOption)) {
        selectChatDocument(decodeURIComponent(kbOption.getAttribute('data-rail-kb-key') || ''));
        return;
      }
      var modeBtn = event.target.closest('[data-chat-mode]');
      if (modeBtn && workspaceRail.contains(modeBtn)) {
        var mode = modeBtn.getAttribute('data-chat-mode');
        if (window.ReviewWidget && window.ReviewWidget.setMode) window.ReviewWidget.setMode(mode);
        syncChatModeUI(mode);
        return;
      }
      var filterBtn = event.target.closest('[data-rail-filter]');
      if (filterBtn) {
        state.libraryFilter = filterBtn.getAttribute('data-rail-filter') || 'all';
        var pill = document.querySelector('#libraryFilters [data-filter="' + state.libraryFilter + '"]');
        if (pill) {
          document.querySelectorAll('#libraryFilters [data-filter]').forEach(function (el) { el.classList.toggle('is-active', el === pill); });
        }
        if (state.scene !== 'library') navigate('library');
        else renderLibrary();
        return;
      }
      var btn = event.target.closest('[data-rail-action]');
      if (!btn) return;
      var action = btn.getAttribute('data-rail-action');
      if (action === 'parse-main') { navigate('parse'); window.scrollTo(0, 0); }
      else if (action === 'parse-history') {
        navigate('parse');
        window.setTimeout(function () {
          var card = document.querySelector('.recent-task-card');
          if (card) card.scrollIntoView({ behavior: KT.prefersReducedMotion() ? 'auto' : 'smooth', block: 'center' });
        }, 120);
      } else if (action === 'library-all') {
        state.libraryFilter = 'all';
        var allPill = document.querySelector('#libraryFilters [data-filter="all"]');
        if (allPill) allPill.click();
        navigate('library');
      } else if (action === 'chat-main') {
        navigate('chat', { file: state.activeFile });
      } else if (action === 'chat-new') {
        resetChatActivity('新会话 · 等待提问');
        if (window.ReviewWidget && window.ReviewWidget.restart) window.ReviewWidget.restart();
        else navigate('chat', { file: state.activeFile });
      } else if (action === 'chat-settings') {
        if (state.chatInsightsCollapsed) setChatInsightsCollapsed(false);
        switchChatPanel('settings');
      } else if (action === 'chat-switch-doc') {
        state.chatRailDocPickerOpen = !state.chatRailDocPickerOpen;
        setRailKbPickerOpen(state.chatRailDocPickerOpen);
      } else if (action === 'chat-rail-collapse') {
        setChatRailCollapsed(true);
      } else if (action === 'chat-chapter') {
        var chapter = decodeURIComponent(btn.getAttribute('data-rail-chapter') || '');
        state.previewChapter = chapter;
        state.previewMode = 'canvas';
        navigate('mindmap', { file: state.activeFile, previewMode: 'canvas' });
      } else if (action === 'mindmap-outline-toggle') {
        var outlineTitle = decodeURIComponent(btn.getAttribute('data-outline-title') || '');
        var outlineList = document.querySelector('#workspaceRail .rail-outline-list');
        var outlineScroll = outlineList ? outlineList.scrollTop : 0;
        var outlineWasOpen = btn.getAttribute('aria-expanded') === 'true';
        state.mindmapOutlineOpen[outlineTitle] = !outlineWasOpen;
        renderWorkspaceRail('mindmap');
        var nextList = document.querySelector('#workspaceRail .rail-outline-list');
        if (nextList) nextList.scrollTop = outlineScroll;
        window.requestAnimationFrame(function () {
          var toggles = document.querySelectorAll('[data-rail-action="mindmap-outline-toggle"]');
          for (var i = 0; i < toggles.length; i++) {
            if (decodeURIComponent(toggles[i].getAttribute('data-outline-title') || '') === outlineTitle) {
              toggles[i].focus();
              break;
            }
          }
        });
      } else if (action === 'mindmap-rail-collapse') {
        setMindmapRailCollapsed(true);
      } else if (action === 'mindmap-chapter') {
        var mindmapChapter = decodeURIComponent(btn.getAttribute('data-rail-chapter') || '');
        selectPreviewChapter(mindmapChapter);
      } else if (action === 'library-favorites') {
        setLibraryFilter('favorites');
        navigate('library');
      }
    });

    // parse upload
    var dropZone = $('dropZone');
    var fileInput = $('fileInput');
    var browseFilesBtn = $('browseFilesBtn');
    if (dropZone && fileInput) {
      dropZone.addEventListener('click', function (event) {
        if (event.target.closest('button') && event.target.closest('button') !== browseFilesBtn) return;
        if (browseFilesBtn && event.target.closest('button') === browseFilesBtn) return;
        fileInput.click();
      });
      dropZone.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          fileInput.click();
        }
      });
      dropZone.addEventListener('dragover', function (event) {
        event.preventDefault();
        dropZone.classList.add('is-dragging');
      });
      dropZone.addEventListener('dragleave', function () { dropZone.classList.remove('is-dragging'); });
      dropZone.addEventListener('drop', function (event) {
        event.preventDefault();
        dropZone.classList.remove('is-dragging');
        addFiles(event.dataTransfer.files);
      });
      fileInput.addEventListener('change', function () { addFiles(fileInput.files); fileInput.value = ''; });
    }
    if (browseFilesBtn && fileInput) browseFilesBtn.addEventListener('click', function (event) {
      event.stopPropagation();
      fileInput.click();
    });

    var fileList = $('parseFileList');
    if (fileList) {
      fileList.addEventListener('click', function (event) {
        var btn = event.target.closest('[data-remove-file]');
        if (!btn) return;
        var idx = Number(btn.getAttribute('data-remove-file'));
        state.selectedFiles.splice(idx, 1);
        renderParseFiles();
      });
    }
    var startBtn = $('startParseBtn');
    if (startBtn) startBtn.addEventListener('click', startProcessing);
    var resetBtn = $('resultResetBtn');
    if (resetBtn) resetBtn.addEventListener('click', resetParseResult);
    var resultMindmap = $('resultMindmapBtn');
    if (resultMindmap) resultMindmap.addEventListener('click', function () { openResource(state.activeFile, 'canvas'); });
    var resultOverview = $('resultOverviewBtn');
    if (resultOverview) resultOverview.addEventListener('click', function () { openResource(state.activeFile, 'overview'); });
    var resultChat = $('resultChatBtn');
    if (resultChat) resultChat.addEventListener('click', function () { navigate('chat', { file: state.activeFile }); });

    // library controls
    var search = $('librarySearch');
    if (search) {
      search.addEventListener('input', function () { state.librarySearch = search.value; renderLibrary(); });
    }
    var filters = $('libraryFilters');
    if (filters) {
      filters.addEventListener('click', function (event) {
        var pill = event.target.closest('[data-filter]');
        if (!pill) return;
        setLibraryFilter(pill.getAttribute('data-filter'));
      });
    }
    var importPackageBtn = $('importPackageBtn');
    var packageInput = $('packageInput');
    if (importPackageBtn && packageInput) {
      importPackageBtn.addEventListener('click', function () { packageInput.click(); });
      packageInput.addEventListener('change', function () {
        var file = packageInput.files && packageInput.files[0];
        packageInput.value = '';
        importResourcePackage(file);
      });
    }
    var sort = $('librarySort');
    if (sort) sort.addEventListener('change', function () {
      state.librarySort = sort.value;
      if (state.librarySort === 'custom') ensureLibraryOrder();
      renderLibrary();
    });
    initLibraryDrag();

    // preview modes
    var subTabs = document.querySelectorAll('[data-preview-mode]');
    subTabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        state.previewMode = tab.getAttribute('data-preview-mode');
        renderMindmap();
        syncPreviewHash();
      });
    });
    document.querySelectorAll('[data-preview-view]').forEach(function (btn) {
      btn.addEventListener('click', function () { switchPreviewMode(btn.getAttribute('data-preview-view')); });
    });
    var previewViewRail = $('previewViewRail');
    if (previewViewRail) previewViewRail.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      var next = state.previewMode === 'overview' ? 'canvas' : 'overview';
      switchPreviewMode(next);
      var target = previewViewRail.querySelector('[data-preview-view="' + next + '"]');
      if (target) target.focus();
    });
    initPreviewSplit();
    var openFull = $('openFullPreviewBtn');
    if (openFull) openFull.addEventListener('click', function () {
      if (state.activeFile) window.open(previewUrl(state.previewMode, false), '_blank', 'noopener');
    });
    var previewChat = $('previewChatBtn');
    if (previewChat) previewChat.addEventListener('click', function () {
      navigate('chat', { file: state.activeFile, explicit: !!state.previewChapter, chapter: state.previewChapter || '' });
    });

    window.addEventListener('hashchange', route);
    window.addEventListener('message', function (event) {
      if (event.origin !== window.location.origin || !event.data) return;
      if (event.data.type === 'kt-open-chat') {
        var file = event.data.file || state.activeFile;
        if (file) setActiveFile(file, { silent: true });
        navigate('chat', { file: file, explicit: true, chapter: event.data.chapter || '', scope: event.data.scope || 'all' });
      } else if (event.data.type === 'kt-open-library') {
        navigate('library');
      } else if (event.data.type === 'kt-open-resource') {
        openResource(event.data.file || state.activeFile, event.data.mode || 'overview');
      } else if (event.data.type === 'kt-theme-request') {
        event.source && event.source.postMessage({ type: 'kt-theme', theme: KT.getTheme() }, window.location.origin);
      }
    });

    document.addEventListener('keydown', function (event) {
      var modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        if (state.commandOpen) closeCommandPalette();
        else openCommandPalette();
      } else if (modifier && event.key === ',') {
        event.preventDefault();
        openSettings();
      } else if (event.key === 'Escape') {
        if (state.commandOpen) { closeCommandPalette(); return; }
        if (state.chatRailDocPickerOpen) {
          setRailKbPickerOpen(false);
          var railKbTrigger = document.querySelector('#workspaceRail [data-rail-action="chat-switch-doc"]');
          if (railKbTrigger) railKbTrigger.focus();
          return;
        }
        if (state.chatDocPickerOpen) {
          setChatDocPickerOpen(false);
          var chatDocTrigger = $('chatInsightDocBtn');
          if (chatDocTrigger) chatDocTrigger.focus();
          return;
        }
        if (state.docPopoverOpen) { closeDocPopover(); return; }
        if (state.homeDeskOpen) { closeHomeDesk(); return; }
        var drawer = $('settingsDrawer');
        if (drawer && drawer.classList.contains('is-open')) closeSettings();
      } else if (event.key === 'Tab' && state.homeDeskOpen) {
        var desk = $('homeDashboard');
        if (!desk) return;
        var focusables = Array.prototype.slice.call(desk.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'));
        if (!focusables.length) return;
        var first = focusables[0], last = focusables[focusables.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === desk)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    });
  }

  function init() {
    if (initialized) return;
    initialized = true;
    KT.initTheme();
    if (!state.activeFile) {
      try { state.activeFile = localStorage.getItem(ACTIVE_FILE_KEY) || ''; } catch (err) {}
    }
    state.chatPrefs = loadChatPrefs();
    try {
      var savedLibraryOrder = JSON.parse(localStorage.getItem(LIBRARY_ORDER_KEY) || '[]');
      state.libraryOrder = Array.isArray(savedLibraryOrder) ? savedLibraryOrder : [];
    } catch (err) { state.libraryOrder = []; }
    try {
      var savedSplit = JSON.parse(localStorage.getItem(PREVIEW_SPLIT_KEY) || 'null');
      if (savedSplit && typeof savedSplit.ratio === 'number') state.previewSplitRatio = Math.max(0.18, Math.min(0.48, savedSplit.ratio));
      if (savedSplit && savedSplit.collapsed) state.previewSplitCollapsed = true;
    } catch (err) {}
    try {
      state.chatRailCollapsed = localStorage.getItem(CHAT_RAIL_KEY) === '1';
      state.chatInsightsCollapsed = localStorage.getItem(CHAT_INSIGHTS_KEY) === '1';
      state.mindmapRailCollapsed = localStorage.getItem(MINDMAP_RAIL_KEY) === '1';
    } catch (err) {}
    syncThemeButton();
    bindEvents();
    renderParseFiles();
    loadSettings();
    loadWorkspaceData();
    route();

    window.KTClient = {
      navigate: navigate,
      openResource: openResource,
      setActiveFile: setActiveFile,
      getState: function () { return state; },
      refresh: loadWorkspaceData
    };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
