import { test } from 'node:test';
import assert from 'node:assert';
import { serializeLRChatRoot, lrStagesInSnapshot, _isReplayRealContent } from '../lr-snapshot.js';

// _appendReplayedBubbles is DOM-bound (document.createElement) and live-research.js cannot
// load under plain node (it pulls browser globals at import time). Per the plan's pure-helper
// path, _isReplayRealContent + LR_CHECKPOINT_CANNED_STRINGS live in lr-snapshot.js, so we
// import _isReplayRealContent directly and drive a stub that mirrors what _appendReplayedBubbles
// does on each entry:
//   (R8 BLOCKER 1) DELETE any inherited persisted lrContent FIRST, then
//   (R8 BLOCKER 2) re-derive data-lr-content via the CONTENT-AWARE judge.
// The resulting wrappers are read back by the SAME serializeLRChatRoot ([data-lr-content] filter)
// so the round-trip mirrors production exactly.

// `inheritedLrContent` simulates a legacy persisted dataset that already carries lrContent:"1"
// (the serializer spreads the whole dataset). The replay builder DELETES it, then re-derives.
function replayedWrapper(type, stage, html, inheritedLrContent = false) {
  const persisted = { lrStage: String(stage) };
  if (inheritedLrContent) persisted.lrContent = '1';        // legacy persisted marker
  const dataset = { ...persisted };
  delete dataset.lrContent;                                 // R8 BLOCKER 1: strip inherited marker FIRST
  if (_isReplayRealContent(type, html)) dataset.lrContent = '1';   // R8 BLOCKER 2: content-aware re-derive
  return {
    className: `lr-chat-message ${type}`,
    dataset,
    querySelector: (sel) => (sel === '.lr-msg-bubble' ? { className: 'lr-msg-bubble', innerHTML: html } : null),
  };
}
function rootOf(ws) {
  return {
    querySelectorAll: (sel) => (sel === ':scope > .lr-chat-message[data-lr-content]'
      ? ws.filter(w => w.dataset && w.dataset.lrContent !== undefined) : []),
  };
}
const purify = { sanitize: (h) => h };

test('replayed real-content wrappers round-trip: all carry data-lr-content → re-serialize keeps them, stage preserved', () => {
  const ws = [
    replayedWrapper('user', 0, '<p>我的研究問題</p>'),
    replayedWrapper('narration', 1, '<p>建立結構</p>'),
    replayedWrapper('section', 5, '<p>第一段</p>'),
  ];
  const out = serializeLRChatRoot(rootOf(ws), purify);
  assert.equal(out.length, 3);                              // none dropped on re-serialize (all marked)
  assert.equal(out[0].type, 'user'); assert.equal(out[0].stage, 0);   // user-stage-0 survives
  assert.equal(out[1].stage, 1);
  assert.equal(out[2].stage, 5);
  assert.deepEqual(lrStagesInSnapshot(out), [1, 5]);        // stage grouping intact after round-trip
});

test('a replayed wrapper missing data-lr-content would be DROPPED on re-serialize (guards the must-mark rule)', () => {
  const bad = replayedWrapper('narration', 1, '<p>x</p>');
  delete bad.dataset.lrContent;                            // simulate forgetting the mark
  const out = serializeLRChatRoot(rootOf([bad]), purify);
  assert.equal(out.length, 0);                              // unmarked → excluded → the SECOND data-loss bug
});

// ── R7 BLOCKER: legacy-polluted snapshot (system/assistant/error) self-heals on replay ──
test('legacy self-heal: replaying a snapshot with legacy `system` garbage does NOT mark it → re-serialize drops it', () => {
  const ws = [
    replayedWrapper('user', 0, '<p>我的研究問題</p>'),     // real → marked
    replayedWrapper('system', 0, '<div>（暫態 system 垃圾，舊 serializer 誤存）</div>'),  // legacy garbage → NOT marked
    replayedWrapper('narration', 1, '<p>建立結構</p>'),     // real → marked
  ];
  assert.equal(ws[1].dataset.lrContent, undefined);        // sanity: system wrapper unmarked
  const out = serializeLRChatRoot(rootOf(ws), purify);
  assert.equal(out.length, 2);                              // system garbage DROPPED on re-serialize
  assert.ok(out.every(e => e.type !== 'system'));          // legacy garbage self-healed away
  assert.equal(out[0].type, 'user');
  assert.equal(out[1].type, 'narration');
});

