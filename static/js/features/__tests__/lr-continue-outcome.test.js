// plan: lr-reconnect-continue-takeover (2026-08-20)
//
// 鎖住「斷線後按繼續連按三次全失敗」的兩條機制：
//   1. 429（斷線後舊背景 task 仍佔住並行 slot）必須是「有界自動退避重試」，
//      不可跟一般錯誤一樣被丟給使用者去盲目重按。
//   2. 有 SSE 串流在跑時，醒來重連（read-only 重繪）必須關閉——否則舊 state 會把
//      正在跑的續跑洗回中斷點。
import { test } from 'node:test';
import assert from 'node:assert';
import {
  classifyContinueHttpOutcome,
  shouldRunWakeReconnect,
  lrContinueCopy,
  LR_CONTINUE_MAX_AUTO_RETRIES,
  LR_CONTINUE_RETRY_DELAYS_MS,
} from '../lr-continue-outcome.js';

test('2xx → stream（正常續跑）', () => {
  assert.equal(classifyContinueHttpOutcome({ status: 200, attempt: 0 }).action, 'stream');
  assert.equal(classifyContinueHttpOutcome({ status: 204, attempt: 2 }).action, 'stream');
});

test('401 → auth_expired（登入問題，不是忙碌，不重試）', () => {
  assert.equal(classifyContinueHttpOutcome({ status: 401, attempt: 0 }).action, 'auth_expired');
});

test('429 第一次 → auto_retry，且 delay 有界（不是伺服器建議的 30s）', () => {
  const r = classifyContinueHttpOutcome({ status: 429, attempt: 0 });
  assert.equal(r.action, 'auto_retry');
  assert.equal(r.delayMs, LR_CONTINUE_RETRY_DELAYS_MS[0]);
  assert.ok(r.delayMs > 0 && r.delayMs <= 10000, 'delay 應在 10s 內，否則使用者只會再手動重按');
});

test('429 退避遞增（第二次等得比第一次久）', () => {
  const first = classifyContinueHttpOutcome({ status: 429, attempt: 0 }).delayMs;
  const second = classifyContinueHttpOutcome({ status: 429, attempt: 1 }).delayMs;
  assert.ok(second > first, '退避必須遞增，否則等於原地重打');
});

test('429 重試用盡 → busy_giveup（交還使用者，不是無限重試也不是假裝斷線）', () => {
  const r = classifyContinueHttpOutcome({ status: 429, attempt: LR_CONTINUE_MAX_AUTO_RETRIES });
  assert.equal(r.action, 'busy_giveup');
});

test('其他非 2xx（500）→ error（走既有錯誤路徑，不吞）', () => {
  assert.equal(classifyContinueHttpOutcome({ status: 500, attempt: 0 }).action, 'error');
});

test('429 文案：重試中不叫使用者重按；放棄時不謊稱是連線問題', () => {
  assert.match(lrContinueCopy.busyRetrying, /不需要重按/);
  assert.match(lrContinueCopy.busyGaveUp, /進度已保存/);
  // 這兩句都不可宣稱「連線中斷」——那是本次 bug 的錯誤歸因（真因是並行 slot 忙碌）。
  assert.doesNotMatch(lrContinueCopy.busyRetrying, /連線中斷/);
  assert.doesNotMatch(lrContinueCopy.busyGaveUp, /連線中斷/);
});

test('重連 gate：斷過線 + 無串流 + 有 session → 才重連', () => {
  assert.equal(shouldRunWakeReconnect({ connectionLost: true, streamInflight: false, hasSession: true }), true);
});

test('重連 gate：串流在跑時一律不重連（否則舊 state 洗掉正在跑的續跑）', () => {
  assert.equal(shouldRunWakeReconnect({ connectionLost: true, streamInflight: true, hasSession: true }), false);
});

test('重連 gate：沒斷過 / 沒 session → 不重連', () => {
  assert.equal(shouldRunWakeReconnect({ connectionLost: false, streamInflight: false, hasSession: true }), false);
  assert.equal(shouldRunWakeReconnect({ connectionLost: true, streamInflight: false, hasSession: false }), false);
});
