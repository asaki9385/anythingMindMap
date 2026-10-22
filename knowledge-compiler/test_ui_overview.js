/*
 * overview.html 端到端测试（jsdom）—— 资料信息页（导学 / 学习路径 / 模块目录 / 复习进度）
 * 覆盖：封面与统计 / 全书导学 / 学习路径顺序 / 章节卡片（导学摘要 + 打开导图 + 对话复习）/
 *      复习进度区 / 深链到导图页 / 对话复习组件挂载
 *
 * 依赖：npm i jsdom（或设 JSDOM_PATH）
 * 运行：node knowledge-compiler/test_ui_overview.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/overview.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const WIDGET = fs.readFileSync(path.join(PROJ, 'ui/review_widget.js'), 'utf8');

let pass = 0, fail = 0;
const jsdomErrors = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = (ms) => new Promise(r => setTimeout(r, ms || 40));

const TREE = {
  title: '教心', guide: '全书导学：本书围绕教育心理学展开。\n建议按推荐顺序逐章学习。',
  children: [{
    title: '教心', level: 0, children: [
      {
        title: '第一章 心理发展', level: 1,
        guide: '本章概述心理发展的基本框架。\n重点关注阶段划分。',
        prerequisites: [],
        keywords: ['发展', '关键期'],
        exam_points: [{ point: '心理发展的阶段', frequency: '高频', type: '简答' }],
        children: [
          { title: '第一节 发展规律', level: 2, summary: '规律概述。', children: [], exam_points: [] },
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

const vc = new VirtualConsole();
vc.on('jsdomError', e => {
  if (/Could not load|not implemented|Not implemented/.test(e.message)) return;
  jsdomErrors.push(e.message);
});

const inlineMatch = HTML.match(/<script>([\s\S]*?)<\/script>/);
check('内联脚本可提取', !!inlineMatch);
const HTML_STRIPPED = HTML.replace(inlineMatch[0], '');

const dom = new JSDOM(HTML_STRIPPED, {
  url: 'http://localhost/ui/overview.html?file=test_tree.json',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const win = dom.window, doc = win.document;

win.mermaid = { initialize() {}, render: async () => ({ svg: '<svg data-test="1"></svg>' }) };
win.alert = m => console.log('  [alert]', m);
let openedUrl = null;
win.open = function (url) { openedUrl = url; return { focus() {}, close() {} }; };

const SNAP_START = {
  session_id: 'rw_9', file_key: 'test_tree.json', tree_title: '教心', done: false,
  current: { unit_id: 'u1', title: '第一章 心理发展', group: '', path: [], reference_count: 2, turn_count: 0, hint_count: 0, last_level: null, awaiting: 'answer', exam_points: [], h4_used: false },
  counters: { turn: 0, turn_max: 6, hint: 0, hint_max: 2 },
  units: [{ unit_id: 'u1', title: '第一章 心理发展', group: '', status: 'pending', stuck_level: '', turns: 0, hints: 0 }],
  messages: [{ role: 'guide', text: '先不翻书——说说「第一章 心理发展」，现在能想起什么就说什么，碎片也算。', kind: 'question' }],
  counts: { passed: 0, half: 0, failed: 0 }, report: null, degraded: false
};
const startedBodies = [];
win.fetch = async function (url, opts) {
  url = String(url);
  const j = (o) => ({ ok: true, status: 200, json: async () => o });
  if (url.includes('/api/review/start')) {
    try { startedBodies.push(JSON.parse(opts.body)); } catch (e) {}
    return j(SNAP_START);
  }
  if (url.includes('/api/review/progress')) {
    return j({ retest: [{ unit_id: 'u9', title: '学习动机' }], weak: [{ unit_id: 'u8', title: '技能形成' }],
                due: [{ unit_id: 'u9', title: '学习动机' }], due_count: 1,
                unit_count: 5, sessions: [{ passed: 3, half: 1, failed: 1 }] });
  }
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
  await tick(160);

  console.log('[A] 封面与统计');
  check('标题为书名', doc.querySelector('.hero-title').textContent === '教心');
  check('全书导学分段渲染', doc.querySelectorAll('.hero-guide p').length === 2
    && doc.querySelector('.hero-guide').textContent.includes('教育心理学'));
  var nums = [...doc.querySelectorAll('.stat .num')].map(e => e.textContent.trim());
  check('统计四项：模块 2 / 知识点 8 / 考点 1 / AI摘要 1',
    nums.join(',') === '2,8,1,1', nums.join(','));
  check('模块对象保留导学与先修（transformNode 未丢失字段）',
    win.KT.bookModules(win.treeData).every(m => typeof m.guide === 'string' && Array.isArray(m.prerequisites))
    && win.KT.bookModules(win.treeData)[1].prerequisites[0] === '第一章 心理发展');
  check('页面标题更新', doc.title.includes('教心'));

  console.log('[B] 学习路径');
  var items = [...doc.querySelectorAll('.path-item')];
  check('路径两项且带序号', items.length === 2
    && items[0].querySelector('.path-num').textContent === '1'
    && items[1].querySelector('.path-num').textContent === '2');
  check('拓扑顺序：第一章在前', items[0].textContent.includes('第一章'));
  check('短路径使用卡片网格，不生成会缩字的 Mermaid 图',
    doc.querySelector('.path-list').classList.contains('is-reading') === false
      && !doc.querySelector('.mermaid-box')
      && !doc.querySelector('.mermaid-source'));
  check('路径项使用可聚焦的按钮并保留完整标题',
    items[0].tagName === 'BUTTON'
      && items[0].querySelector('.path-title').textContent === '第一章 心理发展'
      && !!items[0].querySelector('.path-enter svg'));
  var shortModules = Array.from({ length: 5 }, function (_, i) {
    return { title: '第' + (i + 1) + '章 短标题' };
  });
  var nineShortModules = Array.from({ length: 9 }, function (_, i) {
    return { title: '第' + (i + 1) + '章 短标题' };
  });
  var longModules = Array.from({ length: 17 }, function (_, i) {
    return { title: '第' + (i + 1) + '章 这是一个较长的章节标题用于测试学习路径显示' };
  });
  var longNine = Array.from({ length: 9 }, function (_, i) {
    return { title: '第' + (i + 1) + '章 这是一个很长很长的章节标题内容需要自动换行显示' };
  });
  check('少量短标题使用卡片网格', win.studyPathLayout(shortModules) === 'grid');
  check('章节较多时切换为纵向阅读轨道', win.studyPathLayout(nineShortModules) === 'reading');
  check('大量长标题切换为纵向阅读轨道', win.studyPathLayout(longModules) === 'reading');
  check('少量但超长标题也切换为纵向阅读轨道', win.studyPathLayout(longNine) === 'reading');
  var probe = [{ title: 'A', prerequisites: ['C'] }, { title: 'B', prerequisites: [] }, { title: 'C', prerequisites: [] }];
  check('拓扑排序：先修优先于文档顺序（B→C→A）',
    JSON.stringify(win.KT.computeStudyOrder(probe)) === JSON.stringify([1, 2, 0]),
    JSON.stringify(win.KT.computeStudyOrder(probe)));

  console.log('[C] 模块目录卡片');
  var cards = doc.querySelectorAll('.cards .card');
  check('两张章节卡片', cards.length === 2);
  var c1 = cards[0];
  check('卡片有顺序徽章', !!c1.querySelector('.order-badge'));
  check('卡片展示导学摘要', c1.querySelector('.card-guide').textContent.includes('心理发展的基本框架'));
  check('卡片关键词渲染', c1.querySelector('.card-kws').textContent.includes('关键期'));
  check('卡片元信息含考点', c1.querySelector('.card-meta').textContent.includes('1 考点')
    && c1.querySelector('.card-meta svg.ic') !== null);
  check('卡片动作：打开导图 + 对话复习',
    c1.querySelectorAll('.card-actions .btn').length === 2
    && c1.querySelector('.card-actions').textContent.includes('打开导图')
    && c1.querySelector('.card-actions').textContent.includes('对话复习'));

  console.log('[D] 复习进度');
  var boxes = doc.querySelectorAll('.prog-box');
  check('三个进度块', boxes.length === 3);
  check('已练 5 / 待复测 1 / 薄弱 1',
    boxes[0].querySelector('.pn').textContent === '5'
    && boxes[1].querySelector('.pn').textContent === '1'
    && boxes[2].querySelector('.pn').textContent === '1');
  check('展示上次战绩', boxes[0].textContent.includes('过关 3'));
  check('待复测块显示今日到期数', boxes[1].textContent.includes('今日到期 1'),
    boxes[1].textContent);
  check('到期时主按钮变为「今日复练」',
    doc.querySelector('.section .btn.primary') !== null
    || doc.body.innerHTML.includes('开始今日复练'),
    (doc.body.innerHTML.match(/开始今日复练[^<]*/) || [''])[0]);

  console.log('[E] 跳转与对话');
  check('顶栏「打开导图」指向导图页', doc.getElementById('mapLink').href.includes('/ui/tree_mindmap.html?file=test_tree.json'));
  win.openMap('第二章 学习理论');
  check('卡片「打开导图」带章节深链',
    (openedUrl || '').includes('tree_mindmap.html') && (openedUrl || '').includes(encodeURIComponent('第二章 学习理论')),
    openedUrl || '');
  win.startReview('第二章 学习理论');
  await tick(100);
  check('对话面板打开', doc.getElementById('rwPanel').classList.contains('open'));
  check('AI 直接提问（非用户发问）',
    doc.getElementById('rwMsgs').textContent.includes('想起')
    && doc.querySelectorAll('#rwMsgs .rw-msg.learner').length === 0);
  check('请求按所选章节开始',
    startedBodies.length && startedBodies[0].chapter === '第二章 学习理论',
    JSON.stringify(startedBodies[0] || {}));
  check('快捷选项齐备',
    ['想不起来', '跳过这个点', '结束本轮'].every(t => doc.getElementById('rwQuick').textContent.includes(t)));

  console.log('[E2] 会话中途切章节 → 出现可点切换入口（不静默重置）');
  win.ReviewWidget.setContext({ chapter: '第一章 心理发展' });
  await tick(60);
  var quick = doc.getElementById('rwQuick').textContent;
  check('出现「切换」按钮且原会话内容仍在',
    quick.includes('切换') && quick.includes('第一章 心理发展')
    && doc.getElementById('rwMsgs').textContent.includes('想起'));
  win.ReviewWidget.setContext({ chapter: '第二章 学习理论' });
  await tick(30);
  check('上下文切回当前会话章节后按钮消失',
    !doc.getElementById('rwQuick').textContent.includes('切换'));

  console.log('[F] 资源混版自检（浏览器缓存了旧版 common.js）');
  {
    const vc2 = new VirtualConsole();
    vc2.on('jsdomError', () => {});
    const dom2 = new JSDOM(HTML_STRIPPED, {
      url: 'http://localhost/ui/overview.html?file=test_tree.json',
      runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc2,
    });
    dom2.window.fetch = win.fetch;
    dom2.window.mermaid = win.mermaid;
    const d2 = dom2.window.document;
    const run2 = (code) => { const s = d2.createElement('script'); s.textContent = code; d2.head.appendChild(s); };
    // 旧版 KT：有基础方法但没有 bookModules / Panels —— 正是用户遇到的情形
    run2('window.KT={escapeHtml:function(s){return String(s);},showToast:function(){},'
      + 'initTheme:function(){},getTheme:function(){return "light";},toggleTheme:function(){}};');
    run2(WIDGET);
    run2(inlineMatch[1]);
    await tick(120);
    const txt = d2.getElementById('page').textContent;
    check('给出可操作的强制刷新提示（而不是 KT.bookModules is not a function）',
      txt.includes('版本不一致') && txt.includes('Cmd+Shift+R') && !txt.includes('is not a function'),
      txt.slice(0, 90));
  }

  console.log('[G] 运行时错误');
  check('无 jsdom 运行时异常', jsdomErrors.length === 0, jsdomErrors.slice(0, 2).join(' ; '));

  console.log(`\n${'='.repeat(44)}\nPASS: ${pass}  FAIL: ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e); process.exit(2); });
