/*
 * settings.html 端到端测试（jsdom + 模拟后端状态机）
 * 覆盖：初始渲染 / 预设填充 / 新增-编辑-删除-设默认 / 测试连接结果渲染 /
 *      MinerU 与学科保存 / 旧配置导入 / 密钥显示切换
 *
 * 依赖：npm i jsdom（或设 JSDOM_PATH）
 * 运行：node knowledge-compiler/test_ui_settings.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const HTML = fs.readFileSync(path.join(PROJ, 'ui/settings.html'), 'utf8');
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');

let pass = 0, fail = 0;
const jsdomErrors = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const tick = () => new Promise(r => setTimeout(r, 30));

const vc = new VirtualConsole();
vc.on('jsdomError', e => {
  if (/Could not load|not implemented|Not implemented/.test(e.message)) return;
  jsdomErrors.push(e.message);
});

const inlineMatch = HTML.match(/<script>([\s\S]*?)<\/script>/);
const HTML_STRIPPED = HTML.replace(inlineMatch[0], '');

const dom = new JSDOM(HTML_STRIPPED, {
  url: 'http://localhost/ui/settings.html',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const win = dom.window, doc = win.document;

// ── 模拟后端状态 ──
const PRESETS = [
  { name: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash' },
  { name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', model: 'deepseek/deepseek-chat' },
  { name: 'Moonshot Kimi', base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { name: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { name: '硅基流动 SiliconFlow', base_url: 'https://api.siliconflow.cn/v1', model: 'x' },
  { name: '通义/阿里云百炼', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { name: '自定义（OpenAI 兼容）', base_url: '', model: '' },
];
let srv = { providers: [], active_id: '', mineru_token: '', subject: { name: '教育学', exam: '333教育综合考研' } };
const ops = [];
let testResult = { ok: true, latency_ms: 123, model: 'm', reply: 'OK' };

function normUrl(u) {
  u = (u || '').trim().replace(/\/+$/, '');
  if (!u) return '';
  return u.endsWith('/chat/completions') ? u : u + '/chat/completions';
}
function mask(v) {
  v = (v || '').trim();
  if (!v) return '';
  return v.length <= 10 ? v.slice(0, 2) + '****' : v.slice(0, 6) + '****' + v.slice(-4);
}
// 仓库默认不提供内置凭证；[A]–[I] 覆盖「未配置 + 有旧 localStorage」路径。
// [J] 只保留兼容性检查，模拟部署方自行注入内置服务时的渲染。
let builtinOn = false;

function builtinBlock() {
  return builtinOn
    ? {
      chat: true, chat_name: 'opencode（内置）', chat_model: 'deepseek-v4.1-flash',
      chat_key_masked: 'demo****key', mineru: true, mineru_token_masked: 'demo****token',
    }
    : {
      chat: false, chat_name: '', chat_model: '',
      chat_key_masked: '', mineru: false, mineru_token_masked: '',
    };
}

function publicView() {
  const bi = builtinBlock();
  return {
    providers: srv.providers.map(p => ({
      id: p.id, name: p.name, base_url: p.base_url, model: p.model,
      has_key: !!p.api_key, key_masked: mask(p.api_key),
      verified_at: p.verified_at || 0, active: p.id === srv.active_id,
    })),
    active_id: srv.active_id,
    mineru: {
      has_token: !!srv.mineru_token || bi.mineru,
      token_masked: mask(srv.mineru_token) || bi.mineru_token_masked,
      from_env: false,
      from_builtin: !srv.mineru_token && bi.mineru,
    },
    builtin: bi,
    subject: srv.subject,
    enhance: {
      scope: srv.enhance_scope || 'standard',
      options: [
        { id: 'fast', label: '快速', desc: '只解读层级最靠前的 30 个节点；适合试跑，快而省。' },
        { id: 'standard', label: '标准', desc: '解读全部 L1–L3 节点；解读完整、成本可控（默认）。' },
        { id: 'full', label: '全量', desc: '连 L4 也一并解读；最全面，耗时与用量也最高。' },
      ],
    },
    subject_fallback: { name: '教育学', exam: '333教育综合考研' },
    env: { has_openai_key: false, key_masked: '' },
    presets: PRESETS,
  };
}
function jbody(o) { return { ok: true, status: 200, json: async () => o }; }

win.fetch = async function (url, opts) {
  url = String(url);
  if (url.includes('/api/settings')) {
    if (!opts || !opts.method || opts.method === 'GET') return jbody(publicView());
    const body = JSON.parse(opts.body);
    ops.push(body);
    if (body.op === 'upsert_provider') {
      const p = body.provider;
      let prov = p.id ? srv.providers.find(x => x.id === p.id) : null;
      if (!prov) {
        prov = { id: 'srv_' + (srv.providers.length + 1), api_key: '' };
        srv.providers.push(prov);
      }
      prov.name = p.name || '未命名';
      prov.base_url = normUrl(p.base_url);
      prov.model = p.model;
      if (p.api_key) prov.api_key = p.api_key;
      if (!srv.active_id) srv.active_id = prov.id;
      return jbody({ ok: true, id: prov.id, settings: publicView() });
    }
    if (body.op === 'set_active') { srv.active_id = body.id; return jbody({ ok: true, settings: publicView() }); }
    if (body.op === 'delete_provider') {
      srv.providers = srv.providers.filter(x => x.id !== body.id);
      if (srv.active_id === body.id) srv.active_id = srv.providers[0] ? srv.providers[0].id : '';
      return jbody({ ok: true, settings: publicView() });
    }
    if (body.op === 'set_general') {
      if (body.mineru_token) srv.mineru_token = body.mineru_token;
      if (body.clear_mineru) srv.mineru_token = '';
      if (body.subject) srv.subject = body.subject;
      return jbody({ ok: true, settings: publicView() });
    }
    if (body.op === 'set_enhance') {
      srv.enhance_scope = body.scope;
      return jbody({ ok: true, enhance: { scope: body.scope }, settings: publicView() });
    }
    if (body.op === 'test_provider') return jbody(testResult);
    if (body.op === 'test_mineru') return jbody({ ok: false, format_ok: true, error: '鉴权失败（HTTP 401）：Token 无效、已过期或已被重置' });
  }
  return { ok: false, status: 404, json: async () => ({ error: 'not found' }) };
};
win.confirm = () => true;
win.alert = m => console.log('  [alert]', m);
win.scrollTo = () => {};

// ── 注入脚本 ──
check('内联脚本可提取', !!inlineMatch);
function runScript(code) {
  const s = doc.createElement('script');
  s.textContent = code;
  doc.head.appendChild(s);
}
// 先放旧配置到 localStorage，验证"导入旧配置"提示
win.localStorage.setItem('kt-api-config', JSON.stringify({ deepseekKey: 'sk-legacy-old-key-123456', mineruToken: 'aaaa.bbbb.cccc' }));
runScript(COMMON);
runScript(inlineMatch[1]);

(async () => {
  await tick(); await tick();

  console.log('[A] 初始渲染（无服务商 + 有旧配置）');
  check('空态提示', doc.getElementById('providerList').textContent.includes('还没有配置'));
  check('旧配置导入横幅显示', doc.getElementById('legacyBanner').classList.contains('show'));
  check('预设下拉已填充', doc.getElementById('presetSelect').options.length === PRESETS.length + 1);
  check('学科字段回填', doc.getElementById('subjectName').value === '教育学'
    && doc.getElementById('subjectExam').value === '333教育综合考研');
  check('MinerU 徽章未配置', doc.getElementById('mineruBadge').textContent === '未配置');

  console.log('[B] 导入旧配置');
  win.importLegacy();
  await tick();
  check('表单被填入旧 Key', doc.getElementById('pvKey').value === 'sk-legacy-old-key-123456');
  check('MinerU 被填入', doc.getElementById('mineruInput').value === 'aaaa.bbbb.cccc');
  check('默认端点已填', doc.getElementById('pvBase').value === 'https://api.deepseek.com');
  check('横幅已隐藏', !doc.getElementById('legacyBanner').classList.contains('show'));

  console.log('[C] 保存新服务商');
  doc.getElementById('pvName').value = 'opencode';
  doc.getElementById('pvBase').value = 'https://opencode.example.com/v1';
  doc.getElementById('pvModel').value = 'gpt-5-mini';
  doc.getElementById('pvKey').value = 'sk-opencode-1234567890';
  await win.saveProvider();
  await tick();
  const upsert = ops.find(o => o.op === 'upsert_provider');
  check('POST upsert_provider 正确', upsert && upsert.provider.name === 'opencode'
    && upsert.provider.api_key === 'sk-opencode-1234567890', JSON.stringify(upsert));
  check('列表渲染新服务商', doc.getElementById('providerList').textContent.includes('opencode'));
  check('自动设为默认展示', doc.getElementById('providerList').textContent.includes('默认'));
  check('Key 脱敏回显', doc.getElementById('providerList').textContent.includes('sk-ope****7890'));
  check('表单已重置', doc.getElementById('pvName').value === '' && doc.getElementById('pvKey').value === '');

  console.log('[D] 预设填充');
  doc.getElementById('presetSelect').value = '1';   // OpenRouter
  win.applyPreset();
  check('预设填入名称与端点', doc.getElementById('pvName').value === 'OpenRouter'
    && doc.getElementById('pvBase').value === 'https://openrouter.ai/api/v1');
  doc.getElementById('presetSelect').value = '';
  win.cancelEdit();

  console.log('[E] 编辑已有服务商');
  const pid = srv.providers[0].id;
  win.editProvider(pid);
  await tick();
  check('编辑态标题', doc.getElementById('formTitle').textContent.includes('opencode'));
  check('编辑态 Key 提示脱敏', doc.getElementById('keyHint').textContent.includes('sk-ope****7890'));
  check('编辑态 Key 输入为空', doc.getElementById('pvKey').value === '');
  doc.getElementById('pvName').value = 'opencode 改名';
  await win.saveProvider();
  await tick();
  const upserts = ops.filter(o => o.op === 'upsert_provider');
  check('更新带 id 且 api_key 为空（不修改）',
    upserts[upserts.length - 1].provider.id === pid
    && upserts[upserts.length - 1].provider.api_key === '',
    JSON.stringify(upserts[upserts.length - 1]));
  check('改名生效', doc.getElementById('providerList').textContent.includes('opencode 改名'));

  console.log('[F] 测试连接');
  testResult = { ok: true, latency_ms: 88, model: 'gpt-5-mini', reply: 'OK' };
  doc.getElementById('pvBase').value = 'https://opencode.example.com/v1';
  await win.testFormProvider();
  await tick();
  check('测试成功渲染', doc.getElementById('pvTestResult').textContent.includes('连接成功')
    && doc.getElementById('pvTestResult').classList.contains('ok'));
  testResult = { ok: false, error: 'HTTP 401：Invalid API key' };
  await win.testFormProvider();
  await tick();
  check('测试失败渲染', doc.getElementById('pvTestResult').textContent.includes('HTTP 401')
    && doc.getElementById('pvTestResult').classList.contains('err'));
  // 列表里的「测试」按钮：只传 id，走已保存的 Key/端点
  testResult = { ok: true, latency_ms: 60, model: 'm' };
  await win.testSaved(pid);
  await tick();
  check('只给 id 也能测试（走已保存配置）',
    ops.some(o => o.op === 'test_provider' && o.id === pid), JSON.stringify(ops.filter(o => o.op === 'test_provider')));

  console.log('[G] 设为默认 / 删除（两个服务商）');
  doc.getElementById('pvName').value = '第二个';
  doc.getElementById('pvBase').value = 'https://second.example.com/v1';
  doc.getElementById('pvModel').value = 'm2';
  doc.getElementById('pvKey').value = 'sk-second-key-abcdef';
  await win.saveProvider();
  await tick();
  check('第二个未抢默认', srv.active_id === pid);
  const second = srv.providers.find(p => p.name === '第二个');
  await win.setActive(second.id);
  await tick();
  check('set_active 生效并重渲染', srv.active_id === second.id
    && doc.getElementById('providerList').textContent.includes('默认'));
  const removePromise = win.removeProvider(second.id);
  await tick();
  check('删除服务商先打开 CDS 确认弹窗', doc.getElementById('confirmDialog').hidden === false
    && doc.getElementById('confirmTitle').textContent.includes('删除服务商'));
  doc.getElementById('confirmOk').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await removePromise;
  await tick();
  check('确认后删除并回退默认', srv.providers.length === 1 && srv.active_id === pid,
    JSON.stringify({ n: srv.providers.length, active: srv.active_id }));

  console.log('[H] MinerU 与学科');
  doc.getElementById('mineruInput').value = 'sk-' + 'a'.repeat(48);
  await win.saveMineru();
  await tick();
  check('MinerU 保存', srv.mineru_token === 'sk-' + 'a'.repeat(48)
    && doc.getElementById('mineruBadge').textContent === '已配置');
  await win.testMineru();
  await tick();
  check('MinerU 测试失败渲染', doc.getElementById('mineruTestResult').classList.contains('err'));
  doc.getElementById('subjectName').value = '教育心理学';
  doc.getElementById('subjectExam').value = '866';
  await win.saveSubject();
  await tick();
  check('学科保存', srv.subject.name === '教育心理学' && srv.subject.exam === '866');

  console.log('[H2] 增强范围档位');
  var enhBtns = doc.querySelectorAll('#enhOpts .enh-opt');
  check('三个档位渲染（快速 / 标准 / 全量）', enhBtns.length === 3
    && doc.getElementById('enhOpts').textContent.includes('快速')
    && doc.getElementById('enhOpts').textContent.includes('标准')
    && doc.getElementById('enhOpts').textContent.includes('全量'),
    doc.getElementById('enhOpts').textContent.slice(0, 60));
  check('默认选中标准档', doc.querySelector('#enhOpts .enh-opt.on')
    && doc.querySelector('#enhOpts .enh-opt.on').getAttribute('data-scope') === 'standard');
  doc.querySelector('#enhOpts .enh-opt[data-scope="full"]').click();
  await tick();
  check('点全量 → 保存并高亮', srv.enhance_scope === 'full'
    && doc.querySelector('#enhOpts .enh-opt.on').getAttribute('data-scope') === 'full',
    'scope=' + srv.enhance_scope);
  check('请求带 set_enhance op', ops.some(function (o) {
    return o.op === 'set_enhance' && o.scope === 'full';
  }));
  check('保存后有提示', doc.getElementById('toast').textContent.includes('增强范围'));

  console.log('[I] 密钥显示切换');
  doc.getElementById('pvKey').value = 'sk-x';
  win.toggleKeyVisible();
  check('切换为明文', doc.getElementById('pvKey').type === 'text'
    && doc.getElementById('eyeBtn').textContent === '隐藏');
  win.toggleKeyVisible();
  check('切回密文', doc.getElementById('pvKey').type === 'password');

  console.log('[J] 内置凭证（builtinOn 打开，模拟后端内置了 opencode + MinerU）');
  builtinOn = true;
  srv.providers = []; srv.active_id = ''; srv.mineru_token = '';
  await win.fetchSettings();
  await tick();
  check('内置时空态提示指向内置服务',
    doc.getElementById('providerList').textContent.includes('内置模型服务'));
  check('服务商计数改为「使用内置」',
    doc.getElementById('providerCount').textContent === '使用内置');
  check('MinerU 徽章标记内置',
    doc.getElementById('mineruBadge').textContent === '已配置（内置）');
  check('MinerU 占位文案指向内置',
    doc.getElementById('mineruInput').placeholder.includes('当前用内置 Token'));
  check('内置后不再提示导入旧配置',
    !doc.getElementById('legacyBanner').classList.contains('show'));
  check('内置 Key 只以脱敏形式出现',
    doc.getElementById('providerList').textContent.includes('demo****key'));

  console.log('[K] 运行时错误');
  check('无 jsdom 运行时异常', jsdomErrors.length === 0, jsdomErrors.slice(0, 2).join(' ; '));

  console.log(`\n${'='.repeat(44)}\nPASS: ${pass}  FAIL: ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e); process.exit(2); });
