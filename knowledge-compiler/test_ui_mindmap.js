/*
 * tree_mindmap.html 端到端测试（jsdom）—— 沉浸式导图页（信息页已独立为 overview.html）
 * 覆盖：深链 ?chapter= / 下钻与面包屑 / 章节侧栏（含导学展开）/ 视图与导出菜单 /
 *      面板互斥 / 对话复习悬浮组件（先选复习方向再对话 / 学习⇄追问模式开关 / 旧会话「接着上次」询问） / 长标题折行 / 流程图缩放画布
 *
 * 依赖：npm i jsdom（或设 JSDOM_PATH）
 * 运行：node knowledge-compiler/test_ui_mindmap.js [tree.json]
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/tree_mindmap.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const WIDGET = fs.readFileSync(path.join(PROJ, 'ui/review_widget.js'), 'utf8');

let pass = 0, fail = 0;
const jsdomErrors = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = (ms) => new Promise(r => setTimeout(r, ms || 40));

// ── 测试树：单册包装（教心 → 第一章/第二章），带导学与考点 ──
function makeTree() {
  return {
    title: '教心', children: [{
      title: '教心', level: 0, children: [
        {
          title: '第一章 心理发展', level: 1, guide: '本章概述心理发展的基本框架。\n重点关注阶段划分与关键期。',
          prerequisites: [],
          exam_points: [{ point: '心理发展的阶段', frequency: '高频', type: '简答' }],
          children: [
            {
              title: '第一节 发展规律', level: 2, summary: '规律概述。',
              // 真实数据形态：正文里内嵌 HTML 表格（MinerU 原生输出），tables 字段混合两种格式
              content: '要点说明。\n<table><tr><td>对比</td><td>甲</td></tr><tr><td>乙</td><td>丙</td></tr></table>\n后续文字。',
              tables: [
                '<table><tr><td>表2</td><td>丁</td></tr></table>',
                '| 人物 | 观点 |\n|------|------|\n| 孔子 | 仁 |'
              ],
              children: [], exam_points: []
            },
            { title: '第二节 认知发展', level: 2, children: [], exam_points: [] }
          ]
        },
        {
          title: '第二章 学习理论', level: 1, guide: '本章聚焦学习理论流派。',
          prerequisites: ['第一章 心理发展'],
          children: [
            { title: '第三节 行为主义', level: 2, children: [] },
            { title: '第四节 认知理论', level: 2, children: [] }
          ]
        }
      ]
    }]
  };
}
const TREE = makeTree();

const vc = new VirtualConsole();
vc.on('jsdomError', e => {
  if (/Could not load|not implemented|Not implemented/.test(e.message)) return;
  jsdomErrors.push(e.message);
});

const inlineMatch = HTML.match(/<script>([\s\S]*?)<\/script>/);
check('内联脚本可提取', !!inlineMatch);
const HTML_STRIPPED = HTML.replace(inlineMatch[0], '');

const dom = new JSDOM(HTML_STRIPPED, {
  url: 'http://localhost/ui/tree_mindmap.html?file=test_tree.json&chapter=' + encodeURIComponent('第一章 心理发展'),
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(window) {
    // jsdom 不总是把 Node 的 TextDecoder 暴露到页面；真实浏览器原生支持。
    if (typeof TextDecoder !== 'undefined') window.TextDecoder = TextDecoder;
    if (typeof TextEncoder !== 'undefined') window.TextEncoder = TextEncoder;
  },
});
const win = dom.window, doc = win.document;

// 注入 theme.css：复现真实页面的全局样式冲突（textarea{min-height:100px}、button{padding} 等）
const THEME = fs.readFileSync(path.join(PROJ, 'ui/theme.css'), 'utf8');
const themeStyle = doc.createElement('style');
themeStyle.textContent = THEME;
doc.head.appendChild(themeStyle);

// ── stubs ──
// 假视图组：tree_mindmap 的自定义平移/缩放先直接改 group、再 dispatch 记账。
// 双翼布局下原生 roam 已关闭，因此左键无论在内容盒内外都由 panScene 统一接管。
const fakeGroup = {
  x: 0, y: 0, scaleX: 1, scaleY: 1,
  dirty() {}, updateTransform() {},
  getBoundingRect() { return { x: -100, y: -50, width: 200, height: 100 }; },
  transform: [1, 0, 0, 1, 0, 0]
};
const fakeSeriesModel = { id: 'series-0' };
const fakeView = {
  __model: fakeSeriesModel, group: fakeGroup,
  _updateNodeAndLinkScale() {}
};
const zrStub = {
  __ktBlankDismissBound: false,
  handlers: {},
  on(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); },
  emit(name, event) { (this.handlers[name] || []).forEach(fn => fn(event)); }
};
const chartStub = {
  lastOption: null, calls: 0, actions: [],
  setOption(o, notMerge) {
    this.lastOption = o; this.calls++;
    // 模拟 echarts 全量重出图：视图变换重置回 option 默认（x/y=0, scale=1）
    if (notMerge === true) { fakeGroup.x = 0; fakeGroup.y = 0; fakeGroup.scaleX = 1; fakeGroup.scaleY = 1; }
  },
  dispatchAction(a) { this.actions.push(a); },
  on() {}, resize() {},
  getZr() { return zrStub; },
  getWidth() { return 1200; }, getHeight() { return 800; },
  getDataURL() { return 'data:image/png;base64,AAAA'; },
  getModel() { return { getSeriesByIndex: () => fakeSeriesModel }; },
  getViewOfSeriesModel() { return fakeView; }
};
win.echarts = { init: () => chartStub };
function seriesRootLabelBg(series) {
  var d = series && series.data && series.data[0];
  return d && d.label && d.label.backgroundColor;
}
win.mermaid = { initialize() {}, render: async () => ({ svg: '<svg data-test="1"></svg>' }) };
win.alert = m => console.log('  [alert]', m);

let printedHTML = null;
win.open = function () {
  return {
    document: { open() {}, write(h) { printedHTML = h; }, close() {} },
    focus() {}, print() {}, onload: null
  };
};

const SNAP_START = {
  session_id: 'rw_1', file_key: 'test_tree.json', tree_title: '教心', done: false,
  current: { unit_id: 'u1', title: '第一章 心理发展', group: '', path: [], reference_count: 2, turn_count: 0, hint_count: 0, last_level: null, awaiting: 'answer', exam_points: [], h4_used: false },
  counters: { turn: 0, turn_max: 6, hint: 0, hint_max: 2 },
  units: [{ unit_id: 'u1', title: '第一章 心理发展', group: '', status: 'pending', stuck_level: '', turns: 0, hints: 0 }],
  messages: [{ role: 'guide', text: '先不翻书——说说「**第一章 心理发展**」，现在能想起什么就说什么，碎片也算。', kind: 'question' }],
  counts: { passed: 0, half: 0, failed: 0 }, report: null, degraded: true
};
const SNAP_AFTER = Object.assign({}, SNAP_START, {
  current: Object.assign({}, SNAP_START.current, { turn_count: 1 }),
  messages: SNAP_START.messages.concat([
    { role: 'learner', text: '第一节讲发展规律', kind: 'text' },
    { role: 'guide', text: '你说到**重点**了，再补一块？', kind: 'question' },
  ]),
});
const SNAP_AFTER2 = Object.assign({}, SNAP_AFTER, {
  messages: SNAP_AFTER.messages.concat([
    { role: 'learner', text: '再补一块', kind: 'text' },
    { role: 'guide', text: '行，这样就顺了。', kind: 'question' },
  ]),
});
const startedBodies = [];
const answerBodies = [];
const learnBodies = [];
let learn404 = false;
let staleStart = false;   // /api/review/start 返回"3 小时前"的未结束会话（测「接着上次/换方向」）
let _staleSeq = 0;
// NDJSON 流式 mock：逐字 delta → 最终 snapshot
function streamMock(events) {
  var i = 0;
  return {
    ok: true, status: 200,
    body: { getReader: function () { return { read: async function () {
      if (i >= events.length) return { done: true };
      var line = JSON.stringify(events[i++]) + '\n';
      return { done: false, value: new TextEncoder().encode(line) };
    } }; } },
    json: async function () { return {}; }
  };
}
win.fetch = async function (url, opts) {
  url = String(url);
  const j = (o) => ({ ok: true, status: 200, json: async () => o });
  if (url.includes('/api/review/units')) {
    return j({
      units: [
        { unit_id: 'u1', title: '第一节 发展规律', group: '第一章 心理发展', reference_count: 3, has_exam: true, high_freq: true },
        { unit_id: 'u2', title: '第二节 认知发展', group: '第一章 心理发展', reference_count: 2, has_exam: false, high_freq: false },
        { unit_id: 'u3', title: '第三节 行为主义', group: '第二章 学习理论', reference_count: 2, has_exam: false, high_freq: false },
        { unit_id: 'u4', title: '第四节 认知理论', group: '第二章 学习理论', reference_count: 2, has_exam: false, high_freq: false }
      ],
      total_candidates: 4, truncated: 0, retest_count: 0, weak_count: 0,
      groups: [{ name: '第一章 心理发展', count: 2 }, { name: '第二章 学习理论', count: 2 }],
    });
  }
  if (url.includes('/api/review/start')) {
    try { startedBodies.push(JSON.parse(opts.body)); } catch (e) {}
    if (staleStart) {
      _staleSeq++;
      const stale = JSON.parse(JSON.stringify(SNAP_START));
      stale.session_id = 'rw_stale_' + _staleSeq;
      const pastTs = (Date.now() / 1000) - 3 * 3600;
      stale.messages.forEach(function (m) { m.ts = pastTs; });   // 服务端每条消息都带 ts
      return j(stale);
    }
    return j(SNAP_START);
  }
  if (url.includes('/api/review/answer')) {
    var idx = answerBodies.length;
    try { answerBodies.push(JSON.parse(opts.body)); } catch (e) {}
    return streamMock([
      { t: 'd', text: '你说到' },
      { t: 'd', text: '**重点**了，再补一块？' },
      { t: 'snap', data: idx === 0 ? SNAP_AFTER : SNAP_AFTER2 },
    ]);
  }
  if (url.includes('/api/review/learn')) {
    if (learn404) {
      return { ok: false, status: 404, json: async function () { return { detail: 'Not Found' }; } };
    }
    try { learnBodies.push(JSON.parse(opts.body)); } catch (e) {}
    return streamMock([
      { t: 'd', text: '颜之推主张' },
      { t: 'd', text: '**德艺周厚**。' },
      { t: 'snap', data: { reply: '颜之推主张**德艺周厚**，重视家庭教育。',
                           sources: ['教心 › 第一章 心理发展 › 第一节 发展规律'],
                           degraded: false } },
    ]);
  }
  if (url.includes('/api/review/progress')) return j({ retest: [], weak: [], unit_count: 0, sessions: [] });
  if (url.includes('/api/file/') || url.includes('/api/result/')) return j(TREE);
  return { ok: false, status: 404, json: async () => ({ error: 'nf' }) };
};

function runScript(code) {
  const s = doc.createElement('script');
  s.textContent = code;
  doc.head.appendChild(s);
}
runScript(COMMON);
runScript(WIDGET);
runScript(inlineMatch[1]);

(async () => {
  await win.initChart();
  await tick(120);

  console.log('[A] 沉浸式导图页：无信息页残留 + 深链');
  check('默认排列方向为全部从左到右',
    doc.getElementById('layoutSelect').value === 'LR_ALL'
      && win.eval('currentLayout') === 'LR_ALL',
    'select=' + doc.getElementById('layoutSelect').value
      + ' current=' + win.eval('currentLayout'));
  check('默认缩放为 100%',
    doc.getElementById('zoomLabel').textContent === '100%'
      && doc.getElementById('zoomRange').value === '100'
      && win.eval('sceneZoom') === 1,
    'label=' + doc.getElementById('zoomLabel').textContent
      + ' range=' + doc.getElementById('zoomRange').value
      + ' sceneZoom=' + win.eval('sceneZoom'));
  check('页面没有总览容器', !doc.getElementById('overviewPage'));
  check('页面没有导学面板', !doc.getElementById('guidePanel'));
  check('深链 ?chapter= 已定位并显示面包屑',
    doc.getElementById('breadcrumb').classList.contains('show')
    && doc.getElementById('breadcrumb').textContent.includes('心理发展'),
    doc.getElementById('breadcrumb').textContent);
  check('深链后图表聚焦为单根子树', chartStub.lastOption.series[0].data.length === 1);
  check('导出标题为当前章', win.currentExportTitle() === '第一章 心理发展');
  check('总览链接指向信息页',
    doc.getElementById('btnOverview').href.includes('/ui/overview.html?file=test_tree.json'),
    doc.getElementById('btnOverview').href);
  check('stats 统计存在', doc.getElementById('stats').textContent.includes('知识点'));

  console.log('[B] 章节侧栏（含导学展开）');
  win.toggleChapterNav();
  check('侧栏打开', doc.getElementById('chapterSidebar').classList.contains('open'));
  check('侧栏两条章节', doc.querySelectorAll('#chapterList .cs-item').length === 2);
  check('顺序徽章 1/2',
    [...doc.querySelectorAll('#chapterList .cs-num')].map(e => e.textContent.trim()).join(',') === '1,2');
  check('考点数展示', doc.getElementById('chapterList').querySelector('.cs-exam')
    && doc.getElementById('chapterList').querySelector('.cs-exam').textContent.includes('1')
    && doc.getElementById('chapterList').querySelector('.cs-exam svg.ic') !== null);
  check('活跃章节高亮', doc.querySelector('#chapterList .cs-item.active') !== null);
  var toggleBtn = doc.querySelector('#chapterList .cs-toggle');
  check('有导学的章节带展开按钮', !!toggleBtn);
  var fakeEvent = { stopPropagation() {} };
  win.toggleChapterGuide(fakeEvent, toggleBtn);
  check('导学展开并显示内容',
    toggleBtn.parentNode.classList.contains('expanded')
    && toggleBtn.parentNode.textContent.includes('心理发展的基本框架'),
    toggleBtn.parentNode.textContent.slice(0, 60));
  win.toggleChapterGuide(fakeEvent, toggleBtn);
  check('导学可收起', !toggleBtn.parentNode.classList.contains('expanded'));

  console.log('[C] 面板互斥（打开一个自动收起其余）');
  win.toggleSearch();
  check('搜索面板打开', doc.getElementById('searchPanel').classList.contains('collapsed') === false);
  win.toggleChapterNav();                       // 打开章节 → 搜索应收起
  check('打开章节后搜索自动收起', doc.getElementById('searchPanel').classList.contains('collapsed'));
  // 打开节点详情 → 章节侧栏应收起
  win.showNodeInfo({ name: '第一节 发展规律', level: 2, summary: '规律概述。', children: [] });
  check('节点详情面板打开', doc.getElementById('infoPanel').classList.contains('show'));
  check('打开详情后章节侧栏自动收起', !doc.getElementById('chapterSidebar').classList.contains('open'));
  // 打开对话组件 → 详情面板应收起
  win.ReviewWidget.open();
  await tick(80);
  check('对话面板打开', doc.getElementById('rwPanel').classList.contains('open'));
  check('打开对话后节点详情自动收起', !doc.getElementById('infoPanel').classList.contains('show'));
  win.ReviewWidget.close();

  console.log('[C2] 画布空白点击收起节点详情');
  win.showNodeInfo({ name: '空白点击测试', level: 2, summary: '测试。', children: [] });
  zrStub.emit('mousedown', { offsetX: 300, offsetY: 200, target: null });
  zrStub.emit('click', { offsetX: 300, offsetY: 200, target: null });
  check('点击画布空白收起详情栏', !doc.getElementById('infoPanel').classList.contains('show'));
  win.showNodeInfo({ name: '节点点击测试', level: 2, summary: '测试。', children: [] });
  zrStub.emit('mousedown', { offsetX: 320, offsetY: 210, target: { type: 'node' } });
  zrStub.emit('click', { offsetX: 320, offsetY: 210, target: { type: 'node' } });
  check('点击节点不会收起详情栏', doc.getElementById('infoPanel').classList.contains('show'));
  win.showNodeInfo({ name: '拖动画布测试', level: 2, summary: '测试。', children: [] });
  zrStub.emit('mousedown', { offsetX: 300, offsetY: 200, target: null });
  zrStub.emit('click', { offsetX: 340, offsetY: 230, target: null });
  check('拖动画布松手不会误收起详情栏', doc.getElementById('infoPanel').classList.contains('show'));
  win.closePanel();

  console.log('[C3] 亮暗主题即时同步（含嵌入 iframe 广播）');
  var themeCallsBefore = chartStub.calls;
  win.dispatchEvent(new win.MessageEvent('message', {
    origin: win.location.origin,
    data: { type: 'kt-theme', theme: 'dark' }
  }));
  var darkSeries = chartStub.lastOption.series[0];
  check('父级广播后无需刷新即切到暗色', doc.documentElement.getAttribute('data-theme') === 'dark');
  check('暗色下导图节点样式立即重绘', chartStub.calls > themeCallsBefore
    && seriesRootLabelBg(darkSeries) === '#e08264',
    'calls=' + chartStub.calls + ' pill=' + seriesRootLabelBg(darkSeries));
  check('暗色下缩放条伪元素样式立即刷新',
    (doc.getElementById('darkRangeStyle') || {}).textContent.indexOf('#242018') >= 0
      && doc.querySelector('.zoom-control').style.background === 'rgb(32, 32, 30)',
    (doc.getElementById('darkRangeStyle') || {}).textContent);
  var darkCalls = chartStub.calls;
  win.dispatchEvent(new win.MessageEvent('message', {
    origin: win.location.origin,
    data: { type: 'kt-theme', theme: 'light' }
  }));
  var lightSeries = chartStub.lastOption.series[0];
  check('父级广播后无需刷新即切回亮色', !doc.documentElement.getAttribute('data-theme'));
  check('亮色下导图节点样式立即重绘', chartStub.calls > darkCalls
    && seriesRootLabelBg(lightSeries) === '#d97757',
    'calls=' + chartStub.calls + ' pill=' + seriesRootLabelBg(lightSeries));
  check('亮色下缩放条伪元素样式立即刷新',
    (doc.getElementById('darkRangeStyle') || {}).textContent.indexOf('#ffffff') >= 0
      && doc.querySelector('.zoom-control').style.background === 'rgb(247, 247, 245)',
    (doc.getElementById('darkRangeStyle') || {}).textContent);
  var localCalls = chartStub.calls;
  win.handleThemeToggle();
  check('独立页面主题按钮同样即时重绘', doc.documentElement.getAttribute('data-theme') === 'dark'
    && chartStub.calls > localCalls
    && (doc.getElementById('darkRangeStyle') || {}).textContent.indexOf('#242018') >= 0);
  win.handleThemeToggle();
  check('独立页面切回亮色同样即时重绘', !doc.documentElement.getAttribute('data-theme')
    && seriesRootLabelBg(chartStub.lastOption.series[0]) === '#d97757');

  console.log('[D] 对话复习组件：先选方向 → AI 先提问 + 快捷选项');
  win.ReviewWidget.open();
  await tick(80);
  check('上来先出「复习方向」列表（未自动开始）',
    startedBodies.length === 0
    && doc.getElementById('rwMsgs').textContent.includes('选择复习方向'),
    'starts=' + startedBodies.length + ' | ' + doc.getElementById('rwMsgs').textContent.slice(0, 60));
  check('方向列表来自导图：全书 + 按章节分组',
    ['全书混合复习', '第一章 心理发展', '第二章 学习理论'].every(function (t) {
      return doc.getElementById('rwMsgs').textContent.includes(t);
    }), doc.getElementById('rwMsgs').textContent.slice(0, 120));
  check('当前浏览章节带「当前」标记',
    doc.querySelector('#rwMsgs .rw-copt-badge') !== null
    && doc.querySelector('#rwMsgs .rw-copt-badge').textContent === '当前');
  check('方向行带复习单元数量与高频标记',
    doc.getElementById('rwMsgs').textContent.includes('2 个复习单元')
    && doc.getElementById('rwMsgs').textContent.includes('高频 1'),
    doc.getElementById('rwMsgs').textContent.slice(0, 200));
  var chOpt = [].slice.call(doc.querySelectorAll('#rwMsgs .rw-copt')).filter(function (b) {
    return b.textContent.indexOf('第一章 心理发展') >= 0;
  })[0];
  check('可选章节按钮存在', !!chOpt);
  chOpt.click();
  await tick(120);
  check('选定后方向列表退场、对话开始',
    !doc.getElementById('rwMsgs').textContent.includes('选择复习方向'));
  check('AI 开场自然提问（不泄条目）',
    doc.getElementById('rwMsgs').textContent.includes('第一章 心理发展')
    && doc.getElementById('rwMsgs').textContent.includes('想起'),
    doc.getElementById('rwMsgs').textContent.slice(0, 50));
  check('对话渲染 Markdown 加粗（知识点高光）',
    doc.getElementById('rwMsgs').innerHTML.includes('<strong>第一章 心理发展</strong>'),
    doc.getElementById('rwMsgs').innerHTML.slice(0, 120));
  check('无用户消息（不是用户发问）', doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 0);
  check('消息带 AI 头像', doc.querySelectorAll('#rwMsgs .rw-avatar.ai').length >= 1,
    String(doc.querySelectorAll('#rwMsgs .rw-avatar').length));
  check('思考中为三点跳动指示器',
    doc.getElementById('rwTyping').querySelectorAll('.rw-dot').length === 3
    && doc.getElementById('rwTyping').textContent.includes('思考中'),
    doc.getElementById('rwTyping').innerHTML);
  check('思考中挂在 AI 头像右侧',
    doc.getElementById('rwTyping').children[0].classList.contains('rw-avatar')
    && doc.getElementById('rwTyping').querySelector('.rw-avatar.ai') !== null
    && doc.getElementById('rwTyping').querySelector('.rw-tdot') !== null,
    doc.getElementById('rwTyping').innerHTML);
  check('思考行在消息流内（气泡应出现的位置，而非页面底部）',
    doc.getElementById('rwMsgs').contains(doc.getElementById('rwTyping')),
    'typing parent=' + (doc.getElementById('rwTyping').parentNode || {}).id);
  check('快捷选项含「想不起来/提示/跳过/结束」',
    ['想不起来', '给点提示', '跳过这个点', '结束本轮'].every(function (t) {
      return doc.getElementById('rwQuick').textContent.includes(t);
    }), doc.getElementById('rwQuick').textContent);
  check('开始请求带所选章节与 scope',
    startedBodies.length === 1 && startedBodies[0].chapter === '第一章 心理发展'
    && startedBodies[0].scope === 'all',
    JSON.stringify(startedBodies[0] || {}));
  check('进度记账不展示（回合/提示/层级/进度 chips 已移除）',
    !doc.getElementById('rwMeta').textContent.includes('回合')
    && !doc.getElementById('rwMeta').textContent.includes('提示 ')
    && !doc.getElementById('rwMeta').textContent.includes('进度')
    && !doc.getElementById('rwMeta').textContent.includes('L1'),
    doc.getElementById('rwMeta').textContent);

  console.log('[D3] 流式作答：用户消息先上屏 → AI 逐字 → 快照收口');
  doc.getElementById('rwInput').value = '第一节讲发展规律';
  doc.getElementById('rwSend').click();
  check('用户消息立即上屏（不等 AI 回答）',
    doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 1
    && doc.getElementById('rwMsgs').textContent.includes('第一节讲发展规律'),
    doc.getElementById('rwMsgs').textContent.slice(-60));
  await tick(120);
  check('作答请求走流式', answerBodies.length && answerBodies[0].stream === true,
    JSON.stringify(answerBodies[0] || {}));
  check('流式文本渲染进对话（含加粗）',
    doc.getElementById('rwMsgs').innerHTML.includes('<strong>重点</strong>'),
    doc.getElementById('rwMsgs').innerHTML.slice(-160));
  check('快照收口后气泡归位', !doc.getElementById('rwStream')
    && doc.getElementById('rwMsgs').textContent.includes('再补一块'),
    doc.getElementById('rwMsgs').textContent.slice(-80));
  check('用户消息带用户头像', doc.querySelectorAll('#rwMsgs .rw-avatar.user').length >= 1,
    String(doc.querySelectorAll('#rwMsgs .rw-avatar.user').length));
  check('AI 头像居左、用户头像居右（DOM 序）',
    doc.querySelector('#rwMsgs .rw-msg:not(.learner)').children[0].classList.contains('rw-avatar')
    && doc.querySelector('#rwMsgs .rw-msg.learner').children[1].classList.contains('rw-avatar'),
    'avatar sides');

  console.log('[D3b] 用户上滑阅读时暂停自动贴底');
  var msgsEl = doc.getElementById('rwMsgs');
  var fakeScrollHeight = 900;
  Object.defineProperty(msgsEl, 'scrollHeight', { configurable: true, get: function () { return fakeScrollHeight; } });
  Object.defineProperty(msgsEl, 'clientHeight', { configurable: true, get: function () { return 300; } });
  msgsEl.scrollTop = 600;
  msgsEl.dispatchEvent(new win.Event('scroll'));
  check('贴底时隐藏回到底部按钮', !doc.getElementById('rwScrollBottom').classList.contains('is-visible'));
  msgsEl.scrollTop = 120;
  fakeScrollHeight = 1100;
  msgsEl.dispatchEvent(new win.Event('scroll'));
  check('用户上滑后显示回到底部按钮',
    doc.getElementById('rwScrollBottom').classList.contains('is-visible')
      && doc.getElementById('rwScrollBottom').getAttribute('aria-hidden') === 'false');
  doc.getElementById('rwInput').value = '继续讲';
  doc.getElementById('rwSend').click();
  await tick(120);
  check('模型继续输出时保持当前阅读位置', msgsEl.scrollTop === 120,
    'scrollTop=' + msgsEl.scrollTop);
  check('上滑期间继续显示回到底部按钮',
    doc.getElementById('rwScrollBottom').classList.contains('is-visible'));
  doc.getElementById('rwScrollBottom').click();
  await tick(80);
  check('点击箭头回到底部并恢复自动跟随',
    msgsEl.scrollTop === fakeScrollHeight
      && !doc.getElementById('rwScrollBottom').classList.contains('is-visible'),
    'scrollTop=' + msgsEl.scrollTop + ' height=' + fakeScrollHeight);
  delete msgsEl.scrollHeight;
  delete msgsEl.clientHeight;
  win.ReviewWidget.close();
  check('可收起为迷你输入框（走 class 过渡而非硬切）',
    !doc.getElementById('rwPanel').classList.contains('open')
    && !doc.getElementById('rwMini').classList.contains('hidden'));
  check('迷你框内部无提示文字', !doc.getElementById('rwMiniInput').getAttribute('placeholder'),
    String(doc.getElementById('rwMiniInput').getAttribute('placeholder')));
  check('迷你输入框不被 theme 全局 textarea 撑爆',
    win.getComputedStyle(doc.getElementById('rwMiniInput')).minHeight !== '100px',
    'minHeight=' + win.getComputedStyle(doc.getElementById('rwMiniInput')).minHeight);
  check('上箭头图标存在且按钮已重置全局 button padding',
    doc.querySelector('#rwMiniUp svg') !== null
    && win.getComputedStyle(doc.getElementById('rwMiniUp')).paddingLeft === '0px',
    'padL=' + win.getComputedStyle(doc.getElementById('rwMiniUp')).paddingLeft);

  console.log('[D4] 迷你输入框：上箭头展开 + 发送即展开并发出');
  doc.getElementById('rwMiniUp').click();
  check('点上箭头向上展开完整对话',
    doc.getElementById('rwPanel').classList.contains('open')
    && doc.getElementById('rwMini').classList.contains('hidden'));
  win.ReviewWidget.close();
  doc.getElementById('rwMiniInput').value = '再补一块';
  doc.getElementById('rwMiniSend').click();
  check('迷你框点发送 → 展开并立即上屏用户消息',
    doc.getElementById('rwPanel').classList.contains('open')
    && doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 3
    && doc.getElementById('rwMsgs').textContent.includes('再补一块'),
    doc.getElementById('rwMsgs').textContent.slice(-80));
  await tick(120);
  check('迷你框发送也走流式作答', answerBodies.length === 3 && answerBodies[2].text === '再补一块',
    JSON.stringify(answerBodies[2] || {}));
  win.ReviewWidget.close();

  console.log('[D5] 模式开关：学习 / 追问');
  win.ReviewWidget.open();
  await tick(80);
  var modeLearn = doc.getElementById('rwModeLearn');
  var modeProbe = doc.getElementById('rwModeProbe');
  check('模式开关存在（学习 / 追问）', !!modeLearn && !!modeProbe
    && modeLearn.textContent === '学习' && modeProbe.textContent === '追问');
  check('默认追问模式：回到进行中的对话',
    modeProbe.classList.contains('on')
    && doc.getElementById('rwMsgs').textContent.includes('第一章 心理发展'),
    doc.getElementById('rwMsgs').textContent.slice(0, 60));
  modeLearn.click();
  await tick(60);
  check('切到学习模式：显示学习引导',
    modeLearn.classList.contains('on') && modeLearn.getAttribute('aria-pressed') === 'true'
    && !modeProbe.classList.contains('on')
    && doc.getElementById('rwMsgs').textContent.includes('直接问我'),
    doc.getElementById('rwMsgs').textContent.slice(0, 80));
  check('学习模式带快捷问题按钮',
    doc.getElementById('rwQuick').textContent.includes('这章讲什么')
    && doc.getElementById('rwQuick').textContent.includes('重点 / 难点'));
  doc.getElementById('rwInput').value = '发展规律讲了什么？';
  doc.getElementById('rwSend').click();
  check('学习提问立即上屏',
    doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 1
    && doc.getElementById('rwMsgs').textContent.includes('发展规律讲了什么？'),
    doc.getElementById('rwMsgs').textContent.slice(-60));
  await tick(120);
  check('学习请求带 file / chapter / history',
    learnBodies.length === 1 && learnBodies[0].file === 'test_tree.json'
    && learnBodies[0].chapter === '第一章 心理发展'
    && Array.isArray(learnBodies[0].history) && learnBodies[0].history.length >= 1,
    JSON.stringify(learnBodies[0] || {}).slice(0, 180));
  check('学习回答渲染并附出处',
    doc.getElementById('rwMsgs').innerHTML.includes('<strong>德艺周厚</strong>')
    && !!doc.querySelector('#rwMsgs .rw-source-pill')
    && doc.getElementById('rwMsgs').textContent.includes('第一节 发展规律'),
    doc.getElementById('rwMsgs').textContent.slice(-120));
  modeProbe.click();
  await tick(60);
  check('切回追问模式：复习会话原样保留',
    modeProbe.classList.contains('on')
    && !doc.getElementById('rwMsgs').textContent.includes('直接问我')
    && doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 2,
    'learners=' + doc.querySelectorAll('#rwMsgs .rw-msg.learner').length);
  modeLearn.click();
  await tick(60);
  check('学习会话不受追问切换影响', 
    doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 1
    && !!doc.querySelector('#rwMsgs .rw-source-pill'));
  modeProbe.click();
  win.ReviewWidget.close();

  console.log('[D6] 旧版服务端（404）→ 报错指向重启，而不是笼统的"连接中断"');
  learn404 = true;
  win.ReviewWidget.open();
  await tick(60);
  if (!doc.getElementById('rwModeLearn').classList.contains('on')) {
    doc.getElementById('rwModeLearn').click();
    await tick(40);
  }
  doc.getElementById('rwInput').value = '随便问问';
  doc.getElementById('rwSend').click();
  await tick(120);
  var failTail = doc.getElementById('rwMsgs').textContent.slice(-200);
  check('404 报错说明真实原因（旧版本 → 重启服务）',
    failTail.includes('404') && failTail.includes('重启') && !failTail.includes('连接中断'),
    failTail);
  learn404 = false;
  win.ReviewWidget.close();

  console.log('[D7] 旧会话未结束：打开先问「接着上次 / 换方向」');
  staleStart = true;
  win.ReviewWidget.startChapter('第一章 心理发展');
  await tick(120);
  win.ReviewWidget.close();
  staleStart = false;
  win.ReviewWidget.open();
  await tick(60);
  check('打开先出「接着上次 / 换方向」询问',
    doc.getElementById('rwMsgs').textContent.includes('接着上次')
    && doc.getElementById('rwMsgs').textContent.includes('换方向')
    && doc.getElementById('rwMsgs').textContent.includes('上次进行到'),
    doc.getElementById('rwMsgs').textContent.slice(0, 90));
  check('询问里说明停了多久（3 小时前）',
    doc.getElementById('rwMsgs').textContent.includes('3 小时前'),
    doc.getElementById('rwMsgs').textContent.slice(0, 120));
  var rBtn = [].slice.call(doc.querySelectorAll('#rwMsgs .rw-copt'))
    .filter(function (b) { return b.textContent.indexOf('接着上次') >= 0; })[0];
  check('「接着上次」按钮存在', !!rBtn);
  rBtn.click();
  await tick(60);
  check('点「接着上次」回到原对话',
    doc.getElementById('rwMsgs').textContent.includes('想起'),
    doc.getElementById('rwMsgs').textContent.slice(0, 60));
  win.ReviewWidget.close();
  win.ReviewWidget.open();
  await tick(60);
  check('已确认过的会话不再重复询问',
    !doc.getElementById('rwMsgs').textContent.includes('接着上次')
    && doc.getElementById('rwMsgs').textContent.includes('想起'));
  win.ReviewWidget.close();
  staleStart = true;
  win.ReviewWidget.startChapter('第二章 学习理论');
  await tick(120);
  win.ReviewWidget.close();
  staleStart = false;
  win.ReviewWidget.open();
  await tick(60);
  var cBtn = [].slice.call(doc.querySelectorAll('#rwMsgs .rw-copt'))
    .filter(function (b) { return b.textContent.indexOf('换方向') >= 0; })[0];
  check('可点「换方向」', !!cBtn);
  cBtn.click();
  await tick(120);
  check('换方向 → 回到复习方向列表',
    doc.getElementById('rwMsgs').textContent.includes('选择复习方向')
    && !doc.getElementById('rwMsgs').textContent.includes('接着上次'),
    doc.getElementById('rwMsgs').textContent.slice(0, 60));
  win.ReviewWidget.close();

  console.log('[E] 视图菜单');
  win.toggleMenu({ stopPropagation() {} }, 'viewMenu');
  check('视图菜单打开', doc.getElementById('viewMenu').classList.contains('open'));
  check('菜单含布局/层级/适应/展开收起',
    doc.getElementById('viewMenu').textContent.includes('排列方向')
    && doc.getElementById('viewMenu').textContent.includes('全部展开'));
  var callsBefore = chartStub.calls;
  win.onExpandChange('3');
  check('切换展开层级触发重渲染', chartStub.calls > callsBefore, `${callsBefore} -> ${chartStub.calls}`);
  var sOpt = chartStub.lastOption.series[0];
  check('画布按内容铺开为像素尺寸（防标签重叠）',
    typeof sOpt.width === 'number' && typeof sOpt.height === 'number'
    && sOpt.width > 0 && sOpt.height > 0 && sOpt.right === undefined,
    JSON.stringify({ w: sOpt.width, h: sOpt.height, right: sOpt.right }));
  var mFew = win.measureTreeBox({ name: '根', children: [
    { name: '叶一', children: [] }, { name: '叶二', children: [] }] }, true);
  var manyLeaves = [];
  for (var li = 0; li < 10; li++) manyLeaves.push({ name: '知识点一 中央官学的教师与学生', children: [] });
  var mMany = win.measureTreeBox({ name: '根', children: manyLeaves }, true);
  check('画布高度随叶子数与标签行数铺开、宽度随最长标签铺开',
    mMany.H > mFew.H * 3 && mMany.W >= 200 && mMany.H > mFew.H,
    JSON.stringify({ few: mFew, many: mMany }));

  // 行打包：长标题只在本地多占行；同一列的兄弟行块按实际高度依次排开。
  var longName = '第一节 隋唐时期的文教政策和学校教育制度的发展与改革';
  var shortName = '知识导图';
  var packTree = { name: '根', children: [
    { name: shortName, children: [] },
    { name: longName, children: [] },
    { name: '第三章 儒学独尊', children: [
      { name: '甲', children: [] }, { name: '乙', children: [] }] }] };
  var packH = win.packTreeRows(packTree, win.visibleDataChildren, function (n) {
    return win.treeLabelBlockHeight(n.name, n.pillKind);
  });
  var shortH = win.treeLabelBlockHeight(shortName, '');
  var longH = win.treeLabelBlockHeight(longName, '');
  check('长标题在本地多占行（不拉高整棵树的行距）',
    longH > shortH && packTree.children[1].__ktRowBlock === longH
      && Math.abs(packH - (shortH + longH + packTree.children[2].__ktRowBlock
        + win.TREE_ROW_GAP * 2)) <= 0.01,
    JSON.stringify({ shortH: shortH, longH: longH, total: packH }));
  var rowNodes = packTree.children;
  check('同一列行块互不重叠且保留行距',
    Math.abs(rowNodes[0].__ktRowY - rowNodes[1].__ktRowY)
      >= (rowNodes[0].__ktRowBlock + rowNodes[1].__ktRowBlock) / 2 + win.TREE_ROW_GAP - 0.01
      && Math.abs(rowNodes[1].__ktRowY - rowNodes[2].__ktRowY)
      >= (rowNodes[1].__ktRowBlock + rowNodes[2].__ktRowBlock) / 2 + win.TREE_ROW_GAP - 0.01,
    JSON.stringify(rowNodes.map(function (n) {
      return { y: n.__ktRowY, h: n.__ktRowBlock };
    })));
  check('父节点子树块 = 子块高度和 + 行距',
    Math.abs(packTree.children[2].__ktRowBlock
      - (packTree.children[2].children[0].__ktRowBlock * 2 + win.TREE_ROW_GAP)) <= 0.01,
    String(packTree.children[2].__ktRowBlock));

  var mSpread = win.measureTreeBox(packTree, true);
  var mCollapsed = win.measureTreeBox({ name: '根', children: [
    { name: '支1', collapsed: true, children: [{ name: '甲', children: [] }, { name: '乙', children: [] }] },
    { name: '支2', children: [{ name: '丙', children: [] }] }] }, true);
  check('收起的子树不占画布', mCollapsed.H < mSpread.H && mCollapsed.H > 0,
    JSON.stringify({ collapsed: mCollapsed, spread: mSpread }));
  var layoutSelect = doc.getElementById('layoutSelect');
  layoutSelect.value = 'LR';
  win.onLayoutChange(layoutSelect.value);
  var wingOpts = chartStub.lastOption.series;
  check('单册包装根节点提升为平衡双翼', wingOpts.length === 2,
    'series=' + wingOpts.length);
  check('双翼画布在视口内且根节点对齐',
    wingOpts.length === 2
      && wingOpts.every(function (w) {
        return typeof w.width === 'number' && typeof w.height === 'number'
          && w.width > 0 && w.height > 0
          && w.left >= 0 && w.top >= 0
          && w.left + w.width <= 1200 && w.top + w.height <= 800;
      })
      && (wingOpts[0].left + wingOpts[0].width === wingOpts[1].left)
      && Math.abs((wingOpts[0].top + wingOpts[0].height / 2)
        - (wingOpts[1].top + wingOpts[1].height / 2)) <= 1,
    JSON.stringify(wingOpts.map(function (w) {
      return { left: w.left, top: w.top, width: w.width, height: w.height };
    })));
  win.eval('rebox();');
  var reboxedWings = chartStub.lastOption.series;
  check('单册资料收起重排仍使用正确的双翼盒子',
    reboxedWings.length === 2
      && ['left', 'top', 'width', 'height'].every(function (k, i) {
        return [0, 1].every(function (idx) {
          return Math.abs(Number(reboxedWings[idx][k]) - Number(wingOpts[idx][k])) <= 1;
        });
      }),
    JSON.stringify(reboxedWings.map(function (w) {
      return { left: w.left, top: w.top, width: w.width, height: w.height };
    })));
  layoutSelect.value = 'LR_ALL';
  win.onLayoutChange(layoutSelect.value);
  check('视角居中按钮存在', doc.getElementById('centerViewBtn') !== null);
  var callsC = chartStub.calls;
  win.centerView();
  check('centerView 重出图回到中心', chartStub.calls > callsC,
    `${callsC} -> ${chartStub.calls}`);
  console.log('[E2] 层级严格截断（切回小层级要收回深层展开）');
  win.onExpandChange('5');
  win.onExpandChange('2');
  var tRoot = chartStub.lastOption.series[0].data[0];
  var midNode = tRoot.children[0];        // 显示层 depth1：章节节点
  check('切到 2 层严格截断（章节收起）',
    midNode.collapsed === true,
    JSON.stringify({ mid: midNode.collapsed }));
  midNode.collapsed = false;              // 模拟手动展开章节
  win.centerView();
  var tRoot2 = chartStub.lastOption.series[0].data[0];
  check('视角居中保留手动展开', tRoot2.children[0].collapsed === false,
    String(tRoot2.children[0].collapsed));
  win.onExpandChange('2');                // 再次切层级 = 再次严格截断
  check('再次切层级仍严格截断',
    chartStub.lastOption.series[0].data[0].children[0].collapsed === true,
    String(chartStub.lastOption.series[0].data[0].children[0].collapsed));

  console.log('[E2b] 点击节点只展开一层（不整棵子弹开）');
  var synth = { name: 'r', children: [{ name: 'a', children: [
    { name: 'a1', children: [{ name: 'a11', children: [{ name: 'a111', children: [] }] }] },
    { name: 'a2', children: [{ name: 'a21', children: [] }] }] }] };
  win.applyExpandLevel(synth, 2);
  check('严格截断递归清理深层陈旧展开标记',
    synth.children[0].collapsed === false                     // a（depth1）默认展开
    && synth.children[0].children[0].collapsed === true       // a1（depth2）收起
    && synth.children[0].children[0].children[0].collapsed === true,  // a11（depth3）也收起
    JSON.stringify(synth.children[0]));
  synth.children[0].children[0].children[0].collapsed = false;   // 制造陈旧展开标记
  win.toggleNodeExpand(synth.children[0].children[0]);           // 点击展开 L2 节点 a1
  check('点击展开只出一层（L4 不跟着全开）',
    synth.children[0].children[0].collapsed === false
    && synth.children[0].children[0].children[0].collapsed === true,
    'a11.collapsed=' + synth.children[0].children[0].children[0].collapsed);
  win.toggleNodeExpand(synth.children[0].children[0]);           // 再点收起
  check('再点该节点收起一层', synth.children[0].children[0].collapsed === true,
    String(synth.children[0].children[0].collapsed));

  console.log('[E3] 鼠标交互映射：滚轮=上下滚动 / Ctrl+滚轮=缩放 / 中键拖拽=平移');
  chartStub.actions = [];
  doc.getElementById('chartContainer').dispatchEvent(
    new win.WheelEvent('wheel', { deltaY: 120, bubbles: true, cancelable: true }));
  var act1 = chartStub.actions[chartStub.actions.length - 1];
  check('滚轮 = 导图上下滚动（垂直平移，非缩放）',
    act1 && act1.type === 'treeRoam' && act1.dx === 0 && act1.dy !== 0 && act1.zoom === undefined,
    JSON.stringify(act1 || {}));
  chartStub.actions = [];
  doc.getElementById('chartContainer').dispatchEvent(
    new win.WheelEvent('wheel', { deltaY: -120, ctrlKey: true, bubbles: true, cancelable: true }));
  var act2 = chartStub.actions[chartStub.actions.length - 1];
  check('Ctrl+滚轮 = 缩放', act2 && act2.zoom !== undefined && act2.zoom > 1,
    JSON.stringify(act2 || {}));
  chartStub.actions = [];
  doc.getElementById('chartContainer').dispatchEvent(
    new win.MouseEvent('mousedown', { button: 1, clientX: 300, clientY: 200, bubbles: true, cancelable: true }));
  win.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 340, clientY: 230, bubbles: true }));
  win.dispatchEvent(new win.MouseEvent('mouseup', { button: 1, bubbles: true }));
  var act3 = chartStub.actions[chartStub.actions.length - 1];
  check('中键按下拖动 = 平移', act3 && act3.type === 'treeRoam' && act3.dx === 40 && act3.dy === 30,
    JSON.stringify(act3 || {}));

  console.log('[E3b] 左键空白处拖动：统一接管，双翼 group 位移 + treeRoam 记账');
  chartStub.actions = [];
  var g3x = fakeGroup.x, g3y = fakeGroup.y;
  doc.getElementById('chartContainer').dispatchEvent(
    new win.MouseEvent('mousedown', { button: 0, clientX: 300, clientY: 200, bubbles: true, cancelable: true }));
  win.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 340, clientY: 230, bubbles: true }));
  win.dispatchEvent(new win.MouseEvent('mouseup', { button: 0, bubbles: true }));
  var actL = chartStub.actions[chartStub.actions.length - 1];
  check('左键盒外拖动 = 平移（group 位移 + 记账 40,30）',
    actL && actL.type === 'treeRoam' && actL.dx === 40 && actL.dy === 30
      && fakeGroup.x === g3x + 40 && fakeGroup.y === g3y + 30,
    JSON.stringify(actL || {}) + ' g3=' + g3x + ',' + g3y + ' group=' + fakeGroup.x + ',' + fakeGroup.y + ' sceneZoom=' + win.sceneZoom);

  console.log('[E3c] 左键在内容盒内按下：同样由 panScene 统一接管');
  chartStub.actions = [];
  var g4x = fakeGroup.x, g4y = fakeGroup.y;
  doc.getElementById('chartContainer').dispatchEvent(
    new win.MouseEvent('mousedown', { button: 0, clientX: 40, clientY: 10, bubbles: true, cancelable: true }));
  win.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 90, clientY: 40, bubbles: true }));
  win.dispatchEvent(new win.MouseEvent('mouseup', { button: 0, bubbles: true }));
  var actC = chartStub.actions[chartStub.actions.length - 1];
  check('盒内左键拖动 = 平移（与盒外一致，无双倍位移）',
    chartStub.actions.length === 1 && actC && actC.type === 'treeRoam'
      && actC.dx === 50 && actC.dy === 30
      && fakeGroup.x === g4x + 50 && fakeGroup.y === g4y + 30,
    JSON.stringify(chartStub.actions) + ' group=' + fakeGroup.x + ',' + fakeGroup.y);

  console.log('[E3d] 左键按在按钮上：不启动画布拖动');
  chartStub.actions = [];
  var g5x = fakeGroup.x;
  doc.getElementById('centerViewBtn').dispatchEvent(
    new win.MouseEvent('mousedown', { button: 0, clientX: 1206, clientY: 20, bubbles: true, cancelable: true }));
  win.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 1250, clientY: 60, bubbles: true }));
  win.dispatchEvent(new win.MouseEvent('mouseup', { button: 0, bubbles: true }));
  check('左键按在按钮上不产生平移',
    chartStub.actions.length === 0 && fakeGroup.x === g5x,
    JSON.stringify(chartStub.actions));

  chartStub.actions = [];
  win.updateZoom(1);                      // 先归一（前面 Ctrl+滚轮留下缩放账）
  chartStub.actions = [];
  win.updateZoom(0.5);
  var act4 = chartStub.actions[chartStub.actions.length - 1];
  check('缩放条 = 场景缩放（真缩放，非位图缩放）',
    act4 && Math.abs(act4.zoom - 0.5) < 1e-6 && doc.getElementById('zoomLabel').textContent === '50%',
    JSON.stringify(act4 || {}) + ' label=' + doc.getElementById('zoomLabel').textContent);
  win.centerView();
  check('视角居中后缩放账归位 100%', doc.getElementById('zoomLabel').textContent === '100%',
    doc.getElementById('zoomLabel').textContent);
  chartStub.actions = [];
  win.fitView();
  var act5 = chartStub.actions.filter(function (a) { return a.zoom !== undefined; }).pop();
  check('适应窗口 = 场景缩放缩进视口', act5 !== undefined,
    JSON.stringify(chartStub.actions.slice(-3)));
  win.toggleMenu({ stopPropagation() {} }, 'exportMenu');
  check('打开另一菜单时前一菜单自动关闭（菜单互斥）',
    doc.getElementById('exportMenu').classList.contains('open')
    && !doc.getElementById('viewMenu').classList.contains('open'));
  win.closeAllMenus();

  console.log('[F] 导出（PNG / 讲义）');
  win.exportModulePNG();
  check('PNG 导出 toast', doc.getElementById('toast').textContent.includes('已导出 PNG 导图'));
  printedHTML = null;
  win.exportModuleHandout();
  check('当前章讲义含导学与章名',
    (printedHTML || '').includes('重点关注阶段划分') && (printedHTML || '').includes('第一章 心理发展'),
    (printedHTML || '').slice(0, 60));
  printedHTML = null;
  win.exportWholeHandout();
  check('全书讲义含推荐学习路径与全部章节',
    (printedHTML || '').includes('推荐学习路径')
    && (printedHTML || '').includes('第二章 学习理论'), (printedHTML || '').slice(0, 60));

  console.log('[F2] 导出：Markdown 大纲 / 复习卡 CSV');
  // 拦截下载：捕获导出的文本（jsdom 无 createObjectURL → 页面自动退化 data URI，走与 PNG 相同的链路）
  var capturedText = null;
  var OrigBlob = win.Blob;
  win.Blob = function (parts) { capturedText = (parts || []).join(''); return new OrigBlob(parts); };
  win.URL.createObjectURL = function () { throw new Error('blob unsupported in jsdom'); };
  function clickExportItem(label) {
    win.toggleMenu({ stopPropagation() {} }, 'exportMenu');
    var items = [].slice.call(doc.querySelectorAll('#exportMenu .menu-item'));
    var el = items.filter(function (x) { return x.textContent.indexOf(label) >= 0; })[0];
    if (el) el.click();
    return !!el;
  }
  win.toggleMenu({ stopPropagation() {} }, 'exportMenu');
  check('导出菜单含大纲与复习卡两项',
    doc.getElementById('exportMenu').textContent.includes('大纲 Markdown')
    && doc.getElementById('exportMenu').textContent.includes('复习卡 CSV'),
    doc.getElementById('exportMenu').textContent);
  win.closeAllMenus();
  check('菜单项「大纲 Markdown」可点', clickExportItem('大纲 Markdown'));
  var mdOut = capturedText || '';
  check('Markdown 大纲为当前视图（章级标题起）',
    mdOut.split('\n')[0] === '# 第一章 心理发展' && mdOut.includes('## 第一节 发展规律')
    && mdOut.includes('## 第二节 认知发展'), mdOut.slice(0, 100));
  check('Markdown 大纲含考点与摘要注记',
    mdOut.includes('> 考点（高频 · 简答）：心理发展的阶段')
    && mdOut.includes('> 摘要：规律概述。'), mdOut.slice(0, 260));
  check('Markdown 大纲导出提示', doc.getElementById('toast').textContent.includes('已导出 Markdown 大纲'));
  check('菜单项「复习卡 CSV」可点', clickExportItem('复习卡 CSV'));
  var csvRaw = capturedText || '';
  check('复习卡 CSV：UTF-8 BOM 防止 Excel 中文乱码', csvRaw.charAt(0) === '\ufeff',
    JSON.stringify(csvRaw.slice(0, 4)));
  var csvOut = csvRaw.replace(/^\ufeff/, '');
  check('复习卡 CSV：Front/Back/Tags 表头',
    csvOut.split('\r\n')[0] === '"Front","Back","Tags"', csvOut.slice(0, 60));
  check('复习卡 CSV：含考点卡与结构卡（与对话复习单元同源）',
    csvOut.includes('考什么') && csvOut.includes('包括哪几部分'), csvOut.slice(0, 200));
  check('复习卡 CSV：考点正文与子项清单进 Back',
    csvOut.includes('心理发展的阶段') && csvOut.includes('1. 第一节 发展规律'));
  check('复习卡 CSV：Tags 为 333::模块', csvOut.includes('"333::教心"'), csvOut.slice(0, 360));
  check('复习卡 CSV：每行恰好三个字段且全部带引号',
    csvOut.split('\r\n').filter(Boolean).every(function (line) {
      return line.charAt(0) === '"' && line.charAt(line.length - 1) === '"'
        && (line.match(/","/g) || []).length === 2;
    }));
  check('复习卡导出提示', doc.getElementById('toast').textContent.includes('张复习卡'));

  console.log('[F3] 讲义/详情面板的表格渲染（<td> 不得裸露）');
  printedHTML = null;
  win.exportModuleHandout();
  var ho = printedHTML || '';
  check('讲义：正文内嵌 HTML 表格渲染为真表格', ho.includes('<td>') && !ho.includes('&lt;td&gt;'),
    ho.includes('&lt;td&gt;') ? '出现裸 <td> 文本' : '');
  check('讲义：表格相邻的正文仍在', ho.includes('要点说明') && ho.includes('后续文字'));
  check('讲义：tables 字段的 HTML 表格渲染', ho.includes('<td>表2</td>'));
  check('讲义：tables 字段的 markdown 表格渲染', ho.includes('孔子') && ho.includes('人物'));
  check('讲义：加载 KaTeX 并等待公式排版完成',
    ho.includes('katex.min.css') && ho.includes('auto-render.min.js') && ho.includes('data-kt-math-ready'));
  var formulaRoot = {
    title: '公式测试', level: 0,
    children: [{
      title: '极限',
      level: 1,
      content: '若 $f$ 在 $[a,b]$ 连续，则\n$$f(b)-f(a)=f\'(\\xi)(b-a)$$',
      children: []
    }]
  };
  var formulaHo = win.buildHandoutHTML(formulaRoot, '公式测试', true);
  check('讲义：正文公式进入富文本渲染',
    formulaHo.includes('inline-formula') && formulaHo.includes('block-formula')
      && formulaHo.includes('f(b)-f(a)'),
    formulaHo.slice(formulaHo.indexOf('极限'), formulaHo.indexOf('极限') + 260));
  var infoHtml;
  win.showNodeInfo({
    name: '表格节点', level: 2, summary: '',
    content: '文字\n<table><tr><td>A</td><td>B</td></tr></table>',
    tables: ['<table><tr><td>C</td></tr></table>'], children: []
  });
  infoHtml = doc.getElementById('infoContent').innerHTML;
  check('详情面板：内容内嵌 HTML 表格渲染为真表格',
    infoHtml.includes('<td>A</td>') && !infoHtml.includes('&lt;td&gt;'));
  check('详情面板：tables 字段的 HTML 表格渲染', infoHtml.includes('<td>C</td>'));

  console.log('[G] 退出下钻 / 考点模式已移除');
  win.exitFocus();
  check('退出下钻后面包屑隐藏', !doc.getElementById('breadcrumb').classList.contains('show'));
  check('回到整树', chartStub.lastOption.series[0].data.length === 1);
  check('考点模式入口已移除', !doc.getElementById('examModeBar')
    && typeof win.toggleExamMode !== 'function' && typeof win.pruneToExam !== 'function');

  console.log('[G1] 节点定位点始终朝向父节点');
  // 当前夹具的根节点只有一个“书”包装层，先临时换成真正的双章节树验证双翼。
  var balancedTree = {
    title: '双翼测试', level: 0,
    children: [
      { title: '左翼章节', level: 1, children: [{ title: '左翼叶子', level: 2, children: [] }] },
      { title: '右翼章节', level: 1, children: [{ title: '右翼叶子', level: 2, children: [] }] }
    ]
  };
  win.eval('window.__savedTreeData = treeData; treeData = ' + JSON.stringify(balancedTree)
    + '; currentTargetPath = null; currentLayout = "LR"; renderChart("LR", 2);');
  var lrSeries = chartStub.lastOption.series;
  check('双翼布局由左右两个 tree series 组成', lrSeries.length === 2,
    'series=' + lrSeries.length);
  check('左翼：章节与叶子的点都在文字右侧',
    lrSeries[0].label.position === 'left' && lrSeries[0].label.align === 'right'
      && lrSeries[0].label.verticalAlign === 'middle'
      && lrSeries[0].leaves.label.position === 'left' && lrSeries[0].leaves.label.align === 'right',
    JSON.stringify({ label: lrSeries[0].label, leaves: lrSeries[0].leaves.label }));
  check('右翼：章节与叶子的点都在文字左侧',
    lrSeries[1].label.position === 'right' && lrSeries[1].label.align === 'left'
      && lrSeries[1].label.verticalAlign === 'middle'
      && lrSeries[1].leaves.label.position === 'right' && lrSeries[1].leaves.label.align === 'left',
    JSON.stringify({ label: lrSeries[1].label, leaves: lrSeries[1].leaves.label }));
  check('可见根节点胶囊贴住左右两翼汇合点',
    lrSeries[1].data[0].label && lrSeries[1].data[0].label.distance === 0,
    JSON.stringify(lrSeries[1].data[0].label || {}));
  win.eval('rebox();');
  var reboxed = chartStub.lastOption.series;
  check('展开/收起重排时同时更新左右两翼盒子',
    reboxed.length === 2 && reboxed[0].left !== undefined && reboxed[0].width
      && reboxed[1].left !== undefined && reboxed[1].width,
    JSON.stringify(reboxed));
  win.eval('onLayoutChange("LR_ALL")');
  var allLr = chartStub.lastOption.series;
  check('全部从左到右：单方向 LR 展开且点在文字左侧',
    allLr.length === 1 && allLr[0].orient === 'LR'
      && allLr[0].label.position === 'right' && allLr[0].label.align === 'left'
      && allLr[0].leaves.label.position === 'right' && allLr[0].leaves.label.align === 'left',
    JSON.stringify({ count: allLr.length, orient: allLr[0] && allLr[0].orient, label: allLr[0] && allLr[0].label }));
  win.eval('onLayoutChange("RL_ALL")');
  var allRl = chartStub.lastOption.series;
  check('全部从右到左：单方向 RL 展开且点在文字右侧',
    allRl.length === 1 && allRl[0].orient === 'RL'
      && allRl[0].label.position === 'left' && allRl[0].label.align === 'right'
      && allRl[0].leaves.label.position === 'left' && allRl[0].leaves.label.align === 'right',
    JSON.stringify({ count: allRl.length, orient: allRl[0] && allRl[0].orient, label: allRl[0] && allRl[0].label }));
  win.eval('onLayoutChange("LR")');
  win.eval('treeData = window.__savedTreeData; delete window.__savedTreeData; currentTargetPath = null;');

  // 双翼宽度必须按内容测量值展开，不能压回半屏宽度（窄视口下会压到子节点）。
  var longWing = win.balancedTreeBoxes({
    name: '双翼长标题测试',
    children: [
      { name: '第三章 儒学独尊与读经做官教育模式的初步形成', children: [] },
      { name: '第四章 封建国家教育体制的完善', children: [] }
    ]
  });
  check('长标题双翼按内容宽度展开（不再压回半屏）',
    longWing.leftW >= longWing.leftBox.W
      && longWing.rightW >= longWing.rightBox.W
      && (longWing.leftW > (1200 - 220) / 2 || longWing.rightW > (1200 - 220) / 2),
    JSON.stringify({ leftW: longWing.leftW, rightW: longWing.rightW,
      leftBox: longWing.leftBox.W, rightBox: longWing.rightBox.W }));

  // 标签碰撞兜底：重叠时按比例放大列间距/行间距。
  var sepA = { x: 0, y: 0, w: 100, h: 40, cx: 50, cy: 20 };
  var sepB = { x: 80, y: 10, w: 100, h: 40, cx: 130, cy: 30 };
  var sepHits = win.findLabelCollisions([sepA, sepB], 0);
  var sepF = win.computeSeparationFactors(sepHits);
  check('检测重叠标签并优先放大列间距',
    sepHits.length === 1 && sepF.x > 1 && sepF.y === 1,
    JSON.stringify({ hits: sepHits.length, factors: sepF }));
  var sepV = { x: 0, y: 30, w: 100, h: 40, cx: 50, cy: 50 };
  var sepVF = win.computeSeparationFactors(win.findLabelCollisions([sepA, sepV], 0));
  check('同列纵向重叠放大行间距',
    sepVF.y > 1 && sepVF.x === 1,
    JSON.stringify(sepVF));

  function makeFakeLabel(text, x, y, w, h, tx, ty) {
    return {
      type: 'text',
      style: {
        text: text, fontSize: 12, lineHeight: 18, padding: [5, 9], borderRadius: 8,
        rich: { chapter: { fontSize: 12, lineHeight: 18, padding: [0, 4, 0, 4] } }
      },
      ignore: false,
      setStyle: function(patch) { Object.assign(this.style, patch); },
      dirtyStyle: function() {},
      dirty: function() {},
      getBoundingRect: function() {
        return {
          x: x, y: y, width: w, height: h,
          clone: function() {
            return {
              x: this.x, y: this.y, width: this.width, height: this.height,
              applyTransform: function(m) { this.x += m[4]; this.y += m[5]; return this; }
            };
          }
        };
      },
      getComputedTransform: function() { return [1, 0, 0, 1, tx, ty]; }
    };
  }
  var fakeTextEl = makeFakeLabel('测试标签', 10, 20, 60, 20, 100, 200);
  zrStub.storage = { getDisplayList: function() { return [fakeTextEl, { type: 'tspan', parent: fakeTextEl }]; } };
  var collected = win.collectRenderedLabelRects();
  check('从 zrender 显示列表读取标签包围盒并去重',
    collected.length === 1 && collected[0].x === 110 && collected[0].y === 220
      && collected[0].w === 60 && collected[0].h === 20,
    JSON.stringify(collected));

  zrStub.storage = { getDisplayList: function() { return [fakeTextEl]; } };
  win.applyLabelScale(0.5);
  check('缩放时标签字号/行高/内边距一起缩小',
    fakeTextEl.style.fontSize === 6 && fakeTextEl.style.lineHeight === 9
      && JSON.stringify(fakeTextEl.style.padding) === '[2.5,4.5,2.5,4.5]'
      && fakeTextEl.style.rich.chapter.fontSize === 6
      && JSON.stringify(fakeTextEl.style.rich.chapter.padding) === '[0,2,0,2]',
    JSON.stringify(fakeTextEl.style));
  win.applyLabelScale(1);
  check('回到 100% 后标签样式恢复',
    fakeTextEl.style.fontSize === 12 && fakeTextEl.style.lineHeight === 18
      && JSON.stringify(fakeTextEl.style.padding) === '[5,9,5,9]'
      && fakeTextEl.style.rich.chapter.fontSize === 12,
    JSON.stringify(fakeTextEl.style));
  delete zrStub.storage;

  var shiftGroup = {
    x: 10, y: 20, scaleX: 0.5, scaleY: 0.5,
    dirty: function() {}
  };
  var shiftCtx = { group: shiftGroup, view: { _mainGroup: { x: 100, y: 200 } } };
  var savedShift = [{
    ctx: shiftCtx, x: 10, y: 20, scaleX: 0.5, scaleY: 0.5, mgX: 100, mgY: 200
  }];
  shiftCtx.view._mainGroup.x = 120;
  shiftCtx.view._mainGroup.y = 260;
  win.compensateTreeViewShift(savedShift);
  check('series 盒子移动时反向补偿视图位移（避免展开/收起跳变）',
    shiftGroup.x === 0 && shiftGroup.y === -10,
    JSON.stringify({ x: shiftGroup.x, y: shiftGroup.y }));

  win.eval('onLayoutChange("TB")');
  var tbSeries = chartStub.lastOption.series[0];
  check('上下布局：点在文字上方',
    tbSeries.orient === 'TB' && tbSeries.label.position === 'bottom'
      && tbSeries.label.align === 'center' && tbSeries.label.verticalAlign === 'top'
      && tbSeries.leaves.label.position === 'bottom' && tbSeries.leaves.label.align === 'center',
    JSON.stringify({ label: tbSeries.label, leaves: tbSeries.leaves.label }));
  win.eval('onLayoutChange("BT")');
  var btSeries = chartStub.lastOption.series[0];
  check('下上布局：点在文字下方',
    btSeries.orient === 'BT' && btSeries.label.position === 'top'
      && btSeries.label.align === 'center' && btSeries.label.verticalAlign === 'bottom'
      && btSeries.leaves.label.position === 'top' && btSeries.leaves.label.align === 'center',
    JSON.stringify({ label: btSeries.label, leaves: btSeries.leaves.label }));
  win.eval('onLayoutChange("LR")');
  await tick(20);

  console.log('[G+] 长标题换行与流程图画布');
  // ① 胶囊背景画在整块 label 上（rich token 背景会随每行文字宽度拼出缺角）
  // 显式重出图后再取 option：避免 80ms 的 rebox 定时器用局部 series 覆盖 lastOption。
  win.eval('renderChart("LR", currentExpandLevel, currentTargetPath, true);');
  var pillSeries = chartStub.lastOption.series[0];
  var pillRoot = pillSeries && pillSeries.data && pillSeries.data[0];
  var pillChapter = pillRoot && (pillRoot.children || [])[0];
  check('根胶囊背景画在整块 label 上（折行后仍是单个圆角矩形）',
    !!pillRoot && pillRoot.label.backgroundColor === '#d97757'
      && pillRoot.label.borderRadius === 10
      && Array.isArray(pillRoot.label.padding)
      && !pillSeries.label.rich.rootPill.backgroundColor
      && !pillSeries.label.rich.rootPill.padding
      && !pillSeries.label.rich.rootPill.borderRadius,
    JSON.stringify({ node: pillRoot && pillRoot.label, rich: pillSeries && pillSeries.label && pillSeries.label.rich.rootPill }));
  check('章节胶囊同样使用 label 级背景与圆角',
    !!pillChapter && pillChapter.label.backgroundColor === '#fbf1ed'
      && pillChapter.label.borderRadius === 8
      && !pillSeries.label.rich.chapter0.backgroundColor
      && !pillSeries.label.rich.chapter0.padding,
    JSON.stringify({ chapter: pillChapter && pillChapter.label, rich: pillSeries && pillSeries.label && pillSeries.label.rich.chapter0 }));

  var packSection = pillChapter && (pillChapter.children || [])[0];
  check('标签保持垂直居中（行位由标签高度打包决定）',
    !!packSection && pillSeries.label.verticalAlign === 'middle'
      && packSection.label.verticalAlign == null
      && pillChapter.label.verticalAlign == null,
    JSON.stringify({
      series: pillSeries.label.verticalAlign,
      chapter: pillChapter && pillChapter.label.verticalAlign,
      section: packSection && packSection.label.verticalAlign
    }));

  function seriesNode(name, depth, children) {
    return {
      name: name, depth: depth, isExpand: true, children: children || [],
      _layout: { x: 0, y: depth * 100 },
      getLayout: function() { return this._layout; },
      setLayout: function(layout) { Object.assign(this._layout, layout); }
    };
  }
  var tallA = seriesNode('第一节 隋唐时期的文教政策和学校教育制度的发展', 3);
  var tallB = seriesNode('第四节 魏晋南北朝与隋唐时期的教育思想与制度', 3);
  var shortC = seriesNode('知识导图', 3);
  var tallChapter = seriesNode('第四章 封建国家教育体制的完善', 2, [tallA, tallB, shortC]);
  var packRoot = seriesNode('根', 1, [tallChapter]);
  var packTotal = win.packTreeRows(packRoot, win.visibleSeriesChildren, win.seriesNodeLabelHeight);
  var tallH = win.treeLabelBlockHeight(tallA.name, '');
  check('ECharts 树按标签实际行数打包（两行标题本地多占行）',
    tallH > win.treeLabelBlockHeight(shortC.name, '')
      && tallA.__ktRowBlock === tallH
      && Math.abs(tallChapter.__ktRowBlock
        - (tallA.__ktRowBlock + tallB.__ktRowBlock + shortC.__ktRowBlock + win.TREE_ROW_GAP * 2)) <= 0.01
      && Math.abs(packTotal - tallChapter.__ktRowBlock) <= 0.01,
    JSON.stringify({ total: packTotal, chapter: tallChapter.__ktRowBlock, tallH: tallH }));
  check('同一列的相邻行块不重叠且保留行距',
    Math.abs(tallA.__ktRowY - tallB.__ktRowY) >= (tallA.__ktRowBlock + tallB.__ktRowBlock) / 2 + win.TREE_ROW_GAP - 0.01
      && Math.abs(tallB.__ktRowY - shortC.__ktRowY) >= (tallB.__ktRowBlock + shortC.__ktRowBlock) / 2 + win.TREE_ROW_GAP - 0.01,
    JSON.stringify({
      ys: [tallA.__ktRowY, tallB.__ktRowY, shortC.__ktRowY],
      blocks: [tallA.__ktRowBlock, tallB.__ktRowBlock, shortC.__ktRowBlock]
    }));

  var tooltipHtml = chartStub.lastOption.tooltip.formatter({
    componentSubType: 'tree',
    data: {
      name: '父节点', level: 4,
      children: [
        { name: '知识点一 教育与教育学' },
        { name: '知识点二 <韩愈>' }
      ]
    }
  });
  check('悬浮预览列出子节点标题', tooltipHtml.includes('子节点 · 2')
    && tooltipHtml.includes('知识点一 教育与教育学')
    && tooltipHtml.includes('知识点二 &lt;韩愈&gt;'),
    tooltipHtml.slice(-300));
  check('悬浮预览不再只显示 child nodes 计数',
    !tooltipHtml.includes('child nodes'), tooltipHtml.slice(-160));

  // ② 节点长标题折行（KT.wrapLabel）
  check('短标题原样返回', win.KT.wrapLabel('教育心理学', 14, 3) === '教育心理学');
  check('空标题返回空', win.KT.wrapLabel('', 14, 3) === '' && win.KT.wrapLabel(null, 14, 3) === '');
  const longTitle = '3. 按照诸育相互联系、相互渗透的整体观点进行德育、智育、体育、美育、劳动教育的思想';
  const wrapped = win.KT.wrapLabel(longTitle, 14, 3);
  const wl = wrapped.split('\n');
  check('长标题折行且不超过 3 行', wl.length >= 2 && wl.length <= 3, `行数=${wl.length}`);
  check('每行约 14 字（收尾标点并行后 ≤17）',
    wl.every(l => Array.from(l).length <= 17), wl.map(l => l.length).join(','));
  check('折行后文字仍完整可读',
    wrapped.replace(/\n/g, '').startsWith(longTitle.slice(0, 10)));
  const punct = win.KT.wrapLabel('教学评价的基本方法与实施步骤、常见问题、改进策略与案例', 14, 3);
  check('行首不出现收尾标点',
    punct.split('\n').every(l => l && !'、。，．'.includes(l[0])), punct);
  const trunc = win.KT.wrapLabel('一'.repeat(60), 14, 3);
  check('超长标题截断并加省略号',
    trunc.split('\n').length === 3 && trunc.endsWith('…'), trunc.slice(-3));

  // ② 流程图：原尺寸渲染 + 缩放工具条 + 全屏查看
  win.showNodeInfo({
    name: '含流程图节点', level: 2, summary: '',
    mermaid: 'graph TD\n    A[开始] --> B[结束]', children: []
  });
  await win.KT.postRenderMathAndMermaid(doc.getElementById('infoContent'));
  await tick(80);
  const blk = doc.querySelector('#infoContent .mermaid-block');
  check('详情面板渲染流程图块', !!blk && doc.getElementById('infoContent').textContent.includes('流程图'));
  check('装上缩放工具条', !!blk && !!blk.querySelector('.mm-toolbar') && !!blk.querySelector('[data-mm="fit"]'));
  check('画布视口已启用（不再按面板宽度压扁）',
    !!blk && !!blk.querySelector('.mm-viewport') && !!blk.querySelector('.mm-viewport svg'));
  const scaleEl = blk.querySelector('.mm-scale');
  const before = scaleEl.textContent;
  blk.querySelector('[data-mm="in"]').click();
  check('放大按钮生效', scaleEl.textContent !== before, `${before} -> ${scaleEl.textContent}`);
  blk.querySelector('[data-mm="expand"]').click();
  check('全屏查看打开', !!doc.querySelector('.mm-lightbox'));
  check('全屏层带缩放与关闭操作',
    doc.querySelector('.mm-lightbox').textContent.includes('关闭')
    && doc.querySelector('.mm-lightbox').textContent.includes('适应'));
  win.KT.closeMermaidLightbox();
  check('关闭后全屏层移除、图放回原位',
    !doc.querySelector('.mm-lightbox') && !!blk.querySelector('.mm-viewport svg'));

  // ③ 节点间距调节入口已移除
  check('左下角间距调节入口已移除',
    !doc.getElementById('spacingSlider') && !doc.getElementById('spacingToggle')
    && !win.toggleSpacingPanel);

  // ④ 图标系统（界面不用 emoji）
  check('图标为设计好的线性图标', /^<svg class="ic/.test(win.KT.iconHtml('menu'))
    && win.KT.iconHtml('menu').includes('<path'));
  const slot = doc.createElement('span');
  slot.setAttribute('data-icon', 'target');
  doc.body.appendChild(slot);
  win.KT.applyIconSlots(doc.body);
  check('data-icon 占位符自动填成图标', !!slot.querySelector('svg.ic'));

  console.log('[G2] 双击重命名编辑框已移除');
  {
    check('编辑框与提示元素已移除',
      !doc.getElementById('inlineEditor') && !doc.getElementById('editHint'));
    check('编辑函数已移除',
      typeof win.startEdit !== 'function' && typeof win.saveEdit !== 'function'
      && typeof win.cancelEdit !== 'function');
    let editorCss = false;
    for (const sheet of doc.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
      for (const r of (rules || [])) {
        if (r.selectorText && r.selectorText.indexOf('inline-editor') >= 0) editorCss = true;
      }
    }
    check('编辑器样式无残留', !editorCss);
    // 双击不应再弹编辑框（dblclick 处理器已移除）
    chartStub.lastOption && chartStub.dispatchAction && chartStub.actions.length >= 0;
    check('页面内不再有编辑态逻辑', typeof win.editState === 'undefined');
  }

  console.log('[H] 资源混版自检（浏览器缓存了旧版 common.js）');
  {
    const vc2 = new VirtualConsole();
    vc2.on('jsdomError', () => {});
    const dom2 = new JSDOM(HTML_STRIPPED, {
      url: 'http://localhost/ui/tree_mindmap.html?file=test_tree.json',
      runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc2,
    });
    const win2 = dom2.window, doc2 = win2.document;
    win2.echarts = { init: () => ({ setOption() {}, on() {}, resize() {}, getDataURL: () => '' }) };
    win2.mermaid = win.mermaid;
    win2.fetch = win.fetch;
    // 旧版 KT：缺 bookModules / Panels
    const run2 = (code) => { const s = doc2.createElement('script'); s.textContent = code; doc2.head.appendChild(s); };
    run2('window.KT={escapeHtml:function(s){return String(s);},showToast:function(){},'
      + 'initTheme:function(){},getTheme:function(){return "light";},toggleTheme:function(){},'
      + 'transformNode:function(n){return n;}};');
    run2(inlineMatch[1]);
    await win2.initChart();
    const txt = doc2.getElementById('chart').textContent;
    check('给出可操作的强制刷新提示（而不是 KT.bookModules is not a function）',
      txt.includes('版本不一致') && txt.includes('Cmd+Shift+R') && !txt.includes('is not a function'),
      txt.slice(0, 90));
  }

  console.log('[H2] 运行时错误');
  check('无 jsdom 运行时异常', jsdomErrors.length === 0, jsdomErrors.slice(0, 2).join(' ; '));

  console.log(`\n${'='.repeat(44)}\nPASS: ${pass}  FAIL: ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e); process.exit(2); });
