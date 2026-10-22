/*
 * client.html 场景壳层测试（jsdom）
 * 覆盖：场景 Bar 路由、首页数据、资料卡片、活动资料上下文、AI 对话挂载、设置抽屉。
 * 运行：JSDOM_PATH=/path/to/jsdom node knowledge-compiler/test_ui_client.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/client.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const CLIENT = fs.readFileSync(path.join(PROJ, 'ui/client.js'), 'utf8');
const CLIENT_CSS = fs.readFileSync(path.join(PROJ, 'ui/client.css'), 'utf8');
const DEMO_DIR = path.join(PROJ, 'data/tree_parts_enhanced_fixed');
const DEMO_DOCS = fs.readdirSync(DEMO_DIR)
  .filter((name) => /^demo_.*\.json$/.test(name))
  .map((name) => JSON.parse(fs.readFileSync(path.join(DEMO_DIR, name), 'utf8')));
const DEMO_NODES = [];
DEMO_DOCS.forEach((doc) => {
  (function walk(node) {
    DEMO_NODES.push(node);
    (node.children || []).forEach(walk);
  })(doc);
});


let pass = 0;
let fail = 0;
const runtimeErrors = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms || 55));

const vc = new VirtualConsole();
vc.on('jsdomError', (error) => {
  if (/Could not load|not implemented|Not implemented/.test(error.message)) return;
  runtimeErrors.push(error.message);
});

let statusCalls = 0;
let parsedFileAdded = false;
let favoriteKeys = ['memory.json'];
const NEW_TREE = { title: '新资料', children: [{ title: '第一章', level: 1, children: [ { title: '第一节', level: 2, children: [] }, { title: '第二节', level: 2, children: [] } ] }] };
const PREVIEW_TREE = { title: '学习科学导论', children: [{ title: '第一章 记忆与注意', level: 1, children: [{ title: '第一节 工作记忆', level: 2, children: [] }] }, { title: '第二章 练习与迁移', level: 1, children: [{ title: '第一节 提取练习', level: 2, children: [] }] }] };

const FILES = [
  {
    filename: 'task_demo001',
    task_id: 'demo001',
    source: 'task',
    size: 148000,
    modified: new Date().toISOString(),
    original_files: ['学习科学导论.pdf'],
    node_count: 128
  },
  {
    filename: 'memory.json',
    source: 'enhanced',
    size: 42000,
    modified: new Date(Date.now() - 86400000).toISOString(),
    original_files: [],
    node_count: 42
  }
];

const dom = new JSDOM(HTML, {
  url: 'http://localhost/ui/client.html#home',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole: vc
});
const win = dom.window;
const doc = win.document;

win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
win.scrollTo = () => {};
win.open = () => null;
win.HTMLElement.prototype.scrollIntoView = function () {};
win.fetch = async (url, options) => {
  const target = String(url);
  let data;
  if (target.includes('/api/upload')) data = { task_id: 'new1', filenames: ['demo.txt'] };
  else if (target.includes('/api/status/new1')) { statusCalls++; data = statusCalls < 2 ? { status: 'processing', stage: 'building_tree', progress: 55, messages: ['正在构建章节'] } : { status: 'done', stage: 'complete', progress: 100, messages: ['完成'] }; }
  else if (target.includes('/api/file/task_demo001')) data = PREVIEW_TREE;
  else if (target.includes('/api/result/new1')) { parsedFileAdded = true; data = NEW_TREE; }
  else if (target.includes('/api/report/new1')) data = { nodes: { total: 4, modules: 1, exam_points: 2, enhanced: 2, coverage_pct: 50 }, tokens: { total_tokens: 234 }, files: { count: 1 }, duration_sec: 12 };
  else if (target.includes('/api/stats')) data = { tree_count: 2, node_count: 170 };
  else if (target.includes('/api/files')) data = { files: parsedFileAdded ? FILES.concat([{ filename: 'task_new1', task_id: 'new1', source: 'task', size: 100, modified: new Date().toISOString(), original_files: ['demo.txt'], node_count: 4 }]) : FILES };
  else if (target.includes('/api/tasks')) data = { tasks: [{ task_id: 'run002', filename: '认知负荷.txt', status: 'processing', modified: new Date().toISOString() }] };
  else if (target.includes('/api/favorites')) {
    if (options && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      if (body.favorite) {
        if (!favoriteKeys.includes(body.key)) favoriteKeys.push(body.key);
      } else {
        favoriteKeys = favoriteKeys.filter((key) => key !== body.key);
      }
    }
    data = { keys: favoriteKeys.slice() };
  }
  else if (target.includes('/api/settings')) data = { providers: [], active_id: '', subject: { name: '教育学', exam: '333' }, mineru: { has_token: false }, builtin: { chat: false, chat_name: '', mineru: false }, env: {}, enhance: { label: '标准' } };
  else data = {};
  return { ok: true, status: 200, json: async () => data };
};

const mounted = [];
win.ReviewWidget = {
  mount(opts) { mounted.push({ type: 'mount', opts }); },
  open(silent) { mounted.push({ type: 'open', silent }); },
  ask(text) { mounted.push({ type: 'ask', text }); },
  startChapter(chapter, scope) { mounted.push({ type: 'startChapter', chapter, scope }); },
  setComposerMeta(model, effort) { mounted.push({ type: 'meta', model, effort }); },
  setMentions(items) { mounted.push({ type: 'mentions', items }); },
  setPreferences(prefs) { mounted.push({ type: 'prefs', prefs: Object.assign({}, prefs) }); },
  setMode(mode) { this._mode = mode; mounted.push({ type: 'mode', mode }); },
  getMode() { return this._mode || 'probe'; },
  close() { mounted.push({ type: 'close' }); }
};

win.eval(COMMON);
win.eval(CLIENT);
doc.dispatchEvent(new win.Event('DOMContentLoaded'));

(async function run() {
  await tick(120);

  console.log('[A] 场景 Bar 与首页');
  check('五个场景入口存在', doc.querySelectorAll('.scene-tab').length === 5);
  check('默认停在首页', doc.body.getAttribute('data-scene') === 'home' && win.KTClient.getState().scene === 'home');
  check('首页统计来自 API', doc.getElementById('metricTrees').textContent === '2' && doc.getElementById('metricNodes').textContent === '170');
  check('首页最近资料已渲染', doc.getElementById('homeRecentList').textContent.includes('学习科学导论.pdf'));
  check('进行中的任务被计入', doc.getElementById('metricTasks').textContent === '1');
  check('品牌名称为 AnythingMindmap', doc.getElementById('homeTitle') && doc.title.includes('AnythingMindmap'));
  check('首页使用新标题和双按钮布局', doc.getElementById('homeTitle').textContent.trim() === '构建你的导图' && doc.querySelectorAll('.home-actions .btn').length === 2);
  check('首页刷新时内容错峰淡入',
    /@keyframes\s+homeElementIn/.test(CLIENT_CSS)
      && /\.scene-home\.is-active \.hero-copy \.eyebrow,[\s\S]*?animation:\s*homeElementIn/.test(CLIENT_CSS)
      && /\.scene-home\.is-active \.hero-copy h1\s*\{[^}]*animation-delay:\s*\.10s/.test(CLIENT_CSS)
      && /\.scene-home\.is-active \.home-actions\s*\{[^}]*animation-delay:\s*\.24s/.test(CLIENT_CSS)
      && /filter:\s*blur\(1\.5px\)/.test(CLIENT_CSS),
    'home reveal CSS missing');
  check('首页知识工作台默认收起', !doc.getElementById('homeDashboard').classList.contains('is-open') && doc.getElementById('homeDashboard').getAttribute('aria-hidden') === 'true');
  check('概况抽屉位于场景外层，避免 fixed 定位偏移', !doc.querySelector('.scene-home #homeDashboard') && doc.getElementById('homeDashboard').parentElement.classList.contains('client-shell'));
  doc.getElementById('homeDeskBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(60);
  check('工作台按钮从右侧打开抽屉', doc.getElementById('homeDashboard').classList.contains('is-open') && doc.getElementById('homeDeskBtn').getAttribute('aria-expanded') === 'true' && doc.getElementById('homeDeskBackdrop').hidden === false);
  doc.getElementById('closeHomeDeskBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(60);
  check('工作台抽屉可关闭并恢复无障碍状态', !doc.getElementById('homeDashboard').classList.contains('is-open') && doc.getElementById('homeDashboard').getAttribute('aria-hidden') === 'true');

  console.log('[B] 场景切换');
  let sceneTransitionCalls = 0;
  Object.defineProperty(doc, 'startViewTransition', { configurable: true, value: () => { sceneTransitionCalls++; } });
  doc.querySelector('.scene-tab[data-scene-link="parse"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(160);
  check('点击解析后切到解析场景', win.KTClient.getState().scene === 'parse' && win.location.hash === '#parse');
  check('场景切换不中断 Bar 滑块 CSS 动画', sceneTransitionCalls === 0);
  check('解析场景可见', !doc.querySelector('[data-scene-view="parse"]').hidden);
  check('配置摘要正确显示未配置降级',
    doc.getElementById('parseConfigText').textContent.includes('模型服务：未配置')
      && doc.getElementById('parseConfigText').textContent.includes('MinerU：本地回退'));
  check('解析按钮初始禁用', doc.getElementById('startParseBtn').disabled === true);
  check('解析页不再显示本次覆盖区域', !doc.getElementById('parseOverride') && !doc.getElementById('deepseekKey'));
  check('解析页不再宣传 50MB 单文件限制',
    !doc.querySelector('[data-scene-view="parse"]').textContent.includes('50MB'));

  win.KTClient.navigate('library');
  await tick(160);
  check('资料场景可见', !doc.querySelector('[data-scene-view="library"]').hidden);
  check('资料卡片包含两棵已完成树和一个进行中任务', doc.querySelectorAll('#libraryGrid .resource-card').length === 3 && doc.querySelectorAll('#libraryGrid .resource-card[data-action="overview"]').length === 2);
  check('资料库摘要显示数量', doc.getElementById('librarySummary').textContent.includes('2'));
  check('资料库有导入数据包入口',
    !!doc.getElementById('importPackageBtn') && !!doc.getElementById('packageInput'));
  check('资料库有收藏夹筛选',
    !!doc.querySelector('#libraryFilters [data-filter="favorites"]'));
  let favoriteCard = doc.querySelector('#libraryGrid [data-resource-key="memory.json"]');
  check('收藏资料显示星标', !!favoriteCard && !!favoriteCard.querySelector('.library-favorite.is-favorite'));
  check('资料卡提供导出与删除入口',
    !!favoriteCard.querySelector('[data-action="export"]')
      && !!favoriteCard.querySelector('[data-action="delete"]'));

  const railFavorites = doc.querySelector('[data-rail-action="library-favorites"]');
  check('左侧收藏夹入口存在', !!railFavorites);
  railFavorites.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(120);
  check('左侧收藏夹入口切换到收藏筛选',
    doc.querySelector('#libraryFilters [data-filter="favorites"]').classList.contains('is-active')
      && doc.querySelectorAll('#libraryGrid .resource-card').length === 1
      && doc.querySelector('#libraryGrid [data-resource-key="memory.json"]') !== null);

  doc.querySelector('#libraryGrid .library-favorite').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(80);
  check('取消收藏后从收藏夹移除',
    doc.querySelectorAll('#libraryGrid .resource-card').length === 0
      && doc.getElementById('libraryEmpty').textContent.includes('还没有收藏资料'));

  doc.querySelector('#libraryFilters [data-filter="all"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(80);
  check('返回全部资料数量恢复', doc.querySelectorAll('#libraryGrid .resource-card').length === 3);

  const sortSelect = doc.getElementById('librarySort');
  sortSelect.value = 'custom';
  sortSelect.dispatchEvent(new win.Event('change', { bubbles: true }));
  await tick(100);
  const firstDraggable = doc.querySelector('#libraryGrid .resource-card[data-action="overview"]');
  const firstKey = firstDraggable.getAttribute('data-resource-key');
  check('手动排序启用拖拽卡片', firstDraggable.getAttribute('draggable') === 'true' && firstDraggable.classList.contains('is-draggable'));
  firstDraggable.focus();
  firstDraggable.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true }));
  await tick(120);
  check('Alt+方向键可键盘重排', win.KTClient.getState().libraryOrder[0] !== firstKey);
  sortSelect.value = 'modified';
  sortSelect.dispatchEvent(new win.Event('change', { bubbles: true }));
  await tick(100);

  console.log('[C] 活动资料与跨场景上下文');
  const firstCard = doc.querySelector('#libraryGrid .resource-card[data-action="overview"]');
  firstCard.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(180);
  check('打开资料后进入导图场景', win.KTClient.getState().scene === 'mindmap');
  check('Hash 带当前资料', win.location.hash.includes('mindmap?file='));
  check('当前资料上下文更新', doc.getElementById('docContextName').textContent.includes('学习科学导论.pdf'));
  check('预览标题更新', doc.getElementById('previewTitle').textContent.includes('学习科学导论'));
  check('导览 iframe 地址带 embed=1', doc.getElementById('previewFrame').src.includes('overview.html') && doc.getElementById('previewFrame').src.includes('embed=1'));

  doc.querySelector('[data-preview-mode="canvas"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(120);
  check('切换到导图画布', doc.getElementById('previewFrame').src.includes('tree_mindmap.html') && doc.getElementById('previewFrame').src.includes('embed=1'));
  await tick(160);
  check('画布模式显示资料侧栏', !doc.getElementById('previewSplit').classList.contains('is-overview'));
  check('侧栏加载章节列表', doc.querySelectorAll('#previewChapterList [data-preview-chapter]').length === 2 && doc.getElementById('previewSideDoc').textContent.includes('学习科学导论'));
  const chapterBtn = doc.querySelector('#previewChapterList [data-preview-chapter]');
  chapterBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(120);
  check('章节跳转写入预览状态与 iframe URL', win.KTClient.getState().previewChapter === '第一章 记忆与注意' && doc.getElementById('previewFrame').src.includes('chapter=') && win.location.hash.includes('chapter='));
  const ratioBefore = win.KTClient.getState().previewSplitRatio;
  doc.getElementById('previewResizer').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  await tick(60);
  check('侧栏支持键盘调宽', win.KTClient.getState().previewSplitRatio > ratioBefore);
  doc.getElementById('sidepaneCollapseBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('侧栏可收起并露出恢复按钮', doc.getElementById('previewSplit').classList.contains('side-collapsed') && doc.getElementById('previewSideExpand').hidden === false);
  doc.getElementById('previewSideExpand').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('侧栏可恢复', !doc.getElementById('previewSplit').classList.contains('side-collapsed'));

  win.KTClient.navigate('chat');
  await tick(160);
  check('AI 对话场景可见', !doc.querySelector('[data-scene-view="chat"]').hidden);
  check('AI 对话已挂载当前资料', mounted.some((entry) => entry.type === 'mount' && entry.opts && entry.opts.file === 'task_demo001'));
  check('AI 对话已打开', mounted.some((entry) => entry.type === 'open'));
  check('AI Composer 模型与档位同步', mounted.some((entry) => entry.type === 'meta' && entry.model && entry.effort === '标准'));
  check('章节引用同步到 Composer', mounted.some((entry) => entry.type === 'mentions' && entry.items.some((item) => item.label.includes('第一章'))));
  const railKbTrigger = doc.querySelector('#workspaceRail [data-rail-action="chat-switch-doc"]');
  check('左侧当前知识库是可点击切换入口', !!railKbTrigger
    && railKbTrigger.getAttribute('aria-controls') === 'railKbPicker'
    && railKbTrigger.getAttribute('aria-expanded') === 'false'
    && doc.getElementById('railKbPicker').hidden === true);
  railKbTrigger.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('点击当前知识库展开知识库列表', doc.getElementById('railKbPicker').hidden === false
    && railKbTrigger.getAttribute('aria-expanded') === 'true'
    && doc.querySelectorAll('#railKbPicker [data-rail-kb-key]').length === 2);
  railKbTrigger.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('再次点击当前知识库可收起列表', doc.getElementById('railKbPicker').hidden === true);
  railKbTrigger.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  const otherDoc = Array.from(doc.querySelectorAll('#railKbPicker [data-rail-kb-key]'))
    .find((el) => el.getAttribute('aria-current') !== 'true');
  otherDoc.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(180);
  check('选择知识库后立即切换当前资料', win.KTClient.getState().activeFile === decodeURIComponent(otherDoc.getAttribute('data-rail-kb-key'))
    && doc.getElementById('railKbPicker').hidden === true);
  const railKbTrigger2 = doc.querySelector('#workspaceRail [data-rail-action="chat-switch-doc"]');
  railKbTrigger2.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  const originalDoc = doc.querySelector('#railKbPicker [data-rail-kb-key="' + encodeURIComponent('task_demo001') + '"]');
  originalDoc.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(180);
  check('可再次切回原知识库', win.KTClient.getState().activeFile === 'task_demo001');
  check('模型上下文环形仪表已渲染', doc.getElementById('chatGaugePct').textContent.endsWith('%') && doc.getElementById('chatTokenCaption').textContent.includes('tokens'));
  check('对话偏好同步到 ReviewWidget', mounted.some((entry) => entry.type === 'prefs'
    && entry.prefs.replyStyle === 'auto' && entry.prefs.thinkingEffort === 'standard'));
  check('对话助手栏打开右侧对话设置', (() => {
    const settingsAction = doc.querySelector('[data-rail-action="chat-settings"]');
    settingsAction.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    return doc.getElementById('chatSettingsPanel').hidden === false
      && doc.querySelector('[data-chat-panel="settings"]').classList.contains('is-active');
  })());
  doc.querySelector('[data-chat-pref="replyStyle"][data-value="detailed"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  doc.querySelector('[data-chat-pref="thinkingEffort"][data-value="deep"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  doc.querySelector('[data-chat-pref="citationStyle"][data-value="strong"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  doc.querySelector('[data-chat-pref="language"][data-value="bilingual"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('回答偏好可选择并立即同步', mounted.some((entry) => entry.type === 'prefs'
    && entry.prefs.replyStyle === 'detailed'
    && entry.prefs.thinkingEffort === 'deep'
    && entry.prefs.citationStyle === 'strong'
    && entry.prefs.language === 'bilingual')
    && doc.getElementById('chatReplyStyleValue').textContent === '详细'
    && doc.getElementById('chatEffort').textContent === '深入');
  const customInstruction = doc.getElementById('chatCustomInstruction');
  customInstruction.value = '多举日常例子';
  customInstruction.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick(360);
  check('自定义指令持久化并同步', JSON.parse(win.localStorage.getItem('kt-chat-prefs')).customInstruction === '多举日常例子'
    && mounted.some((entry) => entry.type === 'prefs' && entry.prefs.customInstruction === '多举日常例子'));
  doc.getElementById('chatPrefsReset').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('回答偏好可恢复默认', doc.getElementById('chatReplyStyleValue').textContent === '自动'
    && doc.getElementById('chatThinkingEffortValue').textContent === '标准'
    && doc.getElementById('chatCustomInstruction').value === '');
  const chatHero = doc.getElementById('chatHero');
  check('空会话时欢迎词可见', !!chatHero && chatHero.hidden === false);
  doc.dispatchEvent(new win.CustomEvent('kt-review-conversation', {
    detail: { active: true, mode: 'probe', file: 'task_demo001' }
  }));
  check('开始对话或已有内容后欢迎词收起', chatHero.hidden === true
    && chatHero.getAttribute('aria-hidden') === 'true');
  doc.dispatchEvent(new win.CustomEvent('kt-review-conversation', {
    detail: { active: false, mode: 'probe', file: 'task_demo001' }
  }));
  check('空会话恢复时欢迎词重新出现', chatHero.hidden === false
    && chatHero.getAttribute('aria-hidden') === 'false');
  const railStatus = doc.getElementById('railChatStatus');
  check('对话助手栏显示简略 AI 状态', !!railStatus
    && doc.getElementById('railChatStatusTitle').textContent.includes('已就绪')
    && !doc.getElementById('rwProcess'));
  doc.dispatchEvent(new win.CustomEvent('kt-review-activity', {
    detail: { phase: 'thinking', detail: '正在检索原文并组织回答…', mode: 'learn', file: 'task_demo001' }
  }));
  check('思考阶段同步到左栏状态',
    railStatus.classList.contains('is-thinking')
      && doc.getElementById('railChatStatusTitle').textContent.includes('检索原文'));
  doc.dispatchEvent(new win.CustomEvent('kt-review-activity', {
    detail: { phase: 'done', detail: '已生成带出处的回答', mode: 'learn', file: 'task_demo001' }
  }));
  check('完成阶段同步到左栏状态',
    railStatus.classList.contains('is-done')
      && doc.getElementById('railChatStatusTitle').textContent.includes('回答已完成'));
  const railLearnMode = doc.querySelector('#workspaceRail [data-chat-mode="learn"]');
  const railProbeMode = doc.querySelector('#workspaceRail [data-chat-mode="probe"]');
  check('对话助手栏包含学习方式切换', !!railLearnMode && !!railProbeMode
    && !doc.getElementById('chatModeSeg') && !doc.getElementById('chatModeBadge'));
  railLearnMode.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('学习模式从对话助手栏切换',
    mounted.some((entry) => entry.type === 'mode' && entry.mode === 'learn')
      && railLearnMode.classList.contains('is-active')
      && railLearnMode.getAttribute('aria-pressed') === 'true'
      && doc.getElementById('railChatModeHint').textContent.includes('知识补全'));
  doc.dispatchEvent(new win.CustomEvent('kt-review-mode', { detail: { mode: 'probe' } }));
  check('模型侧模式变化会同步对话助手栏',
    railProbeMode.classList.contains('is-active')
      && railLearnMode.getAttribute('aria-pressed') === 'false'
      && doc.getElementById('railChatModeHint').textContent.includes('逐层追问'));
  check('对话左右栏均有折叠入口', !!doc.querySelector('[data-rail-action="chat-rail-collapse"]') && !!doc.getElementById('chatInsightsCollapseBtn'));
  doc.querySelector('[data-rail-action="chat-rail-collapse"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(40);
  check('对话左栏可折叠并显示展开按钮', doc.getElementById('workspaceRail').classList.contains('is-collapsed') && doc.getElementById('chatRailExpandBtn').hidden === false);
  doc.getElementById('chatRailExpandBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(40);
  check('对话左栏可恢复', !doc.getElementById('workspaceRail').classList.contains('is-collapsed') && doc.getElementById('chatRailExpandBtn').hidden === true);
  doc.getElementById('chatInsightsCollapseBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(40);
  check('对话右栏可折叠并显示展开按钮', doc.querySelector('.chat-shell').classList.contains('is-insights-collapsed') && doc.getElementById('chatInsightsExpandBtn').hidden === false);
  doc.getElementById('chatInsightsExpandBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(40);
  check('对话右栏可恢复', !doc.querySelector('.chat-shell').classList.contains('is-insights-collapsed') && doc.getElementById('chatInsightsExpandBtn').hidden === true);
  const relatedToggle = doc.getElementById('chatRelatedToggle');
  const relatedList = doc.getElementById('chatRelated');
  check('相关知识点默认展开', relatedToggle.getAttribute('aria-expanded') === 'true' && relatedList.hidden === false);
  relatedToggle.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('相关知识点可收起', relatedToggle.getAttribute('aria-expanded') === 'false' && relatedList.hidden === true);
  relatedToggle.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('相关知识点可再次展开', relatedToggle.getAttribute('aria-expanded') === 'true' && relatedList.hidden === false);

  console.log('[D] 首页 Start / 命令面板 / 资料浮层 / Bar 键盘漫游');
  win.KTClient.navigate('home');
  await tick(120);
  check('首页只保留 Start 与概况入口', !!doc.querySelector('.home-start-btn') && !!doc.getElementById('homeDeskBtn') && !doc.getElementById('homeAskForm'));
  doc.querySelector('.home-start-btn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(160);
  check('首页 Start 跳转解析页', win.KTClient.getState().scene === 'parse');
  win.KTClient.navigate('home');
  await tick(120);
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }));
  check('命令面板打开', doc.getElementById('commandPalette').hidden === false);
  const paletteInput = doc.getElementById('paletteInput');
  paletteInput.value = '导图';
  paletteInput.dispatchEvent(new win.Event('input', { bubbles: true }));
  const visiblePaletteItems = Array.from(doc.querySelectorAll('#paletteContent .palette-item')).filter((el) => !el.hidden);
  check('命令面板可按文字过滤', visiblePaletteItems.length === 1 && visiblePaletteItems[0].textContent.includes('导图预览'));
  paletteInput.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await tick(140);
  check('命令面板回车执行', win.KTClient.getState().scene === 'mindmap' && doc.getElementById('commandPalette').hidden === true);

  let docClickSeen = false;
  doc.getElementById('docContextBtn').addEventListener('click', () => { docClickSeen = true; });
  doc.getElementById('docContextBtn').click();
  check('当前资料浮层打开', doc.getElementById('docPopover').hidden === false,
    'state=' + win.KTClient.getState().docPopoverOpen + ' seen=' + docClickSeen + ' hidden=' + doc.getElementById('docPopover').hidden);
  check('浮层显示当前资料', doc.getElementById('docPopoverCurrent').textContent.includes('学习科学导论.pdf'));
  check('浮层导图入口可用', doc.getElementById('docPopoverMindmap').disabled === false);
  doc.getElementById('docPopoverClose').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('当前资料浮层关闭', doc.getElementById('docPopover').hidden === true);

  win.location.hash = '#home';
  win.dispatchEvent(new win.HashChangeEvent('hashchange'));
  await tick(120);
  const homeTab = doc.querySelector('.scene-tab[data-scene-link="home"]');
  homeTab.focus();
  homeTab.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  await tick(100);
  check('场景 Bar 支持方向键漫游', win.KTClient.getState().scene === 'parse', 'scene=' + win.KTClient.getState().scene + ' active=' + (doc.activeElement && doc.activeElement.getAttribute('data-scene-link')) + ' hash=' + win.location.hash);

  console.log('[E] 设置抽屉与主题');
  doc.getElementById('settingsBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('设置抽屉打开', doc.getElementById('settingsDrawer').classList.contains('is-open'));
  check('设置 iframe 指向嵌入模式', doc.getElementById('settingsFrame').src.includes('settings.html?embed=1'));
  doc.getElementById('closeSettingsBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('设置抽屉关闭', !doc.getElementById('settingsDrawer').classList.contains('is-open'));

  const beforeTheme = win.KT.getTheme();
  doc.getElementById('themeBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  check('主题可切换', win.KT.getTheme() !== beforeTheme);
  check('主题图标同步', doc.getElementById('themeBtnIcon').innerHTML.includes('svg'));
  check('首页明暗背景资源已接入', CLIENT_CSS.includes('assets/bg1.png') && CLIENT_CSS.includes('assets/bg1-dark.png'));
  check('明暗背景切换带透明度过渡', /transition:\s*opacity\s*\.58s/.test(CLIENT_CSS));
  check('图标槽默认垂直水平居中', /\[data-icon\][\s\S]{0,180}align-items:\s*center[\s\S]{0,120}justify-content:\s*center/.test(CLIENT_CSS));
  check('卡片文本规则不会覆盖图标容器布局', CLIENT_CSS.includes('.metric-card > span:not(.metric-icon)'));
  check('AI 对话不再显示重复的资料选择卡', !doc.getElementById('chatDocCard'));


  console.log('[F] 解析流程');
  win.KTClient.navigate('parse');
  await tick(150);
  const textFile = new win.File(['第一章\n第一节\n第二节'], 'demo.txt', { type: 'text/plain' });
  Object.defineProperty(doc.getElementById('fileInput'), 'files', { configurable: true, value: [textFile] });
  doc.getElementById('fileInput').dispatchEvent(new win.Event('change', { bubbles: true }));
  await tick(80);
  check('选择文件后开始按钮启用', doc.getElementById('startParseBtn').disabled === false);
  doc.getElementById('startParseBtn').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(650);
  check('提交后显示解析进度', doc.getElementById('parseProgress').hidden === false && doc.getElementById('progressPercent').textContent === '55%');
  await tick(1700);
  check('解析完成后显示结果面板', doc.getElementById('parseResult').hidden === false);
  check('结果统计为本轮知识树', doc.getElementById('resultMetrics').textContent.includes('4') && doc.getElementById('resultMetrics').textContent.includes('知识点'));
  check('解析完成自动设为当前资料', win.KTClient.getState().activeFile === 'task_new1');
  check('处理细节以折叠面板呈现', !!doc.querySelector('#resultReport details.report-accordion') && doc.getElementById('resultReport').textContent.includes('处理细节'));
  check('Token 仪表与覆盖率渲染', !!doc.querySelector('#resultReport .report-gauge-ring') && doc.getElementById('resultReport').textContent.includes('合计'));
  win.KTClient.navigate('chat');
  await tick(180);
  check('任务型资料显示精确解析用量', doc.getElementById('chatTokenCaption').textContent.includes('解析用量 234 tok'));

  console.log('[G] 公式与富内容');
  var formulaHtml = win.KT.formatContent('$$E=mc^2$$\n普通段落', [], 0, []);
  check('单行块公式不会重复渲染或死循环', formulaHtml.includes('block-formula') && formulaHtml.includes('普通段落'));
  check('保留一份人工智能功能演示资料',
    DEMO_DOCS.length === 1 && DEMO_DOCS[0].title === '人工智能基础');
  check('演示资料覆盖公式、表格与流程图', DEMO_NODES.some((n) => String(n.content || '').includes('$$'))
    && DEMO_NODES.some((n) => (n.tables || []).length > 0)
    && DEMO_NODES.some((n) => String(n.mermaid || '').trim().length > 0));

  console.log('[H] 运行时');
  check('无 jsdom 运行时异常', runtimeErrors.length === 0, runtimeErrors.slice(0, 2).join(' ; '));
  console.log(`\n${'='.repeat(44)}\nPASS: ${pass}  FAIL: ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch((error) => {
  console.error('HARNESS ERROR:', error);
  process.exit(2);
});
