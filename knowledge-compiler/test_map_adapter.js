/*
 * map_adapter.js — pure data tests (no DOM, no engine, no network).
 * Run: node test_map_adapter.js
 */
'use strict';

const assert = require('assert');
const A = require('./ui/map/map_adapter.js');

let pass = 0;
let fail = 0;
function check(name, fn) {
  try {
    fn();
    pass++;
    console.log('  PASS ' + name);
  } catch (err) {
    fail++;
    console.log('  FAIL ' + name + ' | ' + (err && err.message || err));
  }
}
function countNodes(node) {
  return 1 + (node.children || []).reduce((sum, child) => sum + countNodes(child), 0);
}
function collect(node, out) {
  out.push(node);
  (node.children || []).forEach(child => collect(child, out));
  return out;
}
function findById(node, id) {
  if (node.id === id) return node;
  for (const child of node.children || []) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

function makeTree() {
  return {
    title: '教心',
    guide: '全书导学',
    links: [],
    children: [{
      title: '教心',
      level: 0,
      children: [
        {
          title: '第一章 心理发展',
          level: 1,
          guide: '本章导学',
          summary: '发展概述',
          keywords: [{ term: '发展', context: '概念' }],
          exam_points: [{ point: '发展阶段', type: '简答', frequency: '高频' }],
          content: '<p>正文</p><table><tr><td>a</td></tr></table>',
          tables: ['<table><tr><td>t</td></tr></table>'],
          captions: [{ label: '图1', text: '说明' }],
          mermaid: 'graph LR',
          images: ['images/x.jpg'],
          highlights: [{ text: '重点', importance: 'high', type: 'concept' }],
          children: [
            { title: '第一节 规律', level: 2, content: '规律正文', children: [] },
            { title: '第二节 认知', level: 2, children: [] }
          ]
        },
        {
          title: '第二章 学习理论',
          level: 1,
          children: [
            { title: '第三节 行为主义', level: 2, children: [] },
            { title: '第四节 认知理论', level: 2, children: [] }
          ]
        }
      ]
    }]
  };
}

console.log('\n[adapter]');

check('count and wrapper promotion', () => {
  const adapter = new A.MapAdapter(makeTree());
  assert.strictEqual(adapter.count(), 7);
  assert.strictEqual(adapter.promoted, true);
  assert.strictEqual(adapter.title, '教心');
  assert.strictEqual(adapter.displayRoot.title, '教心');
  assert.strictEqual(adapter.displayRoot.children.length, 2);
});

check('ids are valid, unique, and stable across reloads', () => {
  const a = new A.MapAdapter(makeTree());
  const b = new A.MapAdapter(makeTree());
  const ids = collect(a.displayRoot, []).map(n => n.id);
  assert.strictEqual(new Set(ids).size, ids.length);
  ids.forEach(id => assert.ok(A.validId(id), id));
  const idsB = collect(b.displayRoot, []).map(n => n.id);
  assert.deepStrictEqual(idsB, ids);
  assert.ok(ids.every(id => id.startsWith('n_')));
});

check('display path and review path follow the wrapper convention', () => {
  const adapter = new A.MapAdapter(makeTree());
  const chapter = adapter.displayRoot.children[0];
  const leaf = chapter.children[0];
  assert.deepStrictEqual(adapter.getPath(chapter.id), ['教心', '第一章 心理发展']);
  assert.deepStrictEqual(adapter.getReviewPath(chapter.id), ['教心', '第一章 心理发展']);
  assert.deepStrictEqual(adapter.getPath(leaf.id), ['教心', '第一章 心理发展', '第一节 规律']);
  assert.deepStrictEqual(adapter.getReviewPath(leaf.id), ['教心', '第一章 心理发展', '第一节 规律']);
  assert.strictEqual(adapter.branchIndexById.get(leaf.id), 0);
  assert.strictEqual(adapter.branchIndexById.get(adapter.displayRoot.children[1].id), 1);
});

check('Mind Elixir data keeps heavy fields out of metadata', () => {
  const adapter = new A.MapAdapter(makeTree());
  const data = adapter.toMindElixirData({ expandLevel: 1, layout: 'SIDE' });
  assert.strictEqual(data.nodeData.topic, '教心');
  assert.strictEqual(data.direction, 2);
  const chapter = data.nodeData.children[0];
  assert.strictEqual(chapter.topic, '第一章 心理发展');
  assert.ok(chapter.metadata && chapter.metadata.kt);
  assert.strictEqual(chapter.metadata.kt.content, undefined);
  assert.strictEqual(chapter.metadata.kt.tables, undefined);
  assert.strictEqual(chapter.metadata.kt.summary, '发展概述');
  assert.strictEqual(chapter.metadata.kt.captions[0].label, '图1');
  assert.ok(adapter.heavyById.get(chapter.id).content.includes('<table>'));
  assert.ok(adapter.heavyById.get(chapter.id).tables.length === 1);
});

check('expand level sets baseline and respects overrides', () => {
  const adapter = new A.MapAdapter(makeTree());
  const data = adapter.toMindElixirData({ expandLevel: 1, layout: 'SIDE' });
  assert.strictEqual(data.nodeData.expanded, true);
  assert.strictEqual(data.nodeData.children[0].expanded, false);
  const chapterId = data.nodeData.children[0].id;
  const data2 = adapter.toMindElixirData({
    expandLevel: 1,
    layout: 'SIDE',
    expandedOverrides: { [chapterId]: true }
  });
  assert.strictEqual(data2.nodeData.children[0].expanded, true);
});

check('balanced directions split by subtree weight', () => {
  const root = {
    id: 'r', topic: 'root', children: [
      { id: 'a', topic: 'a', children: [{ id: 'a1', topic: 'a1' }, { id: 'a2', topic: 'a2' }, { id: 'a3', topic: 'a3' }] },
      { id: 'b', topic: 'b', children: [{ id: 'b1', topic: 'b1' }] },
      { id: 'c', topic: 'c', children: [{ id: 'c1', topic: 'c1' }, { id: 'c2', topic: 'c2' }] }
    ]
  };
  A.assignBalancedDirections(root);
  assert.deepStrictEqual(root.children.map(c => c.direction), [0, 1, 1]);
  const single = { id: 'r', topic: 'root', children: [{ id: 'x', topic: 'x' }] };
  A.assignBalancedDirections(single);
  assert.strictEqual(single.children[0].direction, 1);
});

check('round-trip preserves all fields and heavy content', () => {
  const adapter = new A.MapAdapter(makeTree());
  const me = adapter.toMindElixirData({ expandLevel: 3, layout: 'SIDE' });
  const back = adapter.fromMindElixirData(me);
  assert.strictEqual(back.title, '教心');
  assert.strictEqual(back.children.length, 1);
  const chapter = back.children[0].children[0];
  assert.strictEqual(chapter.title, '第一章 心理发展');
  assert.strictEqual(chapter.summary, '发展概述');
  assert.strictEqual(chapter.content, '<p>正文</p><table><tr><td>a</td></tr></table>');
  assert.strictEqual(chapter.tables.length, 1);
  assert.strictEqual(chapter.mermaid, 'graph LR');
  assert.deepStrictEqual(chapter.images, ['images/x.jpg']);
  assert.strictEqual(chapter.exam_points[0].point, '发展阶段');
  assert.strictEqual(chapter.guide, '本章导学');
  assert.ok(chapter.id);
});

check('rename, move, add, delete are reflected in serialization', () => {
  const adapter = new A.MapAdapter(makeTree());
  const me = adapter.toMindElixirData({ expandLevel: 3, layout: 'SIDE' });
  const chapter = me.nodeData.children[0];
  chapter.topic = '第一章 改名';
  const moved = chapter.children.shift();
  me.nodeData.children[1].children.push(moved);
  me.nodeData.children[1].children.push({ id: 'new1', topic: '新节点', metadata: { kt: {} } });
  const back = adapter.fromMindElixirData(me);
  const chapters = back.children[0].children;
  assert.strictEqual(chapters[0].title, '第一章 改名');
  assert.strictEqual(chapters[0].children.length, 1);
  assert.strictEqual(chapters[1].children.length, 4);
  assert.strictEqual(chapters[1].children[3].title, '新节点');
  // delete the moved leaf
  me.nodeData.children[1].children.splice(0, 1);
  const back2 = adapter.fromMindElixirData(me);
  assert.strictEqual(back2.children[0].children[1].children.length, 3);
});

check('syncFromMindElixir updates adapter but keeps heavy fields', () => {
  const adapter = new A.MapAdapter(makeTree());
  const me = adapter.toMindElixirData({ expandLevel: 3, layout: 'SIDE' });
  const chapter = me.nodeData.children[0];
  const chapterId = chapter.id;
  chapter.topic = '第一章 改名';
  chapter.children.push({ id: 'new1', topic: '新节点', metadata: { kt: { summary: '新摘要' } } });
  adapter.syncFromMindElixir(me);
  assert.strictEqual(adapter.count(), 8);
  assert.strictEqual(adapter.getNode(chapterId).title, '第一章 改名');
  assert.strictEqual(adapter.getNode('new1').title, '新节点');
  assert.strictEqual(adapter.heavyById.get(chapterId).content, '<p>正文</p><table><tr><td>a</td></tr></table>');
  assert.deepStrictEqual(adapter.getPath('new1'), ['教心', '第一章 改名', '新节点']);
});

check('search covers title, summary, keywords, exam points and content', () => {
  const adapter = new A.MapAdapter(makeTree());
  assert.strictEqual(adapter.search('心理发展')[0].field, 'title');
  assert.strictEqual(adapter.search('发展概述')[0].field, 'summary');
  assert.strictEqual(adapter.search('发展')[0].field, 'title');
  assert.strictEqual(adapter.search('发展阶段')[0].field, 'exam');
  assert.strictEqual(adapter.search('规律正文')[0].field, 'content');
  assert.strictEqual(adapter.search('不存在的内容').length, 0);
  const limited = adapter.search('发展', 1);
  assert.strictEqual(limited.length, 1);
});

check('legacy layout names migrate', () => {
  assert.strictEqual(A.legacyLayoutName('LR_ALL'), 'RIGHT');
  assert.strictEqual(A.legacyLayoutName('RL_ALL'), 'LEFT');
  assert.strictEqual(A.legacyLayoutName('TB'), 'DOWN');
  assert.strictEqual(A.legacyLayoutName('BT'), 'DOWN');
  assert.strictEqual(A.legacyLayoutName('LR'), 'SIDE');
  assert.strictEqual(A.legacyLayoutName('RL'), 'SIDE');
  assert.strictEqual(A.legacyLayoutName('weird'), 'SIDE');
  assert.strictEqual(A.layoutDirection('RIGHT'), 1);
  assert.strictEqual(A.layoutDirection('LEFT'), 0);
  assert.strictEqual(A.layoutDirection('DOWN'), 3);
  assert.strictEqual(A.layoutDirection('SIDE'), 2);
});

check('minimap fit and viewport stay in content coordinates', () => {
  const boxes = [
    { x: 0, y: 0, w: 100, h: 20 },
    { x: 200, y: 80, w: 50, h: 20 }
  ];
  const fit = A.fitBoxesToCanvas(boxes, 200, 100, 10);
  assert.strictEqual(fit.minX, 0);
  assert.strictEqual(fit.minY, 0);
  assert.strictEqual(fit.contentW, 250);
  assert.strictEqual(fit.contentH, 100);
  assert.strictEqual(fit.scale, 0.72);
  assert.strictEqual(fit.ox, 10);
  assert.strictEqual(fit.oy, 14);

  const viewport = A.minimapViewport(
    { left: 100, top: 50, width: 400, height: 300 },
    { x: 10, y: 20, screenX: 200, screenY: 100 },
    2
  );
  assert.deepStrictEqual(viewport, { x: -40, y: -5, w: 200, h: 150 });
});

check('getExportTree returns a subtree with heavy fields', () => {
  const adapter = new A.MapAdapter(makeTree());
  const chapterId = adapter.displayRoot.children[0].id;
  const subtree = adapter.getExportTree(chapterId);
  assert.strictEqual(subtree.title, '第一章 心理发展');
  assert.ok(subtree.content.includes('<table>'));
  assert.strictEqual(subtree.children.length, 2);
});

check('non-promoted tree keeps root out of review paths', () => {
  const tree = {
    title: '根',
    children: [
      { title: '章一', children: [{ title: '节一', children: [] }] },
      { title: '章二', children: [] }
    ]
  };
  const adapter = new A.MapAdapter(tree);
  assert.strictEqual(adapter.promoted, false);
  const chapter = adapter.displayRoot.children[0];
  assert.deepStrictEqual(adapter.getPath(chapter.id), ['根', '章一']);
  assert.deepStrictEqual(adapter.getReviewPath(chapter.id), ['章一']);
  const leaf = chapter.children[0];
  assert.deepStrictEqual(adapter.getReviewPath(leaf.id), ['章一', '节一']);
});

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
