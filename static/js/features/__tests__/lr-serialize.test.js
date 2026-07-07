import { test } from 'node:test';
import assert from 'node:assert';
import { serializeLRChatRoot } from '../lr-snapshot.js';

function mkWrapper(type, stage, bubbleHtml, dataset = {}, content = true) {
  const bubble = { className: 'lr-msg-bubble', innerHTML: bubbleHtml };
  const ds = { lrStage: String(stage), ...dataset };
  if (content) ds.lrContent = '1';   // real-content wrappers carry data-lr-content
  return {
    className: `lr-chat-message ${type}`,
    dataset: ds,
    querySelector: (sel) => (sel === '.lr-msg-bubble' ? bubble : null),
  };
}
function mkRoot(wrappers) {
  return {
    querySelectorAll: (sel) => {
      if (sel === ':scope > .lr-chat-message') return wrappers;            // legacy arm (old tests)
      if (sel === ':scope > .lr-chat-message[data-lr-content]') {
        // emulate [data-lr-content] — keep ONLY wrappers whose dataset has lrContent set
        return wrappers.filter(w => w.dataset && w.dataset.lrContent !== undefined);
      }
      return [];
    },
  };
}
const purifyCalls = [];
const fakePurify = { sanitize: (h) => { purifyCalls.push(h); return `[clean]${h}`; } };

test('serialize 涵蓋所有 type（含 unknown/assistant），保 stage/dataset，過 purify', () => {
  purifyCalls.length = 0;
  const root = mkRoot([
    mkWrapper('narration', 1, '<p>建立結構</p>'),
    mkWrapper('checkpoint', 2, '<div class="lr-checkpoint">讀豹決定</div>'),
    mkWrapper('section', 5, '<p>第一段</p>', { lrSectionIndex: '0' }),
    mkWrapper('assistant', 6, '<em>已完成</em>'),          // H1：unknown type 不丟
  ]);
  const out = serializeLRChatRoot(root, fakePurify);
  assert.equal(out.length, 4);
  assert.equal(out[0].type, 'narration'); assert.equal(out[0].stage, 1);
  assert.equal(out[1].type, 'checkpoint'); assert.equal(out[1].stage, 2);
  assert.equal(out[1].html, '[clean]<div class="lr-checkpoint">讀豹決定</div>'); // C4：checkpoint 被 sanitize
  assert.equal(out[2].dataset.lrSectionIndex, '0');
  assert.equal(out[3].type, 'assistant');                 // 不被白名單漏掉
  assert.equal(purifyCalls.length, 4);                    // 每條都過 purify
});

test('stage 非整數 fallback 0；空 root 回空陣列', () => {
  // stage-0 fallback wrapper must carry data-lr-content to be in scope under the allow-list
  const root = mkRoot([{ className: 'lr-chat-message narration', dataset: { lrContent: '1' }, querySelector: () => ({ innerHTML: 'x' }) }]);
  assert.equal(serializeLRChatRoot(root, fakePurify)[0].stage, 0);
  assert.deepEqual(serializeLRChatRoot(null, fakePurify), []);
});

// ---------------------------------------------------------------------------
// Candidate A: positive allow-list serialize ([data-lr-content])
// ---------------------------------------------------------------------------
test('allow-list: serialize keeps data-lr-content wrappers (incl. opt-in real-proposal checkpoint), drops un-marked transient ones', () => {
  const realProposal = mkWrapper('checkpoint', 4, '<div class="lr-checkpoint-proposal">引用格式偏好？</div>');  // content=true → marked
  const transient = mkWrapper('assistant', 0, '<div>從中斷處繼續</div>', {}, false);  // NOT content → unmarked
  const root = mkRoot([
    mkWrapper('user', 0, '<p>我的研究問題</p>'),       // marked → kept (user-stage-0 must store)
    mkWrapper('narration', 1, '<p>建立結構</p>'),       // marked → kept
    transient,                                          // unmarked transient → dropped
    realProposal,                                       // marked checkpoint → kept
  ]);
  const out = serializeLRChatRoot(root, fakePurify);
  assert.equal(out.length, 3);                          // user + narration + real proposal
  assert.equal(out[0].type, 'user');                   // user-stage-0 NOT dropped
  assert.equal(out[2].type, 'checkpoint');             // real-proposal checkpoint kept
  assert.ok(out.every(e => e.type !== 'assistant'));   // unmarked transient excluded
});

