/*
 * tree_mindmap.html — page wiring tests (jsdom, fake KTMap engine).
 *
 * The real engine/layout is validated in Chrome (see docs) and by the pure
 * adapter tests; this suite checks the page contract: data loading, deep links,
 * header controls, export routing, offline mode, and error states.
 *
 * Run: JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_mindmap.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/tree_mindmap.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = (ms) => new Promise(resolve => setTimeout(resolve, ms || 30));

function makeTree() {
  return {
    title: '教心',
    guide: '全书导学',
    children: [{
      title: '教心',
      level: 0,
      children: [
        {
          title: '第一章 心理发展',
          level: 1,
          guide: '本章导学',
          summary: '发展概述',
          exam_points: [{ point: '发展阶段', type: '简答', frequency: '高频' }],
          children: [
            { title: '第一节 规律', level: 2, children: [] },
            { title: '第二节 认知', level: 2, children: [] }
          ]
        },
        {
          title: '第二章 学习理论',
          level: 1,
          children: [
            { title: '第三节 行为主义', level: 2, children: [] }
          ]
        }
      ]
    }]
  };
}

const inlineMatch = HTML.match(/<script>\s*\(function \(\) \{[\s\S]*?\}\)\(\);\s*<\/script>/);
if (!inlineMatch) {
  console.error('Cannot find the page boot script');
  process.exit(2);
}
const BOOT = inlineMatch[0].replace(/^<script>|<\/script>$/g, '');
const HTML_STRIPPED = HTML.replace(inlineMatch[0], '');
const TREE = makeTree();

function makeDom(opts) {
  opts = opts || {};
  const vc = new VirtualConsole();
  const jsdomErrors = [];
  vc.on('jsdomError', e => {
    if (/Could not load|not implemented|Not implemented/.test(e.message)) return;
    jsdomErrors.push(e.message);
  });
  const url = opts.url || 'http://localhost/ui/tree_mindmap.html?file=test_tree.json';
  const dom = new JSDOM(HTML_STRIPPED, {
    url,
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) {
      window.KT_OFFLINE = !!opts.offline;
      window.__TEST_TREE = opts.tree || TREE;
      window.__TEST_FETCHES = [];
      window.__TEST_MAP_CALLS = [];
      window.__TEST_EXPORTS = [];
      window.__TEST_REVIEW = [];
      window.matchMedia = window.matchMedia || function () {
        return { matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} };
      };
      window.confirm = () => true;
      window.alert = () => {};
      window.open = () => null;
      window.fetch = function (input) {
        const url = String(input);
        window.__TEST_FETCHES.push(url);
        if (opts.fetchStatus && opts.fetchStatus !== 200) {
          return Promise.resolve({
            ok: false,
            status: opts.fetchStatus,
            json: () => Promise.resolve({ error: opts.fetchError || 'not found' })
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            tree: window.__TEST_TREE,
            key: opts.key || 'test_tree.json',
            rev: opts.rev || 'rev1',
            title: window.__TEST_TREE.title
          })
        });
      };
    }
  });
  const win = dom.window;
  const doc = win.document;
  if (opts.offline) {
    const offlineTree = doc.createElement('script');
    offlineTree.type = 'application/json';
    offlineTree.id = 'kt-offline-tree';
    offlineTree.textContent = JSON.stringify(opts.tree || TREE);
    doc.body.appendChild(offlineTree);
  }
  if (url.includes('embed=1')) doc.documentElement.setAttribute('data-embed', '1');
  win.eval(COMMON);
  win.ReviewWidget = {
    mount(o) { win.__TEST_REVIEW.push({ type: 'mount', opts: o }); },
    open() { win.__TEST_REVIEW.push({ type: 'open' }); },
    setContext(o) { win.__TEST_REVIEW.push({ type: 'context', opts: o }); },
    ask(q) { win.__TEST_REVIEW.push({ type: 'ask', q }); },
    startChapter(c, s) { win.__TEST_REVIEW.push({ type: 'chapter', c, s }); }
  };
  win.KTMapExports = {
    exportPNG() { win.__TEST_EXPORTS.push('png'); },
    exportModuleHandout() { win.__TEST_EXPORTS.push('handout-current'); },
    exportWholeHandout() { win.__TEST_EXPORTS.push('handout-all'); },
    exportOutlineMarkdown() { win.__TEST_EXPORTS.push('outline'); },
    exportAnkiCSV() { win.__TEST_EXPORTS.push('anki'); },
    exportDataPackage() { win.__TEST_EXPORTS.push('package'); }
  };
  win.KTMap = {
    init(mapOpts) {
      win.__TEST_MAP_CALLS.push({ type: 'init', opts: mapOpts });
      const adapter = {
        title: mapOpts.tree.title,
        count() {
          let n = 0;
          (function walk(node) { n++; (node.children || []).forEach(walk); })(mapOpts.tree);
          return n;
        },
        getDisplayTree() { return mapOpts.tree; },
        getNode() { return null; },
        getPath() { return []; },
        search() { return []; }
      };
      const state = {
        readonly: !!mapOpts.readonly,
        offline: !!mapOpts.offline,
        embed: !!mapOpts.embed,
        key: mapOpts.key,
        rev: mapOpts.rev,
        editing: false,
        layout: 'SIDE',
        expandLevel: 2
      };
      const api = {
        on(name, fn) { state['on_' + name] = fn; },
        getAdapter() { return adapter; },
        getState() { return state; },
        getSelectedId() { return ''; },
        getPath() { return []; },
        getNode() { return null; },
        setEditable(v) { state.editing = !!v; win.__TEST_MAP_CALLS.push({ type: 'editable', value: !!v }); },
        isEditing() { return !!state.editing; },
        toggleSearch() { win.__TEST_MAP_CALLS.push({ type: 'search' }); },
        toggleDrawer() { win.__TEST_MAP_CALLS.push({ type: 'drawer' }); },
        openOutline() { win.__TEST_MAP_CALLS.push({ type: 'outline' }); },
        setLayout(v) { win.__TEST_MAP_CALLS.push({ type: 'layout', value: v }); },
        setExpandLevel(v) { win.__TEST_MAP_CALLS.push({ type: 'expand', value: v }); },
        expandAll() { win.__TEST_MAP_CALLS.push({ type: 'expandAll' }); },
        collapseAll() { win.__TEST_MAP_CALLS.push({ type: 'collapseAll' }); },
        fit() { win.__TEST_MAP_CALLS.push({ type: 'fit' }); },
        zoomTo(v) { win.__TEST_MAP_CALLS.push({ type: 'zoom', value: v }); },
        toggleMinimap() { win.__TEST_MAP_CALLS.push({ type: 'minimap' }); },
        toggleHelp() { win.__TEST_MAP_CALLS.push({ type: 'help' }); },
        closeDetail() {},
        exportPNG() {},
        toast() {},
        save() {},
        undo() {},
        redo() {},
        setTheme() {},
        focusByTitle() {},
        spotlight() {},
        clearSpotlight() {}
      };
      win.__FAKE_MAP = api;
      return Promise.resolve(api);
    }
  };
  win.eval(BOOT);
  return { dom, win, doc, jsdomErrors };
}

function click(win, el) {
  if (!el) throw new Error('missing element');
  el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
}
function change(win, el, value) {
  el.value = value;
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

(async function run() {
  console.log('\n[tree_mindmap page]');

  // ── 1. normal load ───────────────────────────────────────
  {
    const { win, doc, jsdomErrors } = makeDom();
    await tick(100);
    const init = win.__TEST_MAP_CALLS.find(c => c.type === 'init');
    check('boot initializes KTMap', !!init);
    check('passes tree and key', init && init.opts.tree.title === '教心' && init.opts.key === 'test_tree.json');
    check('passes rev', init && init.opts.rev === 'rev1');
    check('document title updated', doc.title.includes('教心'));
    check('stats show node count', doc.getElementById('stats').textContent.includes('个知识点'));
    check('review widget mounted', win.__TEST_REVIEW.some(r => r.type === 'mount' && r.opts.file === 'test_tree.json'));
    check('fetch used canonical tree endpoint', win.__TEST_FETCHES.some(u => u.includes('/api/tree/test_tree.json')));
    check('no jsdom errors', jsdomErrors.length === 0, jsdomErrors[0]);

    click(win, doc.getElementById('btnSearch'));
    check('search button calls KTMap.toggleSearch', win.__TEST_MAP_CALLS.some(c => c.type === 'search'));
    click(win, doc.getElementById('btnDrawer'));
    check('drawer button calls KTMap.toggleDrawer', win.__TEST_MAP_CALLS.some(c => c.type === 'drawer'));
    click(win, doc.getElementById('btnEdit'));
    check('edit button toggles editable', win.__TEST_MAP_CALLS.some(c => c.type === 'editable' && c.value === true));
    click(win, doc.getElementById('btnReview'));
    check('review button opens widget', win.__TEST_REVIEW.some(r => r.type === 'open'));

    change(win, doc.getElementById('layoutSelect'), 'RIGHT');
    check('layout select calls setLayout', win.__TEST_MAP_CALLS.some(c => c.type === 'layout' && c.value === 'RIGHT'));
    change(win, doc.getElementById('expandLevel'), '3');
    check('expand select calls setExpandLevel', win.__TEST_MAP_CALLS.some(c => c.type === 'expand' && c.value === 3));

    click(win, doc.querySelector('[data-ktm-view="fit"]'));
    click(win, doc.querySelector('[data-ktm-view="expand-all"]'));
    click(win, doc.querySelector('[data-ktm-view="collapse-all"]'));
    click(win, doc.querySelector('[data-ktm-view="minimap"]'));
    click(win, doc.querySelector('[data-ktm-view="help"]'));
    check('view menu actions routed', ['fit', 'expandAll', 'collapseAll', 'minimap', 'help'].every(t => win.__TEST_MAP_CALLS.some(c => c.type === t)));

    click(win, doc.querySelector('[data-ktm-export="png"]'));
    click(win, doc.querySelector('[data-ktm-export="handout-current"]'));
    click(win, doc.querySelector('[data-ktm-export="handout-all"]'));
    click(win, doc.querySelector('[data-ktm-export="outline"]'));
    click(win, doc.querySelector('[data-ktm-export="anki"]'));
    click(win, doc.querySelector('[data-ktm-export="package"]'));
    check('export menu routes all actions', ['png', 'handout-current', 'handout-all', 'outline', 'anki', 'package'].every(x => win.__TEST_EXPORTS.includes(x)));

    const beforeTheme = doc.documentElement.getAttribute('data-theme');
    click(win, doc.getElementById('themeToggle'));
    check('theme toggle changes theme', doc.documentElement.getAttribute('data-theme') !== beforeTheme);

    click(win, doc.querySelector('[data-ktm-drawer-tab="outline"]'));
    check('outline tab calls openOutline', win.__TEST_MAP_CALLS.some(c => c.type === 'outline'));
    check('chapters panel hidden on outline tab', doc.getElementById('chapterList').hidden === true);
    check('outline panel shown', doc.getElementById('outlineMount').hidden === false);
  }

  // ── 2. deep links ────────────────────────────────────────
  {
    const { win } = makeDom({ url: 'http://localhost/ui/tree_mindmap.html?file=test_tree.json&chapter=%E7%AC%AC%E4%B8%80%E7%AB%A0%20%E5%BF%83%E7%90%86%E5%8F%91%E5%B1%95&node=n_x' });
    await tick(60);
    const init = win.__TEST_MAP_CALLS.find(c => c.type === 'init');
    check('chapter deep link passed', init && init.opts.chapter === '第一章 心理发展');
    check('node deep link passed', init && init.opts.nodeId === 'n_x');
  }

  // ── 3. embed mode ────────────────────────────────────────
  {
    const { win, doc } = makeDom({ url: 'http://localhost/ui/tree_mindmap.html?file=test_tree.json&embed=1' });
    await tick(60);
    const init = win.__TEST_MAP_CALLS.find(c => c.type === 'init');
    check('embed flag passed', init && init.opts.embed === true);
    check('review widget not mounted in embed', !win.__TEST_REVIEW.some(r => r.type === 'mount'));
    check('embed hides drawer button', win.getComputedStyle(doc.getElementById('btnDrawer')).display === 'none');
    check('embed hides review button', win.getComputedStyle(doc.getElementById('btnReview')).display === 'none');
  }

  // ── 4. offline mode ──────────────────────────────────────
  {
    const { win, doc } = makeDom({ offline: true, url: 'http://localhost/ui/tree_mindmap.html' });
    await tick(60);
    const init = win.__TEST_MAP_CALLS.find(c => c.type === 'init');
    check('offline flag passed', init && init.opts.offline === true && init.opts.readonly === true);
    check('offline does not fetch', win.__TEST_FETCHES.length === 0);
    check('offline hides edit', doc.getElementById('btnEdit').hidden === true);
    check('offline hides review', doc.getElementById('btnReview').hidden === true);
  }

  // ── 5. missing params / errors ───────────────────────────
  {
    const { doc } = makeDom({ url: 'http://localhost/ui/tree_mindmap.html' });
    await tick(60);
    check('missing params shows error', doc.getElementById('chart').textContent.includes('导图加载失败'));
  }
  {
    const { doc } = makeDom({ url: 'http://localhost/ui/tree_mindmap.html?file=x.json', fetchStatus: 404, fetchError: '未找到资料' });
    await tick(60);
    check('404 shows error message', doc.getElementById('chart').textContent.includes('未找到资料'));
  }

  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch(err => {
  console.error('HARNESS ERROR:', err);
  process.exit(2);
});
