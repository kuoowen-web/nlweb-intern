import { test } from 'node:test';
import assert from 'node:assert';
import { lrStagesInSnapshot, lrSnapshotForStage } from '../lr-snapshot.js';

const snap = [
  { type: 'user', stage: 0, html: '<p>我的研究問題</p>', dataset: {}, ts: 1 },     // 初始提問
  { type: 'narration', stage: 1, html: '<p>建立結構</p>', dataset: {}, ts: 2 },
  { type: 'checkpoint', stage: 1, html: '<div>讀豹決定</div>', dataset: {}, ts: 3 },
  { type: 'section', stage: 5, html: '<p>第一段</p>', dataset: {}, ts: 4 },
];

test('lrStagesInSnapshot 去重升冪、stage 0 併入 1', () => {
  assert.deepEqual(lrStagesInSnapshot(snap), [1, 5]);
});

test('lrSnapshotForStage(1) 含初始提問(stage0) + stage1 對話、保序', () => {
  const s1 = lrSnapshotForStage(snap, 1);
  assert.equal(s1.length, 3);
  assert.equal(s1[0].html, '<p>我的研究問題</p>');
  assert.equal(s1[2].type, 'checkpoint');
});

test('lrSnapshotForStage 空 stage 回空', () => {
  assert.deepEqual(lrSnapshotForStage(snap, 3), []);
});

test('非陣列輸入安全降級', () => {
  assert.deepEqual(lrStagesInSnapshot(null), []);
  assert.deepEqual(lrSnapshotForStage(undefined, 1), []);
});
