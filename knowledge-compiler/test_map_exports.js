/*
 * map_exports.js — handout / Markdown / Anki / PNG routing.
 * Uses common.js + a fake KTMap; no engine/layout.
 *
 * Run: JSDOM_PATH=/path/to/jsdom node test_map_exports.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const PROJ = __dirname;
const COMMON = fs.readFileSync(path.join(PROJ, 'ui/common.js'), 'utf8');
const EXPORTS = fs.readFileSync(path.join(PROJ, 'ui/map/map_exports.js'), 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}

const dom = new JSDOM('<!doctype html><html><body><div id="toast"></div></body></html>', {
  url: 'http://localhost/ui/tree_mindmap.html?file=test_tree.json',
  runScripts: 'outside-only',
  pretendToBeVisual: true
});
const win = dom.window;
win.eval(COMMON);

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
        summary: '发展概述。第二句。',
        keywords: [{ term: '发展', context: '概念' }, '关键期'],
        exam_points: [{ point: '发展阶段', type: '简答', frequency: '高频' }],
        content: '正文第一段\n<table><tr><td>表格甲</td></tr></table>',
        tables: ['<table><tr><td>表格乙</td></tr></table>'],
        captions: [{ label: '图1', text: '说明' }],
        mermaid: 'graph LR',
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

let selectedId = '';
let pngCalls = 0;
const chapter = TREE.children[0].children[0];
const map = {
  getDisplayTree() { return TREE; },
  getSelectedId() { return selectedId; },
  getExportTree(id) {
    return id === chapter.title ? chapter : TREE;
  },
  getNode(id) { return id === chapter.title ? chapter : null; },
  getState() { return { key: 'test_tree.json' }; },
  exportPNG() { pngCalls++; return Promise.resolve('data:image/png;base64,AAAA'); }
};
win.KTMap = map;
win.eval(EXPORTS);

const ex = win.KTMapExports;

console.log('\n[map exports]');
check('page book title', ex.pageBookTitle() === '教心');

const handout = ex.buildHandoutHTML(TREE, '教心', true);
check('handout includes summary', handout.includes('发展概述'));
check('handout includes exam point', handout.includes('发展阶段'));
check('handout includes guide', handout.includes('本章导学'));
check('handout includes study path', handout.includes('推荐学习路径'));
check('handout renders inline table', handout.includes('表格甲') && handout.includes('<td>'));
check('handout renders tables field', handout.includes('表格乙'));
check('handout keeps formula hooks', handout.includes('katex'));

const md = ex.buildOutlineMarkdown(TREE, '教心');
check('markdown starts with title', md.startsWith('# 教心'));
check('markdown has chapter heading', md.includes('## 第一章 心理发展'));
check('markdown has summary annotation', md.includes('> 摘要：发展概述'));
check('markdown has exam annotation', md.includes('> 考点（高频 · 简答）：发展阶段'));
check('markdown handles object keywords', md.includes('> 关键词：发展、关键期') && !md.includes('[object Object]'));

const anki = ex.buildAnkiCSV(TREE, '教心');
check('anki header', anki.csv.startsWith('"Front","Back","Tags"'));
check('anki chapter card', anki.csv.includes('【教心】第一章 心理发展'));
check('anki child list', anki.csv.includes('1. 第一节 规律') && anki.csv.includes('2. 第二节 认知'));
check('anki exam point', anki.csv.includes('考点（高频 · 简答）：发展阶段'));
check('anki tag', anki.csv.includes('333::教心'));
check('anki card count', anki.count >= 1);

selectedId = chapter.title;
check('current export title follows selection', ex.currentExportTitle() === '第一章 心理发展');
const selectedHandout = ex.buildHandoutHTML(map.getExportTree(selectedId), ex.currentExportTitle(), false);
check('selected handout uses chapter', selectedHandout.includes('第一章 心理发展') && !selectedHandout.includes('第二章 学习理论'));

let opened = '';
win.open = () => ({
  document: { open() {}, write(html) { opened += html; }, close() {} },
  focus() {}, print() {}, onload: null
});
ex.exportModuleHandout();
check('handout export opens print window', opened.includes('第一章 心理发展'));

ex.exportPNG();
check('PNG export delegates to KTMap', pngCalls === 1);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
