import { test } from 'node:test';
import assert from 'node:assert';
import { classifyReconnectFetchOutcome, lrReconnectAuthCopy } from '../lr-reconnect-auth.js';

test('200 → ok (no refresh, no degrade)', () => {
  const r = classifyReconnectFetchOutcome({ initialStatus: 200, refreshAttempted: false, retryStatus: null });
  assert.equal(r.outcome, 'ok');
});

test('401 then refresh ok then retry 200 → ok', () => {
  const r = classifyReconnectFetchOutcome({ initialStatus: 401, refreshAttempted: true, refreshOk: true, retryStatus: 200 });
  assert.equal(r.outcome, 'ok');
});

test('401, refresh fails → degrade (auth dead, gentle relogin)', () => {
  const r = classifyReconnectFetchOutcome({ initialStatus: 401, refreshAttempted: true, refreshOk: false, retryStatus: null });
  assert.equal(r.outcome, 'auth_dead');
  assert.equal(r.keepConnectionLost, true);    // must NOT clear the disconnect state
  assert.equal(r.showRelogin, true);            // gentle hint, not modal
});

test('401, refresh ok, but retry still 401 → degrade as auth_dead', () => {
  const r = classifyReconnectFetchOutcome({ initialStatus: 401, refreshAttempted: true, refreshOk: true, retryStatus: 401 });
  assert.equal(r.outcome, 'auth_dead');
  assert.equal(r.showRelogin, true);
});

test('non-401 server error (500) → transient, retry next wake, no relogin hint', () => {
  const r = classifyReconnectFetchOutcome({ initialStatus: 500, refreshAttempted: false, retryStatus: null });
  assert.equal(r.outcome, 'transient');
  assert.equal(r.keepConnectionLost, true);     // still disconnected; try again on next wake
  assert.equal(r.showRelogin, false);
});

test('copy string is the gentle non-terminal relogin hint (no "error", mentions 重新登入 + 接回)', () => {
  assert.match(lrReconnectAuthCopy.reloginNeeded, /重新登入/);
  assert.match(lrReconnectAuthCopy.reloginNeeded, /接回/);
});
