/*
 * client shell ↔ map iframe sync (jsdom).
 * Covers: kt-mindmap-ready, kt-map-focus (no iframe reload), kt-map-selection,
 * kt-open-resource chapter pass-through, kt-open-chat question pass-through.
 *
 * Run: JSDOM_PATH=/path/to/jsdom node test_ui_map_sync.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/client.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const CLIENT = fs.readFileSync(path.join(PROJ, 'ui/client.js'), 'utf8');
const PREVIEW_TREE = {
  title: '学习科学导论',
  children: [
    { title: '第一章 记忆与注意', level: 1, children: [{ title: '第一节 工作记忆', level: 2, children: [] }] },
    { title: '第二章 练习与迁移', level: 1, children: [{ title: '第一节 提取练习', level: 2, children: [] }] }
  ]
};

let pass = 0;
let fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = (ms) => new Promise(resolve => setTimeout(resolve, ms || 60));

const vc = new VirtualConsole();
const runtimeErrors = [];
vc.on('jsdomError', error => {
  if (/Could not load|not implemented|Not implemented/.test(error.message)) return;
  runtimeErrors.push(error.message);
});

const dom = new JSDOM(HTML, {
  url: 'http://localhost/ui/client.html#mindmap?file=task_demo001&mode=canvas',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole: vc
});
const win = dom.window;
const doc = win.document;
const posts = [];

win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
win.scrollTo = () => {};
win.open = () => null;
win.HTMLElement.prototype.scrollIntoView = function () {};
win.fetch = async (url) => {
  const target = String(url);
  let data = {};
  if (target.includes('/api/file/task_demo001')) data = PREVIEW_TREE;
  else if (target.includes('/api/files')) data = { files: [{ filename: 'task_demo001', task_id: 'demo001', source: 'task', size: 1000, modified: new Date().toISOString(), original_files: ['导论.pdf'], node_count: 128 }] };
  else if (target.includes('/api/stats')) data = { tree_count: 1, node_count: 128 };
  else if (target.includes('/api/tasks')) data = { tasks: [] };
  else if (target.includes('/api/favorites')) data = { keys: [] };
  else if (target.includes('/api/settings')) data = { providers: [], active_id: '', subject: { name: '教育学', exam: '333' }, mineru: { has_token: false }, builtin: { chat: false, chat_name: '', mineru: false }, env: {}, enhance: { label: '标准' } };
  return { ok: true, status: 200, json: async () => data };
};
const reviewCalls = [];
win.ReviewWidget = {
  mount() {}, open() {}, close() {}, ask(q) { reviewCalls.push({ type: 'ask', q }); }, startChapter(c, sc) { reviewCalls.push({ type: 'chapter', c, sc }); },
  setContext() {}, setComposerMeta() {}, setMentions() {}, setPreferences() {},
  setMode() {}, getMode() { return 'probe'; }
};

win.eval(COMMON);
win.eval(CLIENT);
doc.dispatchEvent(new win.Event('DOMContentLoaded'));

(async function run() {
  await tick(300);
  const frame = doc.getElementById('previewFrame');
  check('mindmap iframe exists', !!frame);
  // Give the shell a controllable contentWindow (jsdom's iframe is empty).
  Object.defineProperty(frame, 'contentWindow', {
    configurable: true,
    value: { postMessage(message, origin) { posts.push({ message, origin }); } }
  });
  const beforeSrc = frame.src;

  win.dispatchEvent(new win.MessageEvent('message', {
    data: { type: 'kt-mindmap-ready' },
    origin: win.location.origin
  }));
  await tick(80);

  const railBtn = doc.querySelector('#workspaceRail [data-rail-action="mindmap-chapter"]');
  check('rail chapter button exists', !!railBtn);
  if (railBtn) {
    railBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
    await tick(120);
  }
  const focusPost = posts.find(p => p.message && p.message.type === 'kt-map-focus');
  check('chapter click posts kt-map-focus', !!focusPost, JSON.stringify(posts));
  check('chapter click does not reload iframe', frame.src === beforeSrc, beforeSrc + ' -> ' + frame.src);
  check('chapter click updates shell state', win.KTClient.getState().previewChapter === '第一章 记忆与注意');

  win.dispatchEvent(new win.MessageEvent('message', {
    data: { type: 'kt-map-selection', file: 'task_demo001', chapter: '第二章 练习与迁移' },
    origin: win.location.origin
  }));
  await tick(60);
  check('map selection updates shell chapter', win.KTClient.getState().previewChapter === '第二章 练习与迁移');

  win.dispatchEvent(new win.MessageEvent('message', {
    data: { type: 'kt-open-resource', file: 'task_demo001', mode: 'canvas', chapter: '第一章 记忆与注意' },
    origin: win.location.origin
  }));
  await tick(200);
  check('kt-open-resource passes chapter', win.KTClient.getState().previewChapter === '第一章 记忆与注意');

  win.dispatchEvent(new win.MessageEvent('message', {
    data: { type: 'kt-open-chat', file: 'task_demo001', chapter: '第一章 记忆与注意', question: '解释工作记忆' },
    origin: win.location.origin
  }));
  await tick(200);
  check('kt-open-chat passes question', reviewCalls.some(c => c.type === 'ask' && c.q === '解释工作记忆'), JSON.stringify(reviewCalls));
  check('no jsdom runtime errors', runtimeErrors.length === 0, runtimeErrors[0]);

  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch(err => {
  console.error('HARNESS ERROR:', err);
  process.exit(2);
});