test('legacy self-heal: assistant/error legacy garbage also dropped; real checkpoint/user/narration/section retained', () => {
  const ws = [
    replayedWrapper('checkpoint', 4, '<div class="lr-checkpoint-label">Checkpoint — 階段 4</div><div class="lr-checkpoint-proposal">引用格式偏好？</div>'),  // REAL proposal (no canned string) → marked
    replayedWrapper('assistant', 0, '<div>（暫態 assistant 通知）</div>'),  // legacy garbage → NOT marked
    replayedWrapper('error', 0, '<div>（暫態 error 通知）</div>'),          // legacy garbage → NOT marked
    replayedWrapper('section', 5, '<p>第一段</p>'),         // real → marked
  ];
  const out = serializeLRChatRoot(rootOf(ws), purify);
  assert.equal(out.length, 2);                              // assistant + error dropped
  assert.ok(out.every(e => e.type !== 'assistant' && e.type !== 'error'));
  assert.equal(out[0].type, 'checkpoint');                 // real proposal retained
  assert.equal(out[1].type, 'section');
});

// ── R8 BLOCKER 2: legacy CANNED checkpoint self-heals (content-aware, not unconditional) ──
test('R8 self-heal: a legacy CANNED checkpoint (html contains 從中斷處繼續) is NOT marked → dropped on re-serialize', () => {
  const cannedHtml = '<div class="lr-checkpoint-label">Checkpoint — 階段 5</div>'
    + '<div class="lr-checkpoint-proposal"><strong>（從中斷處繼續）階段 5 — 章節撰寫</strong><br>你的研究進度已保存。</div>';
  const realHtml = '<div class="lr-checkpoint-label">Checkpoint — 階段 4</div>'
    + '<div class="lr-checkpoint-proposal">寫作風格確認完畢。引用格式偏好？（APA、Chicago…）</div>';
  const ws = [
    replayedWrapper('checkpoint', 5, cannedHtml),          // CANNED → NOT marked → self-heals
    replayedWrapper('checkpoint', 4, realHtml),            // REAL → marked → retained
  ];
  assert.equal(ws[0].dataset.lrContent, undefined);        // canned checkpoint left unmarked
  assert.equal(ws[1].dataset.lrContent, '1');              // real proposal marked
  const out = serializeLRChatRoot(rootOf(ws), purify);
  assert.equal(out.length, 1);                             // canned dropped, real kept
  assert.equal(out[0].type, 'checkpoint');
  assert.ok(out[0].html.includes('引用格式偏好'));         // it is the REAL proposal that survived
  assert.ok(!out[0].html.includes('從中斷處繼續'));        // the canned box did NOT survive
});

// ── R8 BLOCKER 1: inherited persisted lrContent on legacy garbage is DELETED before the judge ──
test('R8 self-heal: legacy garbage carrying an INHERITED lrContent in its persisted dataset is still dropped', () => {
  const sysWithInherited = replayedWrapper('system', 0, '<div>（暫態 system 垃圾）</div>', /*inheritedLrContent=*/true);
  const cannedCpWithInherited = replayedWrapper('checkpoint', 5,
    '<div class="lr-checkpoint-proposal">（從中斷處繼續）階段 5</div>', /*inheritedLrContent=*/true);
  assert.equal(sysWithInherited.dataset.lrContent, undefined);
  assert.equal(cannedCpWithInherited.dataset.lrContent, undefined);
  const out = serializeLRChatRoot(rootOf([sysWithInherited, cannedCpWithInherited]), purify);
  assert.equal(out.length, 0);                             // inherited marker did NOT save the garbage
});

test('replay checkpoint marking is CONTENT-AWARE (real proposal marked, no isRealContent needed on replay side)', () => {
  const ws = [replayedWrapper('checkpoint', 4, '<div class="lr-checkpoint-proposal">真提案：引用格式偏好？</div>')];
  assert.equal(ws[0].dataset.lrContent, '1');             // real proposal marked by the content-aware judge
  const out = serializeLRChatRoot(rootOf(ws), purify);
  assert.equal(out.length, 1);
  assert.equal(out[0].type, 'checkpoint');                // real proposal survives re-serialize
});

// Direct assertion against the imported judge (canned vs real, and bare 繼續 must NOT mis-kill)
test('_isReplayRealContent: type + content rules (bare 繼續 does NOT trigger canned exclusion)', () => {
  assert.equal(_isReplayRealContent('user', '<p>x</p>'), true);
  assert.equal(_isReplayRealContent('narration', '<p>x</p>'), true);
  assert.equal(_isReplayRealContent('section', '<p>x</p>'), true);
  assert.equal(_isReplayRealContent('system', '<p>x</p>'), false);
  assert.equal(_isReplayRealContent('assistant', '<p>x</p>'), false);
  assert.equal(_isReplayRealContent('error', '<p>x</p>'), false);
  assert.equal(_isReplayRealContent('checkpoint', '<div>從中斷處繼續</div>'), false);   // canned
  assert.equal(_isReplayRealContent('checkpoint', '<div>還是可以進入寫作準備？要繼續寫嗎</div>'), true);  // bare 繼續 → still real
});
