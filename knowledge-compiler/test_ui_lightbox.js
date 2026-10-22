/* Image lightbox regression (jsdom). */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');
const COMMON = fs.readFileSync(path.join(__dirname, 'ui/common.js'), 'utf8');
let pass = 0, fail = 0;
const errors = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => {
  if (/Could not load|not implemented|Not implemented/.test(e.message)) return;
  errors.push(e.message);
});
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/ui/test_lightbox.html', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc
});
const win = dom.window;
const doc = win.document;
win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
win.eval(COMMON);

win.KT.openImageLightbox('data:image/png;base64,iVBORw0KGgo=', '示例图片');
const box = doc.querySelector('.kt-image-lightbox');
check('打开图片灯箱', !!box && box.querySelector('img').getAttribute('alt') === '示例图片');
check('初始缩放 100%', box.querySelector('.kt-image-scale').textContent === '100%');
box.querySelector('[data-ktimg="in"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
check('放大按钮生效', box.querySelector('.kt-image-scale').textContent === '125%');
box.querySelector('[data-ktimg="reset"]').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
check('重置缩放生效', box.querySelector('.kt-image-scale').textContent === '100%');
doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
check('Escape 关闭灯箱', !doc.querySelector('.kt-image-lightbox'));
check('无 jsdom 运行时异常', errors.length === 0, errors.slice(0, 2).join(' ; '));
console.log(`\n${'='.repeat(44)}\nPASS: ${pass}  FAIL: ${fail}`);
process.exit(fail ? 1 : 0);