test('allow-list: a root with ZERO data-lr-content wrappers serializes to [] (empty-guard precondition)', () => {
  // The terminal-restore case: a reopened completed/not_started session whose #lrChat
  // holds ONLY unmarked transient notice boxes. serialize MUST yield [] so the
  // resolveLRSnapshotForSave empty-guard preserves the real prior snapshot (no data loss).
  const a = mkWrapper('assistant', 0, '<div>此 Live 研究已完成…</div>', {}, false);
  const b = mkWrapper('system', 0, '<div>快照功能上線前建立…</div>', {}, false);
  const out = serializeLRChatRoot(mkRoot([a, b]), fakePurify);
  assert.equal(out.length, 0);                          // ALL unmarked → fresh serialize is EMPTY
});

// ---------------------------------------------------------------------------
// Task 1: empty-overwrite guard decision logic (resolveLRSnapshotForSave)
// ---------------------------------------------------------------------------
import { resolveLRSnapshotForSave } from '../lr-snapshot.js';

test('guard: empty fresh + non-empty existing keeps existing', () => {
  const existing = [{ type: 'narration', stage: 1, html: '<p>x</p>', dataset: {}, ts: 1 }];
  const r = resolveLRSnapshotForSave([], existing);
  assert.deepEqual(r.snapshot, existing);  // preserve existing
  assert.equal(r.preserved, true);         // flag "skipped overwrite" so caller can log
});

test('guard: non-empty fresh always wins (even when existing non-empty)', () => {
  const fresh = [{ type: 'narration', stage: 2, html: '<p>y</p>', dataset: {}, ts: 2 }];
  const existing = [{ type: 'narration', stage: 1, html: '<p>x</p>', dataset: {}, ts: 1 }];
  const r = resolveLRSnapshotForSave(fresh, existing);
  assert.deepEqual(r.snapshot, fresh);
  assert.equal(r.preserved, false);
});

// INVARIANT TEST (D-4): a SHORTER-but-non-empty fresh MUST overwrite a longer existing.
// This is the legitimate recollect/revise/delete-section case. If someone "tightens"
// the guard to `fresh.length >= existing.length`, THIS test fails — which is the point.
test('guard: shorter non-empty fresh still overwrites longer existing (recollect/revise)', () => {
  const fresh = [{ type: 'narration', stage: 1, html: '<p>a</p>', dataset: {}, ts: 3 }];
  const existing = [
    { type: 'narration', stage: 1, html: '<p>a</p>', dataset: {}, ts: 1 },
    { type: 'section', stage: 5, html: '<p>b</p>', dataset: {}, ts: 2 },
  ];
  const r = resolveLRSnapshotForSave(fresh, existing);
  assert.deepEqual(r.snapshot, fresh);   // shorter wins — NOT preserved
  assert.equal(r.preserved, false);
});

test('guard: empty fresh + empty/missing existing writes empty (new session ok)', () => {
  assert.deepEqual(resolveLRSnapshotForSave([], []).snapshot, []);
  assert.deepEqual(resolveLRSnapshotForSave([], null).snapshot, []);
  assert.deepEqual(resolveLRSnapshotForSave([], undefined).snapshot, []);
  assert.equal(resolveLRSnapshotForSave([], []).preserved, false);
});

// ---------------------------------------------------------------------------
// Task 2 (D-7): stream-scoped session-id guard decision logic (shouldSaveLRSnapshot)
// NOTE (R3 in-house): verifies the PURE FUNCTION only. It does NOT verify the
// wiring (that triggeringLRSid carries a non-null value on continue streams) —
// that is enforced by Step 1b's call-site capture + stop-cond.11 and can only be
// confirmed by a real two-session-switch E2E (Task 6). "These tests pass" ≠ "D-7
// works at runtime".
// ---------------------------------------------------------------------------
import { shouldSaveLRSnapshot } from '../lr-snapshot.js';
const eq = (a, b) => String(a) === String(b);  // stand-in for window.matchSessionId

test('D-7: stale stream (triggering id != loaded id) skips', () => {
  assert.equal(shouldSaveLRSnapshot('lr-OLD', 'sess-NEW', eq), false);
});
test('D-7: same session (ids match) proceeds', () => {
  assert.equal(shouldSaveLRSnapshot('lr-A', 'lr-A', eq), true);
});
test('D-7: null triggering id (mock / pre-adopt) does NOT skip', () => {
  assert.equal(shouldSaveLRSnapshot(null, 'sess-1', eq), true);
  assert.equal(shouldSaveLRSnapshot(undefined, 'sess-1', eq), true);
});
test('D-7: null loaded id does NOT skip', () => {
  assert.equal(shouldSaveLRSnapshot('lr-A', null, eq), true);
});
