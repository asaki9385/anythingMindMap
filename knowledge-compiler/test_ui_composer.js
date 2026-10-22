/*
 * Rich Composer 测试（jsdom）
 * 覆盖：/ 快捷指令、@ 章节引用、键盘选择、菜单关闭、CJK composition guard 基本状态。
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const WIDGET = fs.readFileSync(path.join(PROJ, 'ui/review_widget.js'), 'utf8');
let pass = 0, fail = 0;
const errors = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = () => new Promise((r) => setTimeout(r, 30));

const vc = new VirtualConsole();
vc.on('jsdomError', (e) => {
  if (/Could not load|not implemented|Not implemented/.test(e.message)) return;
  errors.push(e.message);
});
const dom = new JSDOM('<!doctype html><html data-theme="light"><body><div id="mount"></div></body></html>', {
  url: 'http://localhost/ui/test_composer.html', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc
});
const win = dom.window;
const doc = win.document;
win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
if (typeof TextDecoder !== 'undefined') win.TextDecoder = TextDecoder;
if (typeof TextEncoder !== 'undefined') win.TextEncoder = TextEncoder;
win.fetch = async () => ({ ok: true, status: 200, body: null, json: async () => ({}) });
win.eval(COMMON);
win.eval(WIDGET);
const activityEvents = [];
const conversationEvents = [];
doc.addEventListener('kt-review-activity', (event) => activityEvents.push(event.detail || {}));
doc.addEventListener('kt-review-conversation', (event) => conversationEvents.push(event.detail || {}));
win.ReviewWidget.mount({ file: 'demo', container: doc.getElementById('mount'), title: 'AI 学习对话' });
win.ReviewWidget.setMentions([{ id: 'c1', label: '第一章 记忆与注意' }, { id: 'c2', label: '第二章 练习与迁移' }]);
const input = doc.getElementById('rwInput');
const suggest = doc.getElementById('rwSuggest');

(async function run() {
  console.log('[A] Slash Commands');
  input.value = '/';
  input.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick();
  check('输入 / 打开快捷指令', suggest.hidden === false && doc.getElementById('rwSuggestHead').textContent.includes('快捷指令'));
  check('四个快捷指令', doc.querySelectorAll('#rwSuggestList .rw-suggest-item').length === 4);
  check('快捷指令含知识补全', doc.getElementById('rwSuggestList').textContent.includes('知识补全'));
  check('默认选中第一项', !!doc.querySelector('#rwSuggestList .rw-suggest-item.is-selected'));
  input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
  check('ArrowDown 改变选中项', doc.querySelectorAll('#rwSuggestList .rw-suggest-item.is-selected').length === 1 && doc.querySelectorAll('#rwSuggestList .rw-suggest-item')[1].classList.contains('is-selected'));
  input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await tick();
  check('Enter 应用指令并关闭菜单', suggest.hidden === true && input.value.includes('出三个问题'));

  console.log('[B] Mentions');
  input.value = '@';
  input.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick();
  check('输入 @ 打开章节引用', suggest.hidden === false && doc.querySelectorAll('#rwSuggestList .rw-suggest-item').length === 2);
  input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await tick();
  check('Enter 应用章节 Mention', suggest.hidden === true && input.value === '@第一章 记忆与注意 ');

  console.log('[C] Escape / 关闭');
  input.value = '/';
  input.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick();
  input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await tick();
  check('Escape 关闭建议菜单', suggest.hidden === true);

  console.log('[C2] Thinking Process');
  const processPanel = doc.getElementById('rwProcess');
  check('消息区不再渲染可展开的思考进程面板', !processPanel && !doc.getElementById('rwProcessToggle'));
  check('组件通过事件向外部广播简略状态',
    activityEvents.some((entry) => entry.phase === 'idle' && String(entry.detail || '').includes('已就绪')));

  console.log('[C3] AI 回答富文本');
  var rich = win.ReviewWidget.renderMarkdown(
    '极限是什么？按资料讲：\n\n'
    + '- **一句话定义**：描述稳定趋势。\n'
    + '- **符号写法**：$\\lim\_{x\\to x_0} f(x)=A$。\n'
    + '- **性质**：\n'
    + '  - 极限若存在，则**唯一**；\n'
    + '  - **局部有界**。\n\n'
    + '| 概念 | 公式 |\n'
    + '| --- | --- |\n'
    + '| 极限 | $A$ |\n\n'
    + '$$\\lim\_{x\\to x_0} f(x)=A$$'
  );
  check('AI 回答：无序列表渲染', rich.includes('<ul>') && rich.includes('<li>'));
  check('AI 回答：嵌套列表渲染', rich.includes('<li><strong>性质</strong>：<ul>'));
  check('AI 回答：行内公式保留给 KaTeX',
    rich.includes('rw-formula-inline') && rich.includes('$\\lim_{x\\to x_0} f(x)=A$'),
    rich.slice(0, 300));
  check('AI 回答：Markdown 表格渲染',
    rich.includes('<table class="rw-table">') && rich.includes('<th>概念</th>')
      && rich.includes('<td>极限</td>'));
  check('AI 回答：表格单元格内公式',
    rich.includes('<td><span class="rw-formula-inline">$A$</span></td>'));
  check('AI 回答：块级公式渲染',
    rich.includes('<div class="rw-formula-block">$$') && rich.includes('$$</div>'));
  var spaced = win.ReviewWidget.renderMarkdown('&#x20; - 子项');
  check('AI 回答：HTML 空格实体归一化', spaced.includes('<ul>') && spaced.includes('子项'));

  console.log('[D] Composer Toolbar');
  const mentionBtn = doc.getElementById('rwMentionBtn');
  const completeBtn = doc.getElementById('rwCompleteBtn');
  check('Composer 工具栏含章节引用与知识补全入口', !!mentionBtn && !!completeBtn
    && completeBtn.getAttribute('aria-label') === '知识补全');
  input.value = '';
  mentionBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick();
  check('引用按钮直接展开章节列表且不污染输入框',
    input.value === '' && suggest.hidden === false
      && mentionBtn.getAttribute('aria-expanded') === 'true'
      && doc.querySelectorAll('#rwSuggestList .rw-suggest-item').length === 2);
  mentionBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick();
  check('再次点击引用按钮可收起章节列表',
    suggest.hidden === true && mentionBtn.getAttribute('aria-expanded') === 'false');
  mentionBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick();
  input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await tick();
  check('选择章节后插入引用', input.value === '@第一章 记忆与注意 ');

  const originalFetch = win.fetch;
  win.fetch = async function (url) {
    if (String(url).indexOf('/api/review/units') === 0) {
      return { ok: true, status: 200, json: async () => ({ groups: [{ name: '第三章 按需加载' }] }) };
    }
    return originalFetch(url);
  };
  win.ReviewWidget.setMentions([]);
  input.value = '';
  mentionBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(80);
  check('章节缓存为空时按需读取并直接展示',
    suggest.hidden === false && doc.getElementById('rwSuggestList').textContent.includes('第三章 按需加载'));
  input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  win.fetch = originalFetch;

  win.ReviewWidget.setMode('learn');
  check('学习模式下知识补全可用', completeBtn.disabled === false);
  completeBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('知识补全开启后显示明确状态',
    completeBtn.getAttribute('aria-pressed') === 'true'
      && completeBtn.classList.contains('is-active')
      && input.placeholder.includes('补全'));
  win.ReviewWidget.setPreferences({
    replyStyle: 'detailed', thinkingEffort: 'deep', citationStyle: 'strong',
    language: 'bilingual', customInstruction: '多举例子'
  });
  let learnPayload = null;
  win.fetch = async function (url, opts) {
    if (String(url) === '/api/review/learn') {
      learnPayload = JSON.parse((opts && opts.body) || '{}');
      return {
        ok: true, status: 200,
        json: async () => ({ reply: '资料依据：……\n\n【AI 补充】完整说明。', sources: [], completion_mode: true })
      };
    }
    return originalFetch(url, opts);
  };
  win.ReviewWidget.ask('请补全这个知识点');
  await tick(120);
  win.fetch = originalFetch;
  check('学习请求携带知识补全标记', !!learnPayload && learnPayload.completion_mode === true);
  check('学习请求携带个性化偏好', !!learnPayload
    && learnPayload.reply_style === 'detailed'
    && learnPayload.thinking_effort === 'deep'
    && learnPayload.citation_style === 'strong'
    && learnPayload.language === 'bilingual'
    && learnPayload.custom_instruction === '多举例子');
  check('补全回答带 AI 补充标识', !!doc.querySelector('#rwMsgs .rw-supplement-badge'));
  check('学习模式发出对话活跃状态供外层收起欢迎词',
    conversationEvents.some((entry) => entry.mode === 'learn' && entry.active === true
      && entry.file === 'demo'));
  completeBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('知识补全可关闭', completeBtn.getAttribute('aria-pressed') === 'false');
  check('发送按钮使用纸飞机图标', !!doc.querySelector('#rwSend svg'));
  check('键盘提示保留在无障碍描述中', doc.getElementById('rwComposerHint').textContent.includes('Shift+Enter'));

  console.log('[D2] 追问模式同步个性化偏好');
  let probeStartPayload = null;
  win.fetch = async function (url, opts) {
    if (String(url) === '/api/review/start') {
      probeStartPayload = JSON.parse((opts && opts.body) || '{}');
      return {
        ok: true, status: 200,
        json: async () => ({
          session_id: 'rw_prefs', done: false,
          current: { title: '第一章 测试', path: ['第一章 测试'], turn_count: 0, hint_count: 0 },
          counters: { turn: 0, turn_max: 6, hint: 0, hint_max: 2 },
          units: [], messages: [{ role: 'guide', text: '先说说什么？', kind: 'question' }],
          report: null
        })
      };
    }
    return originalFetch(url, opts);
  };
  win.ReviewWidget.startChapter('第一章 测试');
  await tick(120);
  win.fetch = originalFetch;
  check('追问开始请求携带当前对话偏好', !!probeStartPayload
    && probeStartPayload.prefs
    && probeStartPayload.prefs.replyStyle === 'detailed'
    && probeStartPayload.prefs.thinkingEffort === 'deep'
    && probeStartPayload.prefs.language === 'bilingual'
    && probeStartPayload.prefs.customInstruction === '多举例子',
    JSON.stringify(probeStartPayload && probeStartPayload.prefs));
  check('追问模式首题出现后发出对话活跃状态',
    conversationEvents.some((entry) => entry.mode === 'probe' && entry.active === true));

  console.log('[D3] 考点讲解可隐藏 / 显示');
  const revealSnapshot = {
    session_id: 'rw_prefs', done: false,
    current: { title: '第一章 测试', path: ['第一章 测试'], turn_count: 0, hint_count: 0 },
    counters: { turn: 0, turn_max: 6, hint: 0, hint_max: 2 }, units: [],
    messages: [
      { role: 'guide', text: '先说说你的理解？', kind: 'question', ts: 1 },
      { role: 'learner', text: '直接告诉我答案', kind: 'text', ts: 2 },
      { role: 'system', text: '**考点内容**：这是需要暂时隐藏的讲解。', kind: 'reveal', ts: 123456 }
    ],
    report: null
  };
  let finishRevealStream = null;
  const enc = new TextEncoder();
  const firstRevealEvent = JSON.stringify({
    t: 'd', text: '**考点内容**：正在流式输出。', kind: 'reveal'
  }) + '\n';
  const revealSnapEvent = JSON.stringify({ t: 'snap', data: revealSnapshot }) + '\n';
  win.fetch = async function (url, opts) {
    if (String(url) === '/api/review/answer') {
      let reads = 0;
      return {
        ok: true, status: 200,
        body: { getReader: function () { return { read: function () {
          reads++;
          if (reads === 1) return Promise.resolve({ done: false, value: enc.encode(firstRevealEvent) });
          if (reads === 2) return new Promise(function (resolve) {
            finishRevealStream = function () {
              resolve({ done: false, value: enc.encode(revealSnapEvent) });
            };
          });
          return Promise.resolve({ done: true });
        } }; } },
        json: async () => revealSnapshot
      };
    }
    return originalFetch(url, opts);
  };
  input.value = '直接告诉我答案';
  input.dispatchEvent(new win.Event('input', { bubbles: true }));
  doc.getElementById('rwSend').click();
  await tick(80);
  const streamReveal = doc.querySelector('#rwStream.reveal');
  check('考点气泡从第一个流式 token 起就使用正确样式',
    !!streamReveal && streamReveal.textContent.includes('正在流式输出'));
  if (finishRevealStream) finishRevealStream();
  await tick(160);
  win.fetch = originalFetch;
  check('快照到达后流式气泡收口为可折叠考点框', !doc.querySelector('#rwStream')
    && !!doc.querySelector('.rw-msg.reveal .rw-reveal-toggle'));
  let revealToggle = doc.querySelector('.rw-reveal-toggle');
  check('考点讲解气泡带隐藏 / 显示图标', !!revealToggle
    && revealToggle.getAttribute('aria-expanded') === 'true'
    && !!revealToggle.querySelector('.rw-reveal-icon svg'));
  revealToggle.click();
  check('点击后隐藏考点内容并显示占位提示',
    doc.querySelector('.rw-reveal-content').hidden === true
      && doc.querySelector('.rw-reveal-note').hidden === false
      && doc.querySelector('.rw-reveal-toggle').getAttribute('aria-expanded') === 'false');
  revealToggle = doc.querySelector('.rw-reveal-toggle');
  revealToggle.click();
  check('再次点击恢复考点内容',
    doc.querySelector('.rw-reveal-content').hidden === false
      && doc.querySelector('.rw-reveal-note').hidden === true
      && revealToggle.getAttribute('aria-expanded') === 'true');
  revealToggle.click();
  win.ReviewWidget.setMode('learn');
  win.ReviewWidget.setMode('probe');
  await tick(60);
  check('隐藏状态在消息重新渲染后保持',
    doc.querySelector('.rw-reveal-content').hidden === true
      && doc.querySelector('.rw-reveal-note').hidden === false);

  console.log('[E] 运行时');
  check('无 jsdom 运行时异常', errors.length === 0, errors.slice(0, 2).join(' ; '));
  console.log(`\n${'='.repeat(44)}\nPASS: ${pass}  FAIL: ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('HARNESS ERROR:', e); process.exit(2); });
