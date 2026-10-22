/*
 * common.js 图片引用清理回归。
 * 运行：JSDOM_PATH=/path/to/node_modules/jsdom node test_ui_image_refs.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const COMMON = fs.readFileSync(path.join(__dirname, 'ui/common.js'), 'utf8');
let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
});
dom.window.matchMedia = function() { return { matches: false, addListener() {}, removeListener() {} }; };
dom.window.eval(COMMON);
const KT = dom.window.KT;
const HASH = 'images/5bdb1cc9cfe9135161dc538eb637e9ac6d4e640492c359cc0f5e9c916bc7eb9d.jpg';

check('KT.stripLocalImageRefs 已导出', typeof KT.stripLocalImageRefs === 'function');
const raw = '人——重视知识分子\n![](' + HASH + ')';
const clean = KT.stripLocalImageRefs(raw);
check('正文中的本地图片路径被移除', clean === '人——重视知识分子', clean);
check('formatContent 不再输出 images/', !KT.formatContent(raw, [], 0).includes('images/'), KT.formatContent(raw, [], 0));
check('formatTextBlocks 不再输出 images/', !KT.formatTextBlocks(raw, [], 'nc').includes('images/'), KT.formatTextBlocks(raw, [], 'nc'));
check('formatSummary 不再输出 images/', !KT.formatSummary('摘要 ![](' + HASH + ')', [], 0).includes('images/'), KT.formatSummary('摘要 ![](' + HASH + ')', [], 0));
check('单独一行的 hash 路径被移除', KT.stripLocalImageRefs('images/5bdb1cc9cfe9135161dc538eb637e9ac') === '');

const code = '```\n![](' + HASH + ')\n```';
check('代码围栏内路径原样保留', KT.stripLocalImageRefs(code).includes(HASH), KT.stripLocalImageRefs(code));
const remote = '远程 ![x](https://example.com/a.png)';
check('远程图片 URL 不被误删', KT.stripLocalImageRefs(remote).includes('https://example.com/a.png'), KT.stripLocalImageRefs(remote));

console.log('\n============================================');
console.log('PASS: ' + pass + '  FAIL: ' + fail);
process.exit(fail ? 1 : 0);
