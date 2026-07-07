import { test } from 'node:test';
import assert from 'node:assert';
import { isCurrentGeneration } from '../search-generation.js';

test('token 等於 current → 是當前世代（放行）', () => {
  assert.equal(isCurrentGeneration(5, 5), true);
});

test('token 舊於 current（stale 遲到訊息）→ 不是當前世代（攔）', () => {
  assert.equal(isCurrentGeneration(4, 5), false);
});

test('token 為 null（caller 未傳 token，維持現行放行行為）→ 放行', () => {
  // 決策：null token = opt-out gate，維持既有無 gate 行為（供 chat 保守選項用）。
  assert.equal(isCurrentGeneration(null, 5), true);
});

test('token 為 undefined（未提供參數）→ 放行（向後相容）', () => {
  assert.equal(isCurrentGeneration(undefined, 5), true);
});

test('token 為 0（第一個世代，合法值不可被當 falsy 漏放）', () => {
  assert.equal(isCurrentGeneration(0, 0), true);
  assert.equal(isCurrentGeneration(0, 1), false);
});
