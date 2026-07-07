import { test } from 'node:test';
import assert from 'node:assert';
import { snapshotHasReplayableEntries } from '../lr-snapshot.js';

test('non-empty snapshot is replayable', () => {
  const snap = [
    { type: 'user', stage: 0, html: '<p>我的研究問題</p>', dataset: {}, ts: 1 },
    { type: 'narration', stage: 1, html: '<p>建立結構</p>', dataset: {}, ts: 2 },
  ];
  assert.equal(snapshotHasReplayableEntries(snap), true);
});

test('empty array is not replayable', () => {
  assert.equal(snapshotHasReplayableEntries([]), false);
});

test('null / undefined / non-array safely not replayable', () => {
  assert.equal(snapshotHasReplayableEntries(null), false);
  assert.equal(snapshotHasReplayableEntries(undefined), false);
  assert.equal(snapshotHasReplayableEntries('nope'), false);
});

test('array of malformed entries (no html) still counts as replayable if present', () => {
  // We do NOT silently drop entries here; replay itself sanitizes empty html.
  const snap = [{ type: 'system', stage: 0, dataset: {}, ts: 1 }];
  assert.equal(snapshotHasReplayableEntries(snap), true);
});
