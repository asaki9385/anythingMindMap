/*
 * KTMap controller with the real Mind Elixir engine in jsdom.
 * Layout geometry is zero in jsdom, so this suite checks data/DOM/event wiring:
 * init, selection, detail, search, theme, layout, edit/save, undo, minimap.
 *
 * Run: JSDOM_PATH=/path/to/jsdom node test_map_controller.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/tree_mindmap.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const VENDOR = fs.readFileSync(path.join(PROJ, 'ui/vendor/mind-elixir/mind-elixir.iife.js'), 'utf8');
const ADAPTER = fs.readFileSync(path.join(PROJ, 'ui/map/map_adapter.js'), 'utf8');
const CONTROLLER = fs.readFileSync(path.join(PROJ, 'ui/map/knowledge_map.js'), 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = (ms) => new Promise(resolve => setTimeout(resolve, ms || 60));

const bootMatch = HTML.match(/<script>\s*\(function \(\) \{[\s\S]*?\}\)\(\);\s*<\/script>/);
const HTML_STRIPPED = HTML.replace(bootMatch[0], '');
const TREE = {
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
        content: '<p>正文</p>',
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

const vc = new VirtualConsole();
const runtimeErrors = [];
vc.on('jsdomError', error => {
  if (/Could not load|not implemented|Not implemented/.test(error.message)) return;
  runtimeErrors.push(error.message);
});
const dom = new JSDOM(HTML_STRIPPED, {
  url: 'http://localhost/ui/tree_mindmap.html?file=test_map',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole: vc
});
const win = dom.window;
const doc = win.document;

win.document.fonts = { ready: Promise.resolve() };
win.matchMedia = win.matchMedia || function () {
  return { matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} };
};
win.PointerEvent = win.PointerEvent || win.MouseEvent;
win.ResizeObserver = win.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} };
if (win.crypto && !win.crypto.randomUUID) {
  try { win.crypto.randomUUID = () => 'u' + Math.random().toString(36).slice(2); } catch (err) {}
}
win.requestAnimationFrame = win.requestAnimationFrame || (cb => setTimeout(() => cb(Date.now()), 16));
win.HTMLElement.prototype.scrollIntoView = function () {};
win.confirm = () => true;
win.alert = () => {};
win.open = () => null;

const saves = [];
win.fetch = async (url, options) => {
  const target = String(url);
  if (target.includes('/api/tree/save')) {
    const body = JSON.parse((options && options.body) || '{}');
    saves.push(body);
    return { ok: true, status: 200, json: async () => ({ ok: true, rev: 'r' + (saves.length + 1), node_count: 1 }) };
  }
  if (target.includes('/api/review/progress')) {
    return { ok: true, status: 200, json: async () => ({ unit_count: 0 }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};
win.ReviewWidget = {
  mount() {}, open() {}, close() {}, ask() {}, startChapter() {}, setContext() {}
};

win.eval(COMMON);
win.eval(VENDOR);
win.eval(ADAPTER);
win.eval(CONTROLLER);

(async function run() {
  console.log('\n[map controller]');
  let map;
  try {
    map = await win.KTMap.init({
      tree: JSON.parse(JSON.stringify(TREE)),
      key: 'test_map',
      rev: 'r1',
      embed: false,
      readonly: false
    });
  } catch (err) {
    console.error('INIT FAILED', err);
    process.exit(2);
  }
  await tick(200);
  check('real engine initializes', !!map && !!map.getMei());
  check('adapter count', map.getAdapter().count() === 6, String(map.getAdapter().count()));
  check('mind map DOM rendered', doc.querySelectorAll('#chart .me-tpc').length > 0, String(doc.querySelectorAll('#chart .me-tpc').length));
  check('chapter list rendered', doc.querySelectorAll('#chapterList .ktm-chapter').length === 2);
  check('aria tree set', doc.getElementById('chart').getAttribute('role') === 'tree');

  const chapterId = map.getAdapter().displayRoot.children[0].id;
  map.select(chapterId, { center: true, spotlight: true });
  await tick(100);
  check('selection opens detail', doc.getElementById('infoPanel').classList.contains('show'));
  check('detail title', (doc.querySelector('.ktm-detail-title') || {}).textContent === '第一章 心理发展');
  check('breadcrumb shows path', doc.getElementById('breadcrumb').textContent.includes('第一章 心理发展'));
  check('spotlight applied', !!doc.querySelector('#chart .ktm-dimmed'));

  const results = map.search('发展阶段');
  check('search finds exam point', results.length >= 1 && results[0].field === 'exam', JSON.stringify(results.slice(0, 2)));
  map.select(results[0].id, { center: true });
  await tick(80);
  check('search selection updates detail', (doc.querySelector('.ktm-detail-title') || {}).textContent === '第一章 心理发展');

  const beforeTheme = doc.querySelectorAll('#chart .me-tpc').length;
  map.setTheme();
  await tick(200);
  check('theme change keeps map', doc.querySelectorAll('#chart .me-tpc').length === beforeTheme);

  map.setLayout('RIGHT', { force: true });
  await tick(200);
  check('layout switch keeps map', doc.querySelectorAll('#chart .me-tpc').length > 0);
  check('layout switch updates engine direction', map.getMei().direction === 1, String(map.getMei().direction));
  map.setLayout('LEFT', { force: true });
  await tick(120);
  check('left layout updates engine direction', map.getMei().direction === 0, String(map.getMei().direction));
  map.setLayout('DOWN', { force: true });
  await tick(120);
  check('down layout updates engine direction', map.getMei().direction === 3, String(map.getMei().direction));
  map.setLayout('SIDE', { force: true });
  await tick(120);
  check('side layout updates engine direction', map.getMei().direction === 2, String(map.getMei().direction));

  map.setEditable(true);
  await tick(80);
  check('edit mode on', map.isEditing() === true);
  check('edit bar visible', doc.getElementById('ktmEditBar').hidden === false);
  check('edit bar has icons on every action', doc.querySelectorAll('#ktmEditBar button [data-icon]').length === 7,
    String(doc.querySelectorAll('#ktmEditBar button [data-icon]').length));
  check('toolbar keeps a single primary action', !doc.getElementById('btnEdit').classList.contains('is-primary')
    && doc.querySelector('#btnEdit .label').textContent === '编辑中');
  const beforeCount = map.getAdapter().count();
  const mei = map.getMei();
  const chapterTopic = mei.findEle(chapterId);
  await mei.addChild(chapterTopic);
  await tick(250);
  check('add child updates adapter', map.getAdapter().count() === beforeCount + 1, String(map.getAdapter().count()));
  map.save();
  await tick(250);
  check('save posts full tree', saves.length >= 1 && saves[0].key === 'test_map' && saves[0].base_rev === 'r1');
  check('save tree has ids', saves.length >= 1 && !!saves[0].tree.children[0].children[0].id);
  map.undo();
  await tick(250);
  check('undo removes child', map.getAdapter().count() === beforeCount, String(map.getAdapter().count()));
  // ── edit panel: node title + content editing ──────────────
  {
    const findNode = (node, id) => {
      if (!node) return null;
      if (node.id === id) return node;
      for (const child of node.children || []) {
        const hit = findNode(child, id);
        if (hit) return hit;
      }
      return null;
    };
    map.select(chapterId);
    await tick(120);
    check('edit panel title field', !!doc.querySelector('[data-ktm-field="title"]'));
    check('edit panel summary field', (doc.querySelector('[data-ktm-field="summary"]') || {}).tagName === 'TEXTAREA');
    check('edit panel content field', (doc.querySelector('[data-ktm-field="content"]') || {}).tagName === 'TEXTAREA');

    map.select(map.getAdapter().displayRoot.id);
    await tick(120);
    check('root title stays read-only', !doc.querySelector('[data-ktm-field="title"]'));

    map.select(chapterId);
    await tick(120);
    const titleField = doc.querySelector('[data-ktm-field="title"]');
    titleField.value = '第一章 心理发展（改名）';
    titleField.dispatchEvent(new win.Event('change', { bubbles: true }));
    await tick(200);
    check('panel title updates canvas', String(mei.findEle(chapterId).nodeObj.topic).includes('改名'),
      String(mei.findEle(chapterId).nodeObj.topic));

    const beforeContent = map.getAdapter().fromMindElixirData(mei.getData());
    check('content starts in tree', /正文/.test(String((findNode(beforeContent, chapterId) || {}).content)));

    const contentField = doc.querySelector('[data-ktm-field="content"]');
    contentField.value = '改写后的正文';
    contentField.dispatchEvent(new win.Event('input', { bubbles: true }));
    contentField.dispatchEvent(new win.Event('blur'));
    await tick(900);
    const edited = findNode(map.getAdapter().fromMindElixirData(mei.getData()), chapterId);
    check('panel content saved into tree', edited && edited.content === '改写后的正文', edited && edited.content);

    map.undo();
    await tick(200);
    const undone = findNode(map.getAdapter().fromMindElixirData(mei.getData()), chapterId);
    check('undo restores content', undone && String(undone.content) === '<p>正文</p>', String(undone && undone.content));
    check('undo keeps panel title change', String(mei.findEle(chapterId).nodeObj.topic).includes('改名'));

    // Clearing a heavy field must persist as a clear, not fall back to the old text.
    const clearField = doc.querySelector('[data-ktm-field="content"]');
    clearField.value = '';
    clearField.dispatchEvent(new win.Event('input', { bubbles: true }));
    clearField.dispatchEvent(new win.Event('blur'));
    await tick(900);
    const cleared = findNode(map.getAdapter().fromMindElixirData(mei.getData()), chapterId);
    check('cleared content stays cleared', cleared && cleared.content === '', JSON.stringify(cleared && cleared.content));

    // Adding 原文 to a node that had none must be undoable too.
    const bareId = map.getAdapter().displayRoot.children[0].children[0].id;
    map.select(bareId);
    await tick(150);
    const addField = doc.querySelector('[data-ktm-field="content"]');
    addField.value = '新增的正文';
    addField.dispatchEvent(new win.Event('input', { bubbles: true }));
    addField.dispatchEvent(new win.Event('blur'));
    await tick(900);
    const added = findNode(map.getAdapter().fromMindElixirData(mei.getData()), bareId);
    check('content added to bare node', added && added.content === '新增的正文', added && added.content);
    map.undo();
    await tick(200);
    const reverted = findNode(map.getAdapter().fromMindElixirData(mei.getData()), bareId);
    check('undo clears added content', reverted && !reverted.content, reverted && String(reverted.content));

    // Two fields typed in quick succession must both commit (per-field debounce).
    map.select(chapterId);
    await tick(150);
    const sumField = doc.querySelector('[data-ktm-field="summary"]');
    const noteField = doc.querySelector('[data-ktm-field="note"]');
    sumField.value = '并发摘要';
    sumField.dispatchEvent(new win.Event('input', { bubbles: true }));
    noteField.value = '并发笔记';
    noteField.dispatchEvent(new win.Event('input', { bubbles: true }));
    await tick(950);
    const both = findNode(map.getAdapter().fromMindElixirData(mei.getData()), chapterId);
    check('two fields commit independently', both && both.summary === '并发摘要' && both.note === '并发笔记',
      both && (both.summary + ' / ' + both.note));
  }

  map.setEditable(false);
  await tick(100);
  check('edit mode off', map.isEditing() === false);

  map.toggleMinimap(true);
  await tick(300);
  check('minimap visible', doc.getElementById('minimap').hidden === false);
  check('minimap toggle reflects state', doc.querySelector('[data-ktm-view="minimap"]').getAttribute('aria-pressed') === 'true');

  map.destroy();
  check('destroy clears map state', !map.getMei());
  check('no jsdom runtime errors', runtimeErrors.length === 0, runtimeErrors[0]);

  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch(err => {
  console.error('HARNESS ERROR:', err);
  process.exit(2);
});
