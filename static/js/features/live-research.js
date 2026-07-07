// static/js/features/live-research.js
//
// D-1 Module Header — Live Research Owner (state + functions — commit 16, Phase 8)
//
//   Owned state:
//     - _currentLRSessionId (string|null — server-generated UUID for LR state persistence)
//     - _lrInProgress (boolean — LR run-in-progress flag for Bug 3 guard)
//     - _lrAwaitingCheckpointReply (boolean — UI flag for backend reply-pending state)
//     - _currentLRStage (number — UX-1 D-6 typing indicator stage tracking)
//     - _currentLRActivity (string — UX-1 D-6 dynamic activity text override)
//
//   Functions migrated (15 — commit 16, Phase 8):
//     UI helpers:
//       - resetLiveResearchUI (was news-search.js:2622)
//       - addLRChatMessage (was 2664)
//       - showLRTypingIndicator (was 2716)
//       - hideLRTypingIndicator (was 2733)
//       - updateLRTypingIndicatorText (was 2738)
//       - resetLRTypingState (was 2746)
//       - deriveActivityFromNarration (was 2753)
//       - updateLRStageProgress (was 2816)
//       - showLRCheckpoint (was 2844)
//       - addLRSection (was 2872)
//       - showLRExport (was 2898)
//     SSE handler:
//       - handleLiveResearchSSE (was 2948)
//     Main entries:
//       - performLiveResearch (was 3182)
//       - continueLiveResearch (was 3249)
//
//   Trigger writes:
//     - performLiveResearch / continueLiveResearch SSE handlers (set session_id from server payload)
//     - LR Stage 1-5 entry sets lrInProgress=true
//     - LR Stage 6 export / resetLiveResearchUI / explicit LR end sets lrInProgress=false + clears session id
//     - UserStateSync.clearUserScopedState (IIFE relocated to state-sync.js commit 11) clears
//       via clearLRSessionId with skipIfInflight guard (LR Bug 3 fix — preserves session id
//       when lrInProgress)
//
//   External read:
//     - news-search.js LR control flow (start / stop / continue / SSE handlers via imports)
//     - core/page-bootstrap.js checkAuthOnLoad + visibilitychange guards (preserve authManager._user)
//     - state-sync.js IIFE clearUserScopedState guard (via injected backref)
//
// D-3 Cross-Module Communication:
//   This module IMPORTS injectStateSyncBackref from core/state-sync.js (D-V3 pattern)
//   and registers a small backref bundle at module-init time. state-sync.js does NOT
//   import this module — that direction would create a circular dependency + TDZ risk.
//
//   Additional cross-module imports (D-V6 relax, commit 16):
//     - features/search.js — setProcessingState (LR uses search's spinner state)
//     - features/search.js — pushConversationHistory + setCurrentConversationId
//     - features/source-filters.js — getSelectedSitesParam (LR URL param)
//     - features/session-manager.js — markSessionDirty
//     - features/sessions-list.js — getSavedSessions/setSavedSessions are NOT needed; LR
//       uses window.saveCurrentSession (KEEP-in-place per CEO #5 until commit 25 sweep)
//     - utils/analytics.js — getCurrentSessionId (HTTP session id)
//
// D-13 Compliance:
//   ONE top-level side effect: injectStateSyncBackref({...}) — registers backref bundle.
//   No fetch / no DOM / no localStorage / no addEventListener at top level. The inject
//   call is explicitly permitted as a module-init wiring step (analogous to main.js's
//   injectStateSync({...}) call which wires UserStateSync into auth-manager).
//
// v4.0 Commit 7 (2026-05-24): State migration + D-V3 H1 fix (injectStateSync wire).
// v4.0 Commit 16 (2026-05-25, Phase 8): 15 LR function bodies migrated from news-search.js.
//   Bridge removed: 1 — `window.performLiveResearch` (was news-search.js:3247).
//   continueLiveResearch / resetLiveResearchUI accessed via ES import in
//   news-search.js bindLRCheckpointListeners IIFE + mode-switch handler.
//   saveCurrentSession invoked via window bridge (KEEP-in-place until commit 25 sweep).

import { injectStateSyncBackref } from '../core/state-sync.js';
import { setProcessingState, pushConversationHistory, setCurrentConversationId, escapeHTML } from './search.js?v=20260705c';
import { getSelectedSitesParam } from './source-filters.js';
import { markSessionDirty } from './session-manager.js';
import { getCurrentSessionId } from '../utils/analytics.js';
import { classifyLRResumeState } from './lr-resume-classify.js';
import { buildCitationHref, escapeHtmlAttr } from './text-fragment.js';
import { serializeLRChatRoot, lrStagesInSnapshot, lrSnapshotForStage, shouldSaveLRSnapshot, snapshotHasReplayableEntries, _isReplayRealContent, LR_CHECKPOINT_CANNED_STRINGS } from './lr-snapshot.js';
import { getCurrentLoadedSessionId } from './sessions-list.js';
import { classifyReconnectFetchOutcome, lrReconnectAuthCopy } from './lr-reconnect-auth.js';
// Re-export pure snapshot helpers (unit-tested in lr-snapshot.js) for any importers of this module.
export { serializeLRChatRoot, lrStagesInSnapshot, lrSnapshotForStage };

// ============================================================================
// currentLRSessionId — server-generated UUID for LR state persistence
// ============================================================================
let _currentLRSessionId = null;

export function getLRSessionId() {
    return _currentLRSessionId;
}

export function setLRSessionId(id) {
    _currentLRSessionId = id;
}

// LR Bug 3 guard (2026-05-19 root fix): when an LR run is inflight, refuse to
// clear the session id from background reset paths (UserStateSync.clearUserScopedState
// / runInitSync / 401-refresh-fail). The next continue POST relies on this id to
// hit the same backend lr_session row; wiping it produces R5「找不到先前的研究 session」.
//
// Callers that legitimately end an LR run (Stage 6 export, resetLiveResearchUI for
// fresh start) call clearLRSessionId() without the skipIfInflight flag → unconditional.
// Background reset paths pass { skipIfInflight: true } → guarded.
export function clearLRSessionId({ skipIfInflight = false } = {}) {
    if (skipIfInflight && _lrInProgress) {
        console.warn('[live-research] LR active — keeping currentLRSessionId across reset (lr_session_id=' + _currentLRSessionId + ')');
        return;
    }
    _currentLRSessionId = null;
}

// ============================================================================
// lrInProgress — LR run-in-progress flag (Bug 3 guard input)
// ============================================================================
let _lrInProgress = false;

export function isLRInProgress() {
    return _lrInProgress;
}

export function setLRInProgress(b) {
    _lrInProgress = !!b;
}

// ============================================================================
// _lrSwitchToken — self-contained session-switch race guard (B5, 2026-06-05)
//   Incremented each time a new session-switch restore is scheduled.
//   restoreLRCheckpointFromState checks if the token it received is still
//   current; if not (a newer switch has been scheduled) it bails out early.
//   Does NOT affect page-load single restore or #19 resume (token null path).
// ============================================================================
let _lrSwitchToken = 0;

export function bumpLRSwitchToken() {
    return ++_lrSwitchToken;
}

export function getLRSwitchToken() {
    return _lrSwitchToken;
}

// ============================================================================
// SSE 斷線重連（plan: lr-sse-reconnect-resume, 2026-06-15）
//   斷線時後端研究仍在跑（server 不取消、跑到 checkpoint 才停）。前端：
//   (b) 偵測 SSE 斷線 → 顯示可恢復狀態（非終止性 error）
//   (c) 醒來（online / visibilitychange）→ 自動重連 → 拉最新 state render
//       **INVARIANT：read-only，絕不送 /continue**（否則醒來誤觸 checkpoint
//       auto-advance，燒錢 + 跳過使用者確認）
//   (d) idempotent / debounce 防多事件連發
// ============================================================================
let _lrConnectionLost = false;
let _lrReconnectInflight = false;
let _lrReconnectTimer = null;

// 測試 / 外部覆寫用 seam：實際重連動作（拉 state + render）。預設 null，
// 由 _doLRReconnect 實作；wake 流程只透過 _debouncedLRReconnect 進入，保證 read-only。

function showLRConnectionInterrupted() {
    // 非 error 樣式的 system bubble：明確告知「研究仍在背景跑」，不可顯示終止性 error。
    addLRChatMessage(
        'system',
        '<em>連線中斷，研究仍在背景進行中，恢復連線後會自動接回。</em>'
    );
}

// Gentle, non-terminal relogin hint shown when wake-reconnect finds auth is dead
// (access token expired AND refresh token expired/revoked). We deliberately do NOT
// pop the login modal here (that is authManager._handleAuthFailure's job for
// foreground interactions) — popping it would bury the "研究仍在背景進行中" bubble
// and make it look like the research died. Instead we leave _lrConnectionLost set so
// a later wake (or a real user interaction) can recover, and add a calm hint.
let _lrReloginHintShown = false;
let _lrReloginHintDeferred = false;   // B1: set when #lrChat absent at hint time; flushed on next LR show
function showLRReloginNeeded() {
    if (_lrReloginHintShown) return;     // idempotent: don't spam on repeated wakes
    // B1 (NO SILENT FAIL — verified addLRChatMessage @ live-research.js:367-369 does
    // `const chat = document.getElementById('lrChat'); if (!chat) return;`, i.e. it
    // SILENTLY swallows the bubble when #lrChat is absent — the user switched away from
    // the LR view / the LR panel was torn down). If we just call addLRChatMessage here,
    // the auth-dead hint vanishes with zero trace and the user thinks reconnect is still
    // trying. So: detect #lrChat absence, log a warn (always leaves a trace), and DEFER
    // the hint so it surfaces the next time the LR view is shown. NO modal.
    const chat = document.getElementById('lrChat');
    if (!chat) {
        console.warn('[Live Research] relogin hint could not render — #lrChat absent; deferring until LR view shows');
        _lrReloginHintDeferred = true;   // flushed by flushDeferredLRReloginHint() on next LR show
        return;                          // do NOT set _lrReloginHintShown — hint hasn't actually shown yet
    }
    _lrReloginHintShown = true;
    _lrReloginHintDeferred = false;
    addLRChatMessage('system', lrReconnectAuthCopy.reloginNeeded);
}

// B1 flush: call this from wherever the LR view/panel becomes visible again (e.g. the
// LR-show / restore path that re-mounts #lrChat) so a hint deferred while #lrChat was
// absent is not lost. Idempotent and cheap.
function flushDeferredLRReloginHint() {
    if (!_lrReloginHintDeferred) return;
    if (_lrReloginHintShown) { _lrReloginHintDeferred = false; return; }
    const chat = document.getElementById('lrChat');
    if (!chat) return;                   // still not mounted; keep deferred
    _lrReloginHintShown = true;
    _lrReloginHintDeferred = false;
    addLRChatMessage('system', lrReconnectAuthCopy.reloginNeeded);
}

function _scheduleLRWakeReconnect(source) {
    if (!_lrConnectionLost) return;            // 沒斷過不處理
    if (!getLRSessionId()) return;             // 無 session 無從 restore
    _debouncedLRReconnect(source);
}

function _debouncedLRReconnect(source) {
    if (_lrReconnectInflight) return;          // idempotent：一次只一個重連
    clearTimeout(_lrReconnectTimer);
    _lrReconnectTimer = setTimeout(async () => {
        _lrReconnectInflight = true;
        try {
            await _doLRReconnect(source);
        } catch (e) {
            console.warn('[Live Research] wake reconnect failed:', e);
        } finally {
            _lrReconnectInflight = false;
        }
    }, 600);                                   // debounce 多事件連發（online + visibilitychange）
}

// 實際重連：READ-ONLY — 只拉最新 state + render，**絕不**送 POST /continue。
// （wake reconnect 不可呼叫 continueLiveResearch / performLiveResearch。）
async function _doLRReconnect(source) {
    const sid = getLRSessionId();
    if (!sid) return;
    console.log('[Live Research] wake reconnect (read-only) source=', source, 'session=', sid);

    const url = new URL(`/api/sessions/${sid}`, window.location.origin);

    // Bare GET helper: attach current access token if present, always send the
    // refresh cookie via credentials. We intentionally bypass
    // authManager.authenticatedFetch so a dead refresh token does NOT trigger
    // _handleAuthFailure (login modal + state wipe) over the disconnect bubble.
    async function _bareSessionGet() {
        const headers = {};
        const tok = (window.authManager && window.authManager.getAccessToken)
            ? window.authManager.getAccessToken() : null;
        if (tok) headers['Authorization'] = `Bearer ${tok}`;
        return fetch(url, { method: 'GET', headers, credentials: 'same-origin' });
    }

    let initialStatus;
    let refreshAttempted = false;
    let refreshOk = false;
    let retryStatus = null;
    let okResp = null;

    try {
        let resp = await _bareSessionGet();
        initialStatus = resp.status;
        if (resp.ok) {
            okResp = resp;
        } else if (resp.status === 401) {
            // W1 — Option B (guard chosen): if authManager already has a foreground
            // refresh in-flight (_refreshPromise set — verified auth-manager.js:182-183),
            // do NOT fire a second bare refresh. Reason: POST /api/auth/refresh ROTATES
            // the refresh token server-side (BP-2, revoke-old + issue-new,
            // auth_service.py:408-426). Two concurrent refreshes race: whichever lands
            // second uses a cookie the first already revoked → "Refresh token has been
            // revoked" → if that loser is the foreground path, its refreshToken() catch
            // fires _handleAuthFailure → the login modal pops anyway, defeating this fix.
            // Option B keeps a single rotation in flight: when a foreground refresh is
            // already running, treat this wake as transient (stay disconnected, retry on
            // the next wake — by then the foreground refresh has settled). This only
            // READS window.authManager._refreshPromise; it does NOT edit auth-manager.js,
            // so we stay inside the narrow fix. (Chose B over A because A leaves a
            // user-visible modal-flash window; B closes it for one cheap `if`.)
            if (window.authManager && window.authManager._refreshPromise) {
                // Early-return as transient (NOT auth_dead): a foreground refresh is
                // already rotating the token; by the next wake it will have settled and
                // a fresh cookie will be in place, so retrying then is correct. We do NOT
                // run a bare refresh here (would race/revoke the foreground one) and do
                // NOT show a relogin hint (auth is not dead, just mid-refresh).
                console.warn('[Live Research] wake reconnect: foreground refresh in-flight (W1 Option B guard); transient, retry next wake');
                return;   // _lrConnectionLost stays true; no relogin hint, no modal
            }
            // One-shot manual refresh (mints a fresh httpOnly cookie / token).
            // NOTE: bare fetch to /api/auth/refresh — NOT authManager.refreshToken(),
            // which would call _handleAuthFailure on failure and pop the modal.
            refreshAttempted = true;
            try {
                const rf = await fetch('/api/auth/refresh', {
                    method: 'POST',
                    credentials: 'same-origin'
                });
                refreshOk = rf.ok;
                if (rf.ok) {
                    // Retry once. Re-read token (it may now be cookie-only → null,
                    // in which case credentials carries the fresh cookie).
                    const resp2 = await _bareSessionGet();
                    retryStatus = resp2.status;
                    if (resp2.ok) okResp = resp2;
                }
            } catch (rfErr) {
                console.warn('[Live Research] wake reconnect: refresh fetch error', rfErr);
                refreshOk = false;
            }
        }
    } catch (e) {
        // Network-level failure (offline mid-wake). Treat as transient: stay
        // disconnected, retry on next wake. Do NOT show a relogin hint.
        // N1 NOTE: this network-error path returns HERE, BEFORE classifyReconnectFetchOutcome
        // is ever called — so the live "network 0 / fetch threw" case does NOT actually flow
        // through the classifier. The behavior is identical to the classifier's transient
        // branch (stay disconnected, no relogin hint), so this is intentional and fine.
        console.warn('[Live Research] wake reconnect: state fetch error', e);
        return;
    }

    const decision = classifyReconnectFetchOutcome({
        initialStatus,
        refreshAttempted,
        refreshOk,
        retryStatus,
    });

    if (decision.outcome === 'auth_dead') {
        // Access token expired AND refresh token expired/revoked. Keep the
        // disconnect bubble (research is genuinely still in the background) and
        // show a calm relogin hint instead of a screen-blocking modal.
        console.warn('[Live Research] wake reconnect: auth dead (refresh expired/revoked); degrading gently, no modal');
        // _lrConnectionLost stays true (decision.keepConnectionLost) so a later
        // wake after the user logs back in can recover.
        if (decision.showRelogin) showLRReloginNeeded();
        return;
    }

    if (decision.outcome === 'transient') {
        console.warn('[Live Research] wake reconnect: transient non-2xx, will retry on next wake; status=', initialStatus);
        return;   // _lrConnectionLost stays true; no relogin hint
    }

    // outcome === 'ok' → we have okResp. Parse state and render.
    let lrState = null;
    try {
        const payload = await okResp.json().catch(() => ({}));
        lrState = (payload.session && payload.session.live_research_state)
               || payload.live_research_state
               || payload.lr_state
               || null;
    } catch (e) {
        console.warn('[Live Research] wake reconnect: state parse error', e);
        return;
    }
    if (!lrState) return;

    // Reconnect succeeded: clear disconnect state, re-arm relogin hint, bump
    // token to prevent a stale render from overwriting, then read-only render.
    _lrConnectionLost = false;
    _lrReloginHintShown = false;
    _lrReloginHintDeferred = false;   // B1: drop any pending deferred hint — auth is healthy again
    const token = bumpLRSwitchToken();
    restoreLRCheckpointFromState(lrState, sid, token);   // INVARIANT: pure DOM, no HTTP
}

// 喚醒監聽：online / visibilitychange 兩路（debounce 收斂）。
if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('online', () => _scheduleLRWakeReconnect('online'));
    if (typeof document !== 'undefined' && document.addEventListener) {
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                _scheduleLRWakeReconnect('visibilitychange');
            }
        });
        // W2: after the user logs back in (state-sync.js applyInit dispatches this on
        // every successful login — verified state-sync.js:306), re-fire wake-reconnect so
        // a research degraded to "auth dead" actually reconnects without needing a network
        // flap or tab switch. _scheduleLRWakeReconnect already no-ops when not disconnected
        // (_lrConnectionLost false) or when there is no LR session, so this is safe to fire
        // on every login. Goes through the same read-only _debouncedLRReconnect path —
        // INVARIANT (no /continue) preserved.
        document.addEventListener('user-state-synced', () => _scheduleLRWakeReconnect('relogin'));
    }
}

// ── Snapshot allow-list (candidate A, positive) ─────────────────────────────
// INVARIANT (class-based — supersedes the old enumerated .lr-no-serialize black-list):
// ONLY real conversation content is serialized into lr_dialog_snapshot. addLRChatMessage
// marks such bubbles with data-lr-content; serializeLRChatRoot stores ONLY marked bubbles
// (lr-snapshot.js — selector :scope > .lr-chat-message[data-lr-content]).
//   • {user, narration, section} are ALWAYS real content → auto-marked by type.
//   • checkpoint is cross-fate: REAL (SSE proposal / clarification) → opt in via
//     options.isRealContent; TRANSIENT restore-canned boxes → do NOT.
//   • EVERY other bubble (system/assistant/error notices, operation echo, restore/reconnect/
//     relogin/connection boxes, degradation banners) is TRANSIENT → left UNMARKED → excluded
//     by DEFAULT. A new transient box needs NO change (default-safe); a new REAL-content type
//     must be added to LR_REAL_CONTENT_TYPES (or pass isRealContent for a checkpoint variant).
const LR_REAL_CONTENT_TYPES = new Set(['user', 'narration', 'section']);

// REPLAY-side real-content decision (R7+R8 BLOCKER fix — used by _appendReplayedBubbles via
// _isReplayRealContent, imported from lr-snapshot.js). NOT a plain type set: a replayed
// `checkpoint` may be a REAL proposal OR a LEGACY restore-canned box (the old store-everything
// serializer captured one via the dirty-save path — prod b08080f8 has 16 real + 1 canned). They
// share IDENTICAL markup, so the judge inspects the entry html for restore-canned operation
// strings (LR_CHECKPOINT_CANNED_STRINGS). There is NO LR_REPLAY_REAL_CONTENT_TYPES constant
// (R8 removed it — type alone is insufficient). See _isReplayRealContent in lr-snapshot.js and
// "Legacy-polluted snapshot handling" in the plan.

// ============================================================================
// LR Typing Indicator state (UX-1 D-3 + D-6 stage-aware)
// ============================================================================
let _currentLRStage = 0;          // 0 = not started
let _currentLRActivity = '';      // override text; empty → use stage default
// Bug fix 2026-05-16：追蹤 reply UI 是否該顯示。true = 後端在等 user reply（
// checkpoint emit 後、continueLiveResearch 送出前）。narration handler 若見此旗標
// 仍為 true 而 reply UI 被隱藏，則重新顯示（防 backend 漏 emit checkpoint 卡死）。
let _lrAwaitingCheckpointReply = false;

// 回顧模式當前 state — stage-nav listener 讀此變數（非 closure），
// 確保每次 restore 後點擊 render 最新 session 的 state（blocker 4）。
let _lrReviewState = null;

// Default text per stage (plan D-6 table)
const LR_STAGE_DEFAULT_TEXT = {
    0: '讀豹思考中...',
    1: '正在規劃研究方向...',
    2: '正在蒐集資料...',
    3: '分析文筆風格...',
    4: '整理報告格式...',
    5: '正在撰寫報告...',
    6: '整理匯出內容...'
};

// ============================================================================
// D-V3 backref inject — install live closures into state-sync.js for the
// IIFE relocate (commit 11). Top-level call is the ONE permitted side effect per
// module header rationale above.
// ============================================================================
injectStateSyncBackref({
    isLRInProgress,
    getLRSessionId,
    clearLRSessionId,
});

// ============================================================================
// UI helpers
// ============================================================================

/**
 * recollect 退回時清除 Stage 5 產物（section cards + chat 泡泡）。
 * 資料仍在 DB；此純前端 DOM 清除，避免退回後舊章節殘留誤導 user。
 * 不清整個 LR chat（保留 narration 對話脈絡），只移除帶 data-lr-section-index 的元素。
 */
function clearLRStage5Artifacts() {
    const sectionsEl = document.getElementById('lrSections');
    if (sectionsEl) {
        sectionsEl.innerHTML = '';
        sectionsEl.style.display = 'none';
    }
    const chat = document.getElementById('lrChat');
    if (chat) {
        chat.querySelectorAll('[data-lr-section-index]').forEach(el => el.remove());
    }
    console.log('[Live Research] cleared Stage 5 artifacts on stage regression');
}

// Low-relevance / low-keyword warning banner for LR, emitted during prepare()
// (BEFORE the research orchestrator starts). Inserted at the TOP of #lrChat — the
// stable dialog container. #lrChat is only fully cleared by resetLiveResearchUI(),
// which runs in performLiveResearch() BEFORE the SSE stream starts; nothing clears
// #lrChat mid-stream (clearLRStage5Artifacts only removes [data-lr-section-index]
// nodes, which this banner is not), so the banner survives the run.
export function showResearchRelevanceWarning(message, kind) {
    // kind: 'relevance' | 'keyword' — distinct DOM ids so both can show at once.
    const id = kind === 'keyword' ? 'lrLowKeywordWarning' : 'lrLowRelevanceWarning';
    const existing = document.getElementById(id);
    if (existing) existing.remove();

    const warning = document.createElement('div');
    warning.id = id;
    warning.className = kind === 'keyword' ? 'low-keyword-match-warning' : 'low-relevance-warning';
    warning.innerHTML = `<span class="warning-text">${escapeHTML(message)}</span>`;

    const container = document.getElementById('lrChat');
    if (container) container.insertBefore(warning, container.firstChild);
}

export function resetLiveResearchUI() {
    console.log('[Live Research] Resetting UI');
    // v4.0 Commit 7: unconditional clear (resetLiveResearchUI is a fresh-start path,
    // not a background reset — skipIfInflight not used).
    clearLRSessionId();
    // LR Bug 3 fix (2026-05-19): reset inflight flag on fresh LR UI reset.
    // performLiveResearch() flips it back to true immediately after this call.
    setLRInProgress(false);
    const chat = document.getElementById('lrChat');
    if (chat) chat.innerHTML = '';

    const sections = document.getElementById('lrSections');
    if (sections) { sections.innerHTML = ''; sections.style.display = 'none'; }

    const exportEl = document.getElementById('lrExport');
    if (exportEl) { exportEl.innerHTML = ''; exportEl.style.display = 'none'; }

    const reply = document.getElementById('lrCheckpointReply');
    if (reply) reply.style.display = 'none';
    // Bug fix 2026-05-16: reset awaiting flag on UI reset
    _lrAwaitingCheckpointReply = false;

    // C (Codex #3 誤觸 fix)：reset 是 fresh-start / session-switch 的清理路徑，
    // 必須把 stage 追蹤歸零 —— 否則切到新 session 後殘留舊 session 的 stage（如 5），
    // 新 session 正常進 Stage 1 會被誤判為「stage 退回」而誤清。
    _currentLRStage = 0;
    _currentLRActivity = '';

    // Reset all stage dots
    document.querySelectorAll('.lr-stage-dot').forEach(dot => {
        dot.classList.remove('active', 'completed');
    });
    document.querySelectorAll('.lr-stage-connector').forEach(conn => {
        conn.classList.remove('completed');
    });
    document.querySelectorAll('.lr-stage-labels span').forEach(lbl => {
        lbl.classList.remove('active', 'completed');
    });

    // SF-A: clear per-stage review container + remove nav classes/sentinel
    // （dataset.lrNavWired 不清 — listener 讀 _lrReviewState，下次 wireLRStageNavigation 重覆寫；
    //   class 清掉後 dot 不可點，直到下次 wireLRStageNavigation。）
    const reviewEl = document.getElementById('lrStageReview');
    if (reviewEl) { reviewEl.innerHTML = ''; reviewEl.style.display = 'none'; }
    document.querySelectorAll('.lr-stage-dot, .lr-stage-labels span').forEach(el => {
        el.classList.remove('lr-stage-clickable', 'lr-stage-unreached', 'lr-stage-selected');
    });
    _lrReviewState = null;

    // Track D D2b Step 6 (sprint 2026-05-28, fix-up round 1 C-3 + kg-spec §8.4):
    // cross-session LR KG container reset — 防 user 重啟 LR / 切 session 後 KG 殘留
    // (沿 memory/lessons-frontend.md「KG cross-session 殘留 bug」紀律)
    // 操作 #lrKGDisplayContainer + 移除 SVG (D3 render 殘留)
    const lrKGContainer = document.getElementById('lrKGDisplayContainer');
    if (lrKGContainer) {
        lrKGContainer.style.display = 'none';
        const lrKGGraph = document.getElementById('lrKGGraphView');
        if (lrKGGraph) {
            const oldSvg = lrKGGraph.querySelector('svg');
            if (oldSvg) oldSvg.remove();
        }
        const lrKGContent = document.getElementById('lrKGDisplayContent');
        if (lrKGContent) lrKGContent.innerHTML = '';
        const lrKGMeta = document.getElementById('lrKGMetadata');
        if (lrKGMeta) lrKGMeta.textContent = '';
        const lrKGLegend = document.getElementById('lrKGLegend');
        if (lrKGLegend) lrKGLegend.innerHTML = '';
    }
    // Hide restoreBar (若 user 之前 Hide 過 KG, 新 session 不該顯示舊 restore bar)
    const lrKGRestoreBar = document.getElementById('lrKGRestoreBar');
    if (lrKGRestoreBar) lrKGRestoreBar.style.display = 'none';
}

// G-M1 (2026-05-29): 加 options.dataset 參數，讓呼叫端注入 data-* attributes，
// 消除 SSE live_research_section handler 內 inline 重複的 avatar/wrapper DOM 結構。
// 呼叫端可傳 { dataset: { lrSectionIndex: '0' } }，wrapper.dataset 會被一次性 assign。
// 無 options 或空 dataset 時行為與既有完全相同（backward-compat）。
export function addLRChatMessage(type, text, options = {}) {
    const chat = document.getElementById('lrChat');
    if (!chat) return;

    const { dataset = {}, isRealContent = false } = options;   // candidate A: checkpoint opt-in flag

    const avatarMap = { narration: '&#x1F43E;', user: '&#x1F464;', system: '&#x2139;&#xFE0F;', error: '&#x26A0;', checkpoint: '&#x1F43E;', section: '&#x1F4DD;' };
    const avatarHTML = avatarMap[type] || '&#x2022;';

    const wrapper = document.createElement('div');
    wrapper.className = `lr-chat-message ${type}`;
    // 蓋當前 stage 章，讓 serialize 能逐條讀 stage（V9：append 時 _currentLRStage 即當前 stage）
    wrapper.dataset.lrStage = String(_currentLRStage);

    // 注入 dataset attributes（G-M1：讓 section bubble 帶 data-lr-section-index）
    Object.entries(dataset).forEach(([k, v]) => { wrapper.dataset[k] = v; });

    // CANDIDATE A positive allow-list: mark ONLY real conversation content so serialize
    // stores it. {user,narration,section} are always real (auto); checkpoint is real only
    // when the caller opts in (SSE proposal / clarification). All transient boxes stay
    // unmarked → excluded by default. See LR_REAL_CONTENT_TYPES invariant near top of file.
    if (LR_REAL_CONTENT_TYPES.has(type) || (type === 'checkpoint' && isRealContent)) {
        wrapper.dataset.lrContent = '1';   // → data-lr-content="1"
    }

    let bubbleInner = '';
    if (type === 'checkpoint') {
        // text is expected to be raw HTML string (built by showLRCheckpoint)
        bubbleInner = text;
    } else {
        bubbleInner = DOMPurify.sanitize(marked.parse(String(text)));
    }

    if (type === 'user') {
        wrapper.innerHTML = `
            <div class="lr-msg-bubble">${DOMPurify.sanitize(String(text))}</div>
            <div class="lr-msg-avatar">${avatarHTML}</div>`;
    } else {
        wrapper.innerHTML = `
            <div class="lr-msg-avatar">${avatarHTML}</div>
            <div class="lr-msg-bubble">${bubbleInner}</div>`;
    }

    chat.appendChild(wrapper);
    chat.scrollTop = chat.scrollHeight;
}

// ── LR dialog snapshot (DOM serialize at save time + replay state) ──────────

/**
 * Serialize the live #lrChat container into a snapshot entry array.
 * Wraps the pure serializeLRChatRoot with the real DOM + global DOMPurify.
 * Called by saveCurrentSession (via window bridge) on every LR save.
 */
export function serializeLRChatDOM() {
    const chat = document.getElementById('lrChat');
    const snap = serializeLRChatRoot(chat, (typeof window !== 'undefined') ? window.DOMPurify : undefined);
    // 體積保護：超夸張閾值僅 warn，絕不截斷 / 折疊 / 去重（重度 user 是寶貴客戶）。
    try {
        const bytes = JSON.stringify(snap).length;
        if (bytes > 2 * 1024 * 1024) {
            console.warn(`[LR] lr_dialog_snapshot 體積偏大：${bytes} bytes（不截斷，僅提示）`);
        }
    } catch (_) { /* stringify 失敗不阻擋存檔 */ }
    return snap;
}

// 回顧時讀回的 snapshot（loadSavedSession 在 restore 前 setLRLoadedSnapshot）。
let _lrLoadedSnapshot = [];
export function setLRLoadedSnapshot(arr) { _lrLoadedSnapshot = Array.isArray(arr) ? arr : []; }
export function getLRLoadedSnapshot() { return _lrLoadedSnapshot; }

/**
 * Replay a loaded snapshot into the live #lrChat log as conversation bubbles.
 * Pure DOM — NO HTTP, NO /continue, NO pipeline re-run. Used by resume restore
 * for the mid-flight (checkpoint / in_progress) branches so the user sees their
 * prior dialog. (The completed branch keeps its stage-toggle review unchanged.)
 *
 * Each entry: { type, stage, html, dataset, ts } (serializeLRChatRoot shape).
 * The html was already rendered + DOMPurify-sanitized at serialize time
 * (lr-snapshot.js serializeLRChatRoot, C4 boundary #1); _appendReplayedBubbles
 * re-sanitizes on insert (boundary #2) and NEVER re-runs marked.parse.
 *
 * REPLAYS THE COMPLETE SNAPSHOT — including a trailing `type==='checkpoint'`
 * bubble. That trailing checkpoint holds the REAL AI-generated research proposal
 * (the actual options/evidence the user must review), NOT a canned notice.
 * (Verified, 3rd-round Gemini AR + Zoe prod DB query, session b08080f8.)
 *
 * Do NOT drop it. The earlier `dropTrailingCheckpoint` idea was REMOVED — it was
 * based on a false "double-render" premise: it assumed the trailing checkpoint
 * is a duplicate of the box that showLRCheckpoint() redraws at resume. It is NOT.
 * showLRCheckpoint(stage, resumeNotice, …) at the restore call site passes a
 * CANNED resumeNotice ("（從中斷處繼續）…") as its `proposal` arg, which it renders
 * into `.lr-checkpoint-proposal`. So the redrawn box's content = canned operation
 * prompt; the snapshot's trailing checkpoint's content = the real proposal. They
 * are DIFFERENT, with DIFFERENT functions (history/content vs. operation entry-
 * point). Dropping the trailing checkpoint would PERMANENTLY ERASE the only copy
 * of the real proposal (= data loss). Both boxes must coexist.
 *
 * ── R7+R8 BLOCKER FIX: replay-side marking re-applies the allow-list keyed off the
 * serialized `type` AND (for checkpoint) the entry HTML — it does NOT blanket-mark and it
 * does NOT mark checkpoint unconditionally. Two collaborating fixes (see _appendReplayedBubbles):
 *   (R8 BLOCKER 1) `delete replayDataset.lrContent` BEFORE copying the entry dataset, so an
 *      inherited persisted lrContent can't bypass the judge — the judge is the SOLE authority.
 *   (R8 BLOCKER 2) `_isReplayRealContent(type, html)` (imported from lr-snapshot.js) is the
 *      content-aware judge: {user,narration,section} always real; `checkpoint` real ONLY if its
 *      html lacks a restore-canned operation string; {system,assistant,error} never real.
 * Net: real content retained, legacy garbage (system/assistant/error AND canned checkpoint)
 * self-heals on the next dirty-save re-serialize (no migration). See "Legacy-polluted snapshot
 * handling" in the plan.
 *
 * @param {Array} snap  loaded snapshot from getLRLoadedSnapshot()
 * @returns {number} count of bubbles replayed
 */
function _replayLRSnapshotIntoChat(snap) {
    if (!snapshotHasReplayableEntries(snap)) return 0;
    // ALL entry types go through the SAME path: _appendReplayedBubbles (mirrors
    // renderLRStageDialog). The stored html was already rendered + DOMPurify-
    // sanitized at serialize time; _appendReplayedBubbles re-sanitizes on insert.
    // NEVER re-run marked.parse here — there is NO per-type branching (do not add
    // a "checkpoint = raw HTML, others = parse" split; renderLRStageDialog, the
    // canonical mirror, treats every type uniformly).
    const bubbles = [];
    for (const e of snap) {
        if (!e || typeof e !== 'object') continue;
        // Consume fragile redundancy: real serialize entries carry BOTH a top-level
        // `stage` and `dataset.lrStage`. Replay preserves dataset (so lrStage survives),
        // but if a future/legacy entry has dataset.lrStage missing while top-level stage
        // is present, restore it here so stage-grouping never silently regresses. This is
        // DEFENSIVE on real data (real entries already carry both).
        const ds = { ...(e.dataset || {}) };
        if (ds.lrStage === undefined && Number.isInteger(e.stage)) ds.lrStage = String(e.stage);
        bubbles.push({ type: e.type || 'system', html: e.html || '', dataset: ds });
    }
    return _appendReplayedBubbles(bubbles);   // single fragment append + one scroll (I4)
}

/**
 * Append a batch of pre-serialized snapshot bubbles to #lrChat in ONE pass:
 * build every wrapper into a DocumentFragment, append the fragment once, scroll
 * once. This mirrors renderLRStageDialog, which also collects into a fragment and
 * appends a single time — NOT per-bubble. (I4: a per-bubble scroll would thrash
 * layout for a 51-bubble session.)
 *
 * Does NOT re-run marked.parse (stored html is already rendered HTML); DOMPurify
 * re-sanitizes (boundary #2) before insertion. Bubble markup is identical to
 * renderLRStageDialog.
 *
 * @param {Array<{type:string, html:string, dataset:object}>} bubbles
 * @returns {number} count appended
 */
function _appendReplayedBubbles(bubbles) {
    const chat = document.getElementById('lrChat');
    if (!chat) return 0;
    const avatarMap = { narration: '&#x1F43E;', user: '&#x1F464;', system: '&#x2139;&#xFE0F;', error: '&#x26A0;', checkpoint: '&#x1F43E;', section: '&#x1F4DD;' };
    const frag = document.createDocumentFragment();
    let n = 0;
    for (const b of bubbles) {
        const type = b.type || 'system';
        const avatar = avatarMap[type] || '&#x2022;';
        const cleanHtml = (typeof window !== 'undefined' && window.DOMPurify)
            ? window.DOMPurify.sanitize(b.html || '')
            : (b.html || '');
        const wrapper = document.createElement('div');
        wrapper.className = `lr-chat-message ${type}`;
        // ── R8 BLOCKER 1 FIX — STRIP the inherited `lrContent` from the entry's dataset
        // BEFORE copying it into the new wrapper, so the type/content-aware judge below is the
        // SOLE authoritative source of data-lr-content. serializeLRChatRoot pushes each entry
        // with `dataset: { ...wrapper.dataset }` for round-trip fidelity (so lrStage /
        // lrSectionIndex survive). After this plan's serializer marks real content, a persisted
        // entry's dataset ALSO carries lrContent:"1". If we copied the dataset wholesale, that
        // inherited lrContent would land on the new wrapper BEFORE the type-keyed judge runs.
        // The judge only ADDS lrContent for real content — it does NOT remove an inherited one.
        // So a LEGACY `type:'system'` (or legacy canned `checkpoint`) entry whose persisted
        // dataset carries lrContent:"1" would replay WITH data-lr-content, ride past the
        // [data-lr-content] serialize filter, and SURVIVE = self-heal defeated. Deleting it here
        // makes the type/content judge the only authority → legacy garbage truly heals.
        const replayDataset = { ...(b.dataset || {}) };
        delete replayDataset.lrContent;
        Object.entries(replayDataset).forEach(([k, v]) => { wrapper.dataset[k] = v; });
        // CANDIDATE A + R7/R8 BLOCKER FIX — re-apply the SAME positive allow-list to replayed
        // bubbles, keyed off the serialized `type` AND (for checkpoint) the entry html. DO NOT
        // blanket-mark every replay bubble, and DO NOT unconditionally mark checkpoint. LEGACY
        // snapshots (written by the OLD store-everything serializer — the very bug this plan
        // fixes) can contain transient garbage (prod: 1 session 15 `system` entries + 1 canned
        // `checkpoint`). Blanket/unconditional marking would bleach garbage into permanent real
        // content (replay → mark → next dirty-save serializes it WITH data-lr-content → sticks
        // forever). So mark via the CONTENT-AWARE judge:
        //   • {user, narration, section} → always real → mark.
        //   • checkpoint → REAL only if its html lacks a restore-canned operation string
        //       (canned checkpoint → unmarked → self-heals like system).
        //   • {system, assistant, error} → legacy transient garbage → never mark → self-heal.
        if (_isReplayRealContent(type, b.html)) {
            wrapper.dataset.lrContent = '1';
        }
        if (type === 'user') {
            wrapper.innerHTML = `<div class="lr-msg-bubble">${cleanHtml}</div><div class="lr-msg-avatar">${avatar}</div>`;
        } else {
            wrapper.innerHTML = `<div class="lr-msg-avatar">${avatar}</div><div class="lr-msg-bubble">${cleanHtml}</div>`;
        }
        frag.appendChild(wrapper);
        n++;
    }
    chat.appendChild(frag);              // single DOM mutation
    chat.scrollTop = chat.scrollHeight;  // single scroll (not per-bubble)
    return n;
}

/**
 * Re-trigger a session save at a meaningful LR stop point (checkpoint / export)
 * so lr_dialog_snapshot captures the dialog streamed since the opening save.
 *
 * markSessionDirty() first: saveCurrentSession early-returns when not dirty
 * (session-coordinator.js:69); reaching a checkpoint/export IS new content.
 * Empty-overwrite guard (Task 1) protects against transient-empty clobber.
 *
 * D-7: `triggeringLRSid` is captured at stream start (Step 1b) and passed in,
 * so a stale background stream (old run after a session switch) skips correctly.
 *
 * D-6: export passes {immediate:true} (bypass 2s debounce). NOT a sync flush —
 * returns the underlying promise so the export caller can await dispatch.
 *
 * @param {string} reason  'checkpoint' | 'export'
 * @param {{immediate?: boolean, triggeringLRSid?: string|null}} [opts]
 * @returns {Promise|undefined}  the save promise when available (export awaits it)
 */
function _saveLRSnapshot(reason, opts = {}) {
    try {
        if (!shouldSaveLRSnapshot(opts.triggeringLRSid ?? null, getCurrentLoadedSessionId(), window.matchSessionId)) {
            console.warn('[LR] _saveLRSnapshot skipped: triggering stream session != loaded session (stale background stream). reason=' + reason + ' triggeringLRSid=' + opts.triggeringLRSid);
            return;
        }
        markSessionDirty();  // defeat dirty-guard early-return (session-coordinator.js:69)
        if (typeof window.saveCurrentSession === 'function') {
            return window.saveCurrentSession({ immediate: !!opts.immediate });  // D-6: forward {immediate}; return promise for export await
        }
        console.warn('[LR] _saveLRSnapshot: window.saveCurrentSession unavailable, snapshot not persisted. reason=', reason);
    } catch (e) {
        console.error('[LR] _saveLRSnapshot failed (reason=' + reason + '):', e);
    }
}

// window bridge（比照既有 window.saveCurrentSession pattern）— 讓 core/session-coordinator
// 與 news-search.js 取用而不引入 core→features import 循環。
if (typeof window !== 'undefined') {
    window.serializeLRChatDOM = serializeLRChatDOM;
    window.setLRLoadedSnapshot = setLRLoadedSnapshot;
    window.getLRLoadedSnapshot = getLRLoadedSnapshot;
}

export function showLRTypingIndicator() {
    const chat = document.getElementById('lrChat');
    if (!chat) return;
    hideLRTypingIndicator();
    const ind = document.createElement('div');
    ind.id = 'lrTypingIndicator';
    ind.className = 'lr-typing-indicator active';
    const text = _currentLRActivity || LR_STAGE_DEFAULT_TEXT[_currentLRStage] || LR_STAGE_DEFAULT_TEXT[0];
    ind.innerHTML = `
        <span class="lr-typing-dot"></span>
        <span class="lr-typing-dot"></span>
        <span class="lr-typing-dot"></span>
        <span id="lrTypingText">${DOMPurify.sanitize(String(text))}</span>`;
    chat.appendChild(ind);
    chat.scrollTop = chat.scrollHeight;
}

export function hideLRTypingIndicator() {
    // Remove all (defensive against any leak)
    document.querySelectorAll('#lrTypingIndicator').forEach(el => el.remove());
}

export function updateLRTypingIndicatorText() {
    const textEl = document.getElementById('lrTypingText');
    if (!textEl) return;
    const text = _currentLRActivity || LR_STAGE_DEFAULT_TEXT[_currentLRStage] || LR_STAGE_DEFAULT_TEXT[0];
    textEl.textContent = String(text);
}

// Reset typing-indicator state when starting/finishing a LR run
export function resetLRTypingState() {
    _currentLRStage = 0;
    _currentLRActivity = '';
}

// D-6: Derive activity text from narration string.
// Looks for "開始蒐集 X" / "X 蒐集完成" / writer pattern hints.
export function deriveActivityFromNarration(narrationText) {
    if (!narrationText) return '';
    const s = String(narrationText).trim();

    // Stage 2 — "開始蒐集 X" → "正在蒐集資料：X..."
    let m = s.match(/^開始蒐集\s*(.+?)$/);
    if (m) return `正在蒐集資料：${m[1].trim()}...`;

    // Stage 2 — "X 蒐集完成" → "X 蒐集完成..."
    m = s.match(/^(.+?)\s*蒐集完成$/);
    if (m) return `${m[1].trim()} 蒐集完成...`;

    // Stage 5 — writer hints (fallback; UX-4 writer_status preferred)
    m = s.match(/正在寫.*?第\s*(\d+)\s*\/\s*(\d+)\s*段/);
    if (m) return `正在寫第 ${m[1]}/${m[2]} 段...`;

    return '';
}

export function updateLRStageProgress(stage) {
    const stageNum = parseInt(stage, 10);
    if (!stageNum || stageNum < 1 || stageNum > 6) return;
    console.log('[Live Research] Stage change:', stageNum);

    document.querySelectorAll('.lr-stage-dot').forEach(dot => {
        const n = parseInt(dot.dataset.stage, 10);
        dot.classList.remove('active', 'completed');
        if (n < stageNum) dot.classList.add('completed');
        else if (n === stageNum) dot.classList.add('active');
    });

    // Connectors: complete connectors before the active stage
    const connectors = document.querySelectorAll('.lr-stage-connector');
    connectors.forEach((conn, idx) => {
        // connector idx 0 is between stage 1 and 2, etc.
        conn.classList.toggle('completed', idx + 1 < stageNum);
    });

    // Labels
    document.querySelectorAll('.lr-stage-labels span').forEach(lbl => {
        const n = parseInt(lbl.dataset.stage, 10);
        lbl.classList.remove('active', 'completed');
        if (n < stageNum) lbl.classList.add('completed');
        else if (n === stageNum) lbl.classList.add('active');
    });
}

// ============================================================================
// G3：Legacy session gate — v1 session 唯讀 UI 鎖定 + Modal CTA
// ============================================================================

// 當前 session 是否為 legacy（v1）唯讀
let _lrSessionIsLegacy = false;
// 最後載入的 LR session 原始 query（供 modal 「用同 query 開新研究」功能使用）
let _lrLegacySessionQuery = '';
// 最後載入的 legacy LR session state（供 modal 「匯出當前報告」從 written_sections 組 markdown）
let _lrLegacySessionState = null;

export function setLRLegacyMode(isLegacy, query, state = null) {
    _lrSessionIsLegacy = !!isLegacy;
    _lrLegacySessionQuery = query || '';
    // 只在 legacy 時存 state：_lrLegacySessionState 是 legacy-only 變數，
    // v2 session 載入不得殘留 state 進來（adversarial review S5-7 Codex should-fix）
    _lrLegacySessionState = (isLegacy && state && typeof state === 'object') ? state : null;
}

export function isLRSessionLegacy() {
    return _lrSessionIsLegacy;
}

/**
 * G3：鎖定舊版 LR session 的 revise/continue UI
 * 呼叫時機：loadSavedSession 中偵測到 schema_version < 2 時
 */
export function lockLRUIForLegacySession() {
    // 鎖定 checkpoint reply 區
    const replyEl = document.getElementById('lrCheckpointReply');
    if (replyEl) {
        const input = document.getElementById('lrReplyInput');
        const replyBtn = document.getElementById('lrBtnReply');
        const autoBtn = document.getElementById('lrBtnAutoContine');
        if (input) {
            input.disabled = true;
            input.placeholder = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
        }
        // S5-7 第二層死端修法：標準 DOM 下 disabled 的 <button> 不 dispatch click，
        // 若維持 disabled=true 則下方 click listener 為 dead binding → 點按鈕開不出 modal。
        // 改法（維持 spec G3 grey-out 視覺、不引入新決策）：不設 disabled 屬性，
        // 保留 opacity / cursor / tooltip 視覺鎖定 — click 行為本來就只開 modal、不送 continue。
        if (replyBtn) {
            replyBtn.title = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
            replyBtn.style.opacity = '0.4';
            replyBtn.style.cursor = 'not-allowed';
            replyBtn.addEventListener('click', (e) => { e.stopPropagation(); showLRReadonlyModal(); }, { once: false });
        }
        if (autoBtn) {
            autoBtn.title = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
            autoBtn.style.opacity = '0.4';
            autoBtn.style.cursor = 'not-allowed';
            autoBtn.addEventListener('click', (e) => { e.stopPropagation(); showLRReadonlyModal(); }, { once: false });
        }
        // backward-nav 退回/重來按鈕納入 legacy 鎖（plan: lr-backward-nav, Task 8）。
        // 後端 methods/live_research.py legacy gate（schema_version < 2）本來就擋 continue；
        // 此處只補前端視覺鎖，避免 legacy session 看到可點的退回/重來按鈕（點了被擋但 UX 突兀）。
        const navBackBtn = document.getElementById('lrBtnNavBack');
        const navRestartBtn = document.getElementById('lrBtnNavRestart');
        [navBackBtn, navRestartBtn].forEach(btn => {
            if (btn) {
                btn.title = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
                btn.style.opacity = '0.4';
                btn.style.cursor = 'not-allowed';
                btn.addEventListener('click', (e) => { e.stopPropagation(); showLRReadonlyModal(); }, { once: false });
            }
        });
        // S5-7 blocker 修法：legacy 載入路徑跳過 restoreLRCheckpointFromState，
        // showLRCheckpoint 不會跑 → reply bar 維持靜態 display:none → 被鎖按鈕
        // 不可見 → modal（唯讀說明 + 匯出入口）無真人入口。
        // 照 spec G3（design 2026-05-28 line 250-251）：reply UI 以 disabled 狀態
        // 「可見」呈現，user 點 grey-out 按鈕 → modal CTA，不形成 dead-end。
        replyEl.style.display = '';
    }
}

/**
 * 把 legacy LR session state 的 written_sections 組成可下載的 markdown 報告。
 * legacy v1 state 不可被 orchestrator 繼續跑，但 written_sections 含完整章節內容，
 * 可用於前端唯讀匯出（CEO 2026-05-28 拍板：v1 session 允許瀏覽 / export）。
 * 注意：重建版非 v2 後端 Stage 6 完整報告 — 檔頭標註揭露差異（S5-7 review）。
 *
 * @param {object|null} state  legacy liveResearchState
 * @returns {string} markdown 字串；無可匯出內容時回傳 ''（caller 負責 fallback 提示）
 */
function buildLegacyReportMarkdown(state) {
    if (!state || typeof state !== 'object') return '';
    const rawSections = Array.isArray(state.written_sections) ? state.written_sections : [];
    // filter-first：編號以「有內容的章節」緊湊計算（與驗證 test 同邏輯，S5-7 nit 統一）
    const sections = rawSections.filter(s => s && s.content);
    if (sections.length === 0) return '';
    const parts = sections.map((section, idx) => {
        const title = String(section.title || `第 ${idx + 1} 段`);
        const content = String(section.content);
        // sources_used 為 v1 證據池編號（無完整參考清單可對映，仍保留供查證脈絡）
        const srcs = (Array.isArray(section.sources_used) && section.sources_used.length > 0)
            ? `\n\n（本節引用資料來源編號：${section.sources_used.join(', ')}）`
            : '';
        return `## ${title}\n\n${content}${srcs}`;
    });
    const header = _lrLegacySessionQuery
        ? `# ${String(_lrLegacySessionQuery)}\n\n`
        : '# Live 研究報告\n\n';
    const note = '> 本報告由封存的舊版研究 session 資料重建，未含新版匯出的完整參考來源清單。\n\n';
    return header + note + parts.join('\n\n---\n\n') + '\n';
}

/**
 * G3：顯示 legacy session 唯讀 modal CTA（CEO 拍板 2026-05-28）
 */
export function showLRReadonlyModal() {
    // 避免重複開 modal；重開時恢復預設 body（S5-7：fallback 錯誤訊息不可跨 session 殘留）
    const existingModal = document.getElementById('lrLegacyReadonlyModal');
    if (existingModal) {
        const existingBody = existingModal.querySelector('.lr-modal-body');
        if (existingBody) {
            existingBody.innerHTML = `
                系統已升級研究 pipeline，本 session 為舊版 schema，無法繼續編輯或新增章節。<br>
                建議先匯出當前報告，再用同 query 開啟新研究 session（享受新版 grounding 紀律）。`;
        }
        existingModal.style.display = 'flex';
        return;
    }

    const modal = document.createElement('div');
    modal.id = 'lrLegacyReadonlyModal';
    modal.className = 'lr-modal-overlay';
    modal.innerHTML = `
        <div class="lr-modal-box">
            <div class="lr-modal-title">此 session 已升級為唯讀</div>
            <div class="lr-modal-body">
                系統已升級研究 pipeline，本 session 為舊版 schema，無法繼續編輯或新增章節。<br>
                建議先匯出當前報告，再用同 query 開啟新研究 session（享受新版 grounding 紀律）。
            </div>
            <div class="lr-modal-actions">
                <button class="lr-btn-primary" id="lrModalBtnExport">匯出當前報告</button>
                <button class="lr-btn-secondary" id="lrModalBtnNewSession">用同 query 開新研究</button>
                <button class="lr-btn-cancel" id="lrModalBtnCancel">取消</button>
            </div>
        </div>`;

    document.body.appendChild(modal);

    // 匯出當前報告：legacy session 無 active LR DOM（#lrBtnDownload 不存在），
    // 直接從暫存的 _lrLegacySessionState.written_sections 組 markdown 下載。
    // S5-7 blocker 修法：絕不 fallback 去點 #lrBtnDownload / #lrBtnCopyExport —
    // legacy 載入路徑不會跑 resetLiveResearchUI（resetToHome 不清 #lrExport），
    // 同頁殘留的按鈕閉包綁著上一個 v2 run 的內容，點下去會匯出別的 session 的報告。
    document.getElementById('lrModalBtnExport')?.addEventListener('click', () => {
        const md = buildLegacyReportMarkdown(_lrLegacySessionState);
        if (md) {
            const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'live-research-report.md';
            a.click();
            URL.revokeObjectURL(a.href);
            modal.style.display = 'none';
            return;
        }
        // 不可 silent fail：無可匯出內容時給 user-facing 明確訊息（不關 modal，讓 user 看到）
        const bodyEl = modal.querySelector('.lr-modal-body');
        if (bodyEl) {
            bodyEl.innerHTML = '<span style="color:#c0392b;">此 session 沒有可匯出的報告內容（章節資料缺失或尚未產生）。建議用同 query 開啟新研究。</span>';
        } else {
            // bodyEl 找不到：modal DOM 結構異常，降級為 alert 確保 user 看到錯誤
            // （CLAUDE.md「降級必有 user-facing 訊息」— 不可 silent fail）
            console.error('[Live Research] Legacy export: .lr-modal-body not found — falling back to alert');
            alert('無法匯出：報告內容不可用。建議重整頁面後再試，或用同 query 開啟新研究。');
        }
    });

    // 用同 query 開新研究：預填 query 到搜尋框並切換到 live_research 模式
    document.getElementById('lrModalBtnNewSession')?.addEventListener('click', () => {
        modal.style.display = 'none';
        const searchInput = document.getElementById('searchInput');
        if (searchInput && _lrLegacySessionQuery) {
            searchInput.value = _lrLegacySessionQuery;
        }
        // 切換到 live_research 模式（觸發 mode button click）
        const lrModeBtn = document.querySelector('.mode-btn-inline[data-mode="live_research"]');
        if (lrModeBtn) lrModeBtn.click();
        // 提示使用者現在可以開始新研究
        console.info('[Live Research] Legacy session — user redirected to new research with query:', _lrLegacySessionQuery);
    });

    // 取消
    document.getElementById('lrModalBtnCancel')?.addEventListener('click', () => {
        modal.style.display = 'none';
    });
}

/**
 * renderEvidenceList — 把 evidence_list payload 渲染為可摺疊 HTML。
 *
 * P0 #5: 設計邊界已 CEO 拍板：
 *   邊界 A (llm_knowledge): Option a — 顯示「AI 背景知識」label，無超連結
 *   邊界 B (year): Option a — published_at 非 null 取前 4 字顯示
 *
 * @param {Array<{id,title,url,source_domain,published_at,source}>} evidenceList
 * @param {string} topicName  — 主題名稱（用於 summary label）
 * @param {number} [evidenceTotal]  — evidence_pool 完整筆數（後端帶來，與 narration「共蒐集到 N 筆」同源）。
 *                                    顯示的 evidenceList 只是節選；total > 顯示數時標明「節選 vs 總量」避免誤解。
 * @returns {string} HTML string (caller must DOMPurify.sanitize before insert)
 */
function renderEvidenceList(evidenceList, topicName, evidenceTotal) {
    if (!Array.isArray(evidenceList) || evidenceList.length === 0) {
        return '';
    }

    const itemsHTML = evidenceList.map(e => {
        const isLLM = e.source === 'llm_knowledge';
        const titleSafe = escapeHTML(String(e.title || ''));
        const domainLabel = isLLM
            ? '<span class="lr-evidence-source-label lr-evidence-ai-tag">AI 背景知識</span>'
            : `<span class="lr-evidence-source-label">${escapeHTML(String(e.source_domain || e.source || ''))}</span>`;

        // 邊界 B: 顯示年份
        let yearLabel = '';
        if (e.published_at && typeof e.published_at === 'string' && e.published_at.length >= 4) {
            yearLabel = `<span class="lr-evidence-year">${escapeHTML(e.published_at.slice(0, 4))}</span>`;
        }

        if (isLLM) {
            // 邊界 A: llm_knowledge — 不可點，顯示 AI 背景知識 tag
            return `<li class="lr-evidence-item lr-evidence-ai">
                <span class="lr-evidence-title">${titleSafe}</span>
                ${domainLabel}${yearLabel}
            </li>`;
        } else {
            // internal / web / wiki — 真實 URL，可點
            const urlSafe = escapeHTML(String(e.url || ''));
            return `<li class="lr-evidence-item">
                <a class="lr-evidence-link" href="${urlSafe}" target="_blank" rel="noopener noreferrer">${titleSafe}</a>
                ${domainLabel}${yearLabel}
            </li>`;
        }
    }).join('');

    // 顯示節選 vs 總量：total（後端 evidence_pool 完整筆數）大於目前顯示的子集時，
    // 標明「共 N 筆，顯示 M 筆（節選）」，避免 user 誤以為只蒐集到 M 筆
    // （後端 narration 用的是完整 total，兩處數字必須一致）。
    const shownCount = evidenceList.length;
    const total = (typeof evidenceTotal === 'number' && evidenceTotal > shownCount)
        ? evidenceTotal : null;
    const countLabel = total
        ? `共 ${total} 筆，顯示 ${shownCount} 筆（節選）`
        : `${shownCount} 筆資料`;
    const summaryLabel = topicName
        ? escapeHTML(String(topicName)) + ` — ${countLabel}`
        : countLabel;

    return `<details class="lr-evidence-details">
        <summary class="lr-evidence-summary">${summaryLabel}</summary>
        <ul class="lr-evidence-list">${itemsHTML}</ul>
    </details>`;
}

export function showLRCheckpoint(stage, proposal, autoOption, evidenceList, showNewSampleButton, evidenceTotal, options = {}) {
    console.log('[Live Research] Checkpoint at stage', stage, '— proposal:', proposal);
    // Ensure proposal is always rendered as string (guards against [object Object])
    const proposalText = typeof proposal === 'object' && proposal !== null
        ? (proposal.proposal || proposal.text || JSON.stringify(proposal, null, 2))
        : String(proposal || '');
    const proposalHTML = DOMPurify.sanitize(marked.parse(proposalText));
    const autoLabel = autoOption || '讀豹自動決定';

    // P0 #5: render evidence list if present (stage 1 / stage 2 checkpoint)
    let evidenceHTML = '';
    if (Array.isArray(evidenceList) && evidenceList.length > 0) {
        // Group by topic: stage 1 sends a flat list, render as single collapsible
        const rawEvidenceHTML = renderEvidenceList(evidenceList, '', evidenceTotal);
        evidenceHTML = DOMPurify.sanitize(rawEvidenceHTML);
    }

    const bubbleHTML = `
        <div class="lr-checkpoint-label">Checkpoint — 階段 ${stage}</div>
        <div class="lr-checkpoint-proposal">${proposalHTML}</div>
        ${evidenceHTML}`;

    // candidate A: forward the caller's real-content intent to the bubble. The SSE real
    // proposal passes { isRealContent: true } (store it); restore canned boxes pass nothing
    // (transient → unmarked → excluded). Default {} keeps existing callers transient-by-default.
    addLRChatMessage('checkpoint', bubbleHTML, { isRealContent: options.isRealContent === true });

    // Show reply UI
    const replyEl = document.getElementById('lrCheckpointReply');
    if (replyEl) {
        replyEl.style.display = '';
        const input = document.getElementById('lrReplyInput');
        if (input) { input.value = ''; input.focus(); }
        const autoBtn = document.getElementById('lrBtnAutoContine');
        if (autoBtn) autoBtn.textContent = (typeof autoLabel === 'string' && autoLabel) ? autoLabel : '讀豹決定';
        const newSampleBtn = document.getElementById('lrBtnNewSample');
        if (newSampleBtn) {
            // 僅 Stage 3 風格 checkpoint（後端帶 show_new_sample_button=true）顯示此按鈕。
            // resume / 其他 stage：showNewSampleButton 為 undefined/false → hidden（安全預設）。
            newSampleBtn.style.display = showNewSampleButton ? '' : 'none';
        }
        // backward-nav 退回/重來按鈕顯示 gate（plan: lr-backward-nav, #5 + #7）。
        // navAllowed = (stage 2-5)：Stage 1 無上一階段；Stage 6+/completed 不允許退已匯出
        //   （#7 CEO 拍板 v1 不允許退 Stage 6）。showLRCheckpoint 只在真正 checkpoint 被呼叫，
        //   completed session 走 resume-classify → readonly，不進此 path；stage>=6 為防禦性 gate。
        // 後端 continue_from_checkpoint 另有 stage_status != "checkpoint" gate → 前後端雙層。
        const navAllowed = (stage >= 2 && stage <= 5);
        const backBtn = document.getElementById('lrBtnNavBack');
        if (backBtn) backBtn.style.display = navAllowed ? '' : 'none';
        const restartBtn = document.getElementById('lrBtnNavRestart');
        if (restartBtn) restartBtn.style.display = navAllowed ? '' : 'none';
        // recollect 按鈕：只在 Stage 5 顯示（recollect 後端僅在 _handle_stage_5_response 接線；
        // 比 backward-nav 的 stage 2-5 窄）。completed session 走 resume-classify → readonly，
        // 不進 showLRCheckpoint，故此 gate 不需額外擋 completed。
        const recollectBtn = document.getElementById('lrBtnRecollect');
        if (recollectBtn) recollectBtn.style.display = (stage === 5) ? '' : 'none';
    }
    // Bug fix 2026-05-16: mark we are awaiting reply
    _lrAwaitingCheckpointReply = true;
}

// ============================================================================
// O2 / O2-TF: LR-native inline citation linker + Text Fragment highlight
// ============================================================================

// escapeHtmlAttr / ANCHOR_LEN / MIN_QUOTE / LOW_UNIQUENESS / encFrag /
// buildTextFragmentUrl / buildCitationHref 已抽至共用模組 text-fragment.js
// （commit c236d8b9 落地後抽出，逐字搬移、邏輯未變；由 search.js 卡片「閱讀全文」共用）。
// addLRCitationLinks 留在本檔（LR-native，耦合 citation-urn / citation-private / [N]=eid），
// 改呼叫 import 進來的 buildCitationHref / escapeHtmlAttr（見檔頭 import）。

/**
 * O2: LR-native inline citation 點擊回溯 + O2-TF text fragment highlight。
 * 把 inline citation token（numeric [N]，N = eid）依 citation_format 轉成可點元素。
 *
 * 不沿用 deep-research.js 的 addCitationLinks：DR 假設 [N] = flat sources array
 * 的 1-based 位置；LR 的 [N] 是稀疏 evidence-pool id（eid），且 author_year/footnote
 * 模式根本沒有 [N] token。_render_section_citations numeric 分支 return f"[{eid}]"
 * → N = eid 直接，citationSources[String(num)] 直接命中正確來源。
 *
 * @param {string} html  marked.parse 後、DOMPurify 前的 HTML 字串
 * @param {Object} citationSources  eid(str) -> {url,title,domain,quote}
 * @param {string} citationFormat  'numeric'|'author_year'|'footnote'|'none'|undefined
 * @returns {string} 轉換後的 HTML（仍會被 caller DOMPurify sanitize）
 */
function addLRCitationLinks(html, citationSources, citationFormat) {
    if (!html) return html;
    if (!citationSources || typeof citationSources !== 'object') return html;
    // 只有 numeric 模式有可逐 token 對應的 [N]=eid；其餘格式回原樣。
    // （undefined format → 放行進 [N] regex；author_year 內文是 (作者,年份) 天然 no-op）
    if (citationFormat && citationFormat !== 'numeric') return html;

    return html.replace(/\[(\d+)\]/g, (match, num) => {
        const src = citationSources[String(num)];
        if (!src) {
            // eid 不在 pool（phantom / 已被 references block 標「來源遺失」）→ 不亂連
            return `<span class="citation-no-link" title="來源暫無連結">[${num}]</span>`;
        }
        const url = src.url || '';
        if (url.startsWith('urn:llm:knowledge:')) {
            const topic = url.replace('urn:llm:knowledge:', '');
            return `<span class="citation-urn" title="讀豹背景知識：${escapeHtmlAttr(topic)}">[${num}]<sup>讀豹</sup></span>`;
        }
        if (url.startsWith('private://')) {
            return `<span class="citation-private" title="私人文件來源">[${num}]<sup>\u{1F4C1}</sup></span>`;
        }
        if (url) {
            const { href, textfrag } = buildCitationHref(src);
            const baseTitle = src.title ? src.title : `來源 ${num}`;
            // Decision 4 中性文案：generated-unknown 不承諾 highlight（spike 證必要非充分）
            const title = textfrag === 'generated-unknown'
                ? `${baseTitle}（開啟原文；瀏覽器支援時會嘗試定位引用段落）`
                : `${baseTitle}（開啟原文頁首）`;
            // escapeHtmlAttr：href/title 經 attribute 跳脫，杜絕 attribute injection（Decision 6）
            // rel=noopener noreferrer：reverse tabnabbing + 引用片段不洩漏到 Referer
            return `<a href="${escapeHtmlAttr(href)}" target="_blank" `
                 + `rel="noopener noreferrer" class="citation-link" `
                 + `data-textfrag="${escapeHtmlAttr(textfrag)}" `
                 + `title="${escapeHtmlAttr(title)}">[${num}]</a>`;
        }
        return `<span class="citation-no-link" title="來源暫無連結">[${num}]</span>`;
    });
}

// ============================================================================
// LR per-stage review — 資料 contract 解析層
// (stage_state.py to_dict: *_json 欄位是 model_dump_json() 字串，必須先 parse；
//  但主路徑報告讀 final_report_markdown 整份字串，不 parse、不重組。)
// ============================================================================

/**
 * 安全解析 stage_state 的 *_json 字串欄位（fallback / per-stage renderer 用）。
 * @param {*} raw  原始值（預期 string；空字串/null/undefined 代表「此階段無資料」）
 * @param {string} label  欄位語意（錯誤訊息用）
 * @returns {Object|Array|null}  解析結果；無資料回 null
 * @throws {Error}  raw 是非空字串但 JSON.parse 失敗時拋（不可 silent fail）
 */
function parseLRJsonField(raw, label) {
    if (raw === null || raw === undefined || raw === '') return null;
    if (typeof raw === 'object') return raw;  // 防禦：萬一上游已 parse
    try {
        return JSON.parse(raw);
    } catch (err) {
        throw new Error(`[LR Review] ${label} JSON 解析失敗: ${err.message}`);
    }
}

/** 取得回顧容器；clear=true 清空既有內容。 */
function getLRReviewContainer(clear = true) {
    const el = document.getElementById('lrStageReview');
    if (!el) {
        console.warn('[LR Review] #lrStageReview container not found');
        return null;
    }
    el.style.display = '';
    if (clear) el.innerHTML = '';
    return el;
}

/**
 * 回顧 lazy render：把某 stage 的 snapshot 對話塞進 #lrStageReview。
 * 純 innerHTML 重建（重播前再過 DOMPurify=C4 邊界二）；絕不呼 showLRCheckpoint（V11 副作用：
 * 開 reply UI / focus / 設 _lrAwaitingCheckpointReply）。
 */
function renderLRStageDialog(stageNum, snapshot) {
    const container = getLRReviewContainer(true);     // 取 #lrStageReview、設可見、清空
    if (!container) return;
    const entries = lrSnapshotForStage(snapshot, stageNum);
    if (!entries.length) {
        container.innerHTML = `<div class="lr-review-empty">此階段無對話紀錄。</div>`;  // 不 silent
        return;
    }
    const avatarMap = { narration: '&#x1F43E;', user: '&#x1F464;', system: '&#x2139;&#xFE0F;', error: '&#x26A0;', checkpoint: '&#x1F43E;', section: '&#x1F4DD;' };
    const frag = document.createDocumentFragment();
    for (const e of entries) {
        const wrapper = document.createElement('div');
        wrapper.className = `lr-chat-message ${e.type}`;
        Object.entries(e.dataset || {}).forEach(([k, v]) => { wrapper.dataset[k] = v; });
        const avatar = avatarMap[e.type] || '&#x2022;';
        const cleanHtml = (typeof window !== 'undefined' && window.DOMPurify)
            ? window.DOMPurify.sanitize(e.html || '')      // C4 邊界二
            : (e.html || '');
        if (e.type === 'user') {
            wrapper.innerHTML = `<div class="lr-msg-bubble">${cleanHtml}</div><div class="lr-msg-avatar">${avatar}</div>`;
        } else {
            wrapper.innerHTML = `<div class="lr-msg-avatar">${avatar}</div><div class="lr-msg-bubble">${cleanHtml}</div>`;
        }
        frag.appendChild(wrapper);   // 絕不呼 showLRCheckpoint（V11 副作用）
    }
    container.innerHTML = '';
    container.appendChild(frag);
}

/** 此階段無資料（與「解析失敗」區分）。 */
function lrReviewEmptyNotice(label) {
    return `<div class="lr-review-empty">此階段（${escapeHTML(String(label))}）沒有可回顧的資料。`
        + `可能該研究在此階段前結束。</div>`;
}

/** 此階段資料解析失敗（明確錯誤，不可偽裝成空白/無資料）。 */
function lrReviewErrorNotice(label, err) {
    console.error('[LR Review] render error', label, err);
    return `<div class="lr-review-error">此階段（${escapeHTML(String(label))}）資料解析失敗，`
        + `無法顯示。錯誤：${escapeHTML(String(err && err.message || err))}</div>`;
}

// ----------------------------------------------------------------------------
// Task 1b: citationSources（主路徑+fallback）+ references_block（僅 fallback）
// 後端 _build_citation_sources / _build_references_block 的 client port。
// ----------------------------------------------------------------------------

/**
 * 前端等價重建 citationSources（後端 _build_citation_sources 的 client port）。
 * evidence_pool（已 parse）→ { str(eid): {url,title,domain,quote} }。
 * 主路徑 + fallback 都用：把報告字串裡的 inline [N] 連結化（不改字串文字內容）。
 *
 * quote 分流（對齊後端 Decision 2' / _TEXTFRAG_OK_SOURCES）：
 *   - source === 'internal'（或 source 欄位缺失/空字串，後端預設 "internal"）→
 *     從 snippet 抽 quote（等價後端 _extract_quote：只 trim 頭尾，空 snippet → ''）。
 *   - source 為 'web' / 'wiki' / 'llm_knowledge' 等 → quote = ''
 *     （web 含省略號必 miss、wiki/llm_knowledge 無逐字原文 → 讓前端降級裸 URL）。
 * 前端 addLRCitationLinks 只需感知 quote 是否空，不感知 source。
 *
 * @returns {Object} eid(str) -> {url,title,domain,quote}
 */
function buildLRCitationSources(lrState) {
    const pool = parseLRJsonField(lrState.evidence_pool_json, 'evidence_pool');
    if (!pool || typeof pool !== 'object') return {};
    const out = {};
    for (const [eid, entry] of Object.entries(pool)) {
        if (!entry || typeof entry !== 'object') continue;
        // 對齊後端 getattr(entry, "source", "internal") or "internal"：
        // 欄位缺失或空字串時預設 "internal"。
        const rawSrc = String(entry.source || '').trim();
        const src = rawSrc || 'internal';
        // Decision 2'：只有 internal source 才從 snippet 抽 quote。
        // 對齊後端 _extract_quote：snippet.strip()，空 snippet → ''。
        const rawSnippet = String(entry.snippet || '').trim();
        const quote = (src === 'internal') ? rawSnippet : '';
        out[String(eid)] = {
            url: String(entry.url || '').trim(),
            title: String(entry.title || '').trim(),
            domain: String(entry.source_domain || entry.domain || '').trim(),
            quote,
        };
    }
    return out;
}

/**
 * 前端等價重建 references markdown（後端 _build_references_block 的 client port）。
 * **僅 fallback path（舊 session 重組）用**；主路徑 references 已在 final_report_markdown 字串裡。
 * 主段「## 參考文獻」= 被 sections sources_used 引用過的 evidence（首見順序）；
 * 附段「## 研究時搜尋到的相關資料」= pool 中未被引用的剩餘條目（升序）。
 * phantom id → 「[id] 來源遺失」行（no silent skip）。
 * citation_style==='author_year' → APA 條目；否則數字格式。
 * @returns {string} markdown（空 pool → ''）
 */
function buildLRReferencesBlock(lrState) {
    const pool = parseLRJsonField(lrState.evidence_pool_json, 'evidence_pool');
    if (!pool || typeof pool !== 'object' || Object.keys(pool).length === 0) return '';
    const sections = Array.isArray(lrState.written_sections) ? lrState.written_sections : [];
    const style = lrState.user_voice && lrState.user_voice.citation_style;

    const fmtEntry = (eid, entry) => {
        if (!entry) return `[${eid}] 來源遺失（引用了但不在證據池）`;
        if (style === 'author_year') {
            const author = String(entry.author || entry.source_domain || entry.domain || '佚名').trim();
            let year = String(entry.year || '').trim();
            if (!year && entry.published_at) year = String(entry.published_at).slice(0, 4).trim();
            year = year || 'n.d.';
            const title = String(entry.title || entry.source_domain || '未知標題').trim();
            const domain = String(entry.source_domain || entry.domain || '').trim();
            const url = String(entry.url || '').trim();
            return `${author}. (${year}). ${title}.${domain ? ` ${domain}.` : ''}${url ? ` ${url}` : ''}`;
        }
        const title = String(entry.title || '').trim();
        const domain = String(entry.source_domain || entry.domain || '').trim();
        const url = String(entry.url || '').trim();
        return `[${eid}] ${title}${domain ? ` — ${domain}` : ''}${url ? ` ${url}` : ''}`;
    };

    const cited = [];
    const seen = new Set();
    for (const sec of sections) {
        const used = Array.isArray(sec.sources_used) ? sec.sources_used : [];
        for (const eid of used) {
            const k = String(eid);
            if (!seen.has(k)) { seen.add(k); cited.push(k); }
        }
    }
    const lines = [];
    if (cited.length) {
        lines.push('', '---', '', '## 參考文獻', '');
        for (const k of cited) lines.push(fmtEntry(k, pool[k]));
    }
    const uncited = Object.keys(pool)
        .filter(k => !seen.has(k))
        .sort((a, b) => Number(a) - Number(b));
    if (uncited.length) {
        lines.push('', '---', '', '## 研究時搜尋到的相關資料', '');
        for (const k of uncited) lines.push(fmtEntry(k, pool[k]));
    }
    return lines.length ? lines.join('\n') : '';
}

export function addLRSection(index, title, content, sources, methodologyNote, citationSources, citationFormat) {
    console.log('[Live Research] Section', index, ':', title);
    const sectionsEl = document.getElementById('lrSections');
    if (!sectionsEl) return;
    sectionsEl.style.display = '';

    // O2 / O2-TF: 在 sanitize 之前把 inline [N] 轉成可點 <a>（含 text fragment）。
    // G1 upsert：update-in-place 與新卡片共用此 bodyHTML，linker 套在此即兩路徑同生效。
    const parsedBody = marked.parse(String(content || ''));
    const linkedBody = addLRCitationLinks(parsedBody, citationSources, citationFormat);
    const bodyHTML = DOMPurify.sanitize(linkedBody);
    const sectionNumDisplay = parseInt(index, 10) + 1 || '?';
    const titleSafe = DOMPurify.sanitize(String(title || ''));

    // L3 WARN marker: methodology_note (若非空) 顯示成 ⚠ 提示列
    const warnHTML = methodologyNote
        ? `<div class="lr-section-warn"><span class="lr-section-warn-icon">⚠</span><span class="lr-section-warn-text">${escapeHTML(String(methodologyNote))}</span></div>`
        : '';

    // G1 upsert：先查同 index 既有卡片，找到則 update-in-place，找不到才 append
    // 防止 writer revise / Critic REJECT 重跑同 section_index 時疊加噪音卡片
    const existing = sectionsEl.querySelector(`[data-lr-section-index="${index}"]`);
    if (existing) {
        // Update-in-place：只更新 body（保留折疊狀態）
        const bodyEl = existing.querySelector('.lr-section-body');
        if (bodyEl) bodyEl.innerHTML = bodyHTML;
        // 同步更新 title（revise 可能改題目）
        const titleEl = existing.querySelector('.lr-section-title');
        if (titleEl) titleEl.textContent = title || '';
        // 同步更新 WARN marker（revise 後 critic 可能通過或持續警告）
        const existingWarn = existing.querySelector('.lr-section-warn');
        if (warnHTML) {
            if (existingWarn) {
                existingWarn.outerHTML = warnHTML;
            } else {
                // 插在 body 上方
                const bodyEl2 = existing.querySelector('.lr-section-body');
                if (bodyEl2) bodyEl2.insertAdjacentHTML('beforebegin', warnHTML);
            }
        } else if (existingWarn) {
            existingWarn.remove();
        }
        existing.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        return;
    }

    const card = document.createElement('div');
    card.className = 'lr-section-card';
    // G1：加 data attribute 供後續 upsert 查詢 + G2 collapse 使用
    card.dataset.lrSectionIndex = index;

    // Bug 1 root fix (2026-05-18)：移除段末「來源：[1] [2]」force-append div。
    // O2 / O2-TF (2026-06-16)：inline [N]（numeric 模式）由 addLRCitationLinks 轉為
    // 可點 <a>（internal source 帶 #:~:text= text fragment 嘗試 highlight 原文段落）；
    // author_year/footnote 模式無 eid token 不逐 token 連結。Stage 6 export 末尾的
    // references master list 仍是 user 主要 cross-reference 工具。
    card.innerHTML = `
        <div class="lr-section-header" title="點擊折疊／展開">
            <div class="lr-section-num">${sectionNumDisplay}</div>
            <div class="lr-section-title">${titleSafe}</div>
            <span class="lr-collapse-icon">▼</span>
        </div>
        ${warnHTML}<div class="lr-section-body">${bodyHTML}</div>`;

    // G2：掛 click handler 到 header 實現折疊
    const header = card.querySelector('.lr-section-header');
    const icon = card.querySelector('.lr-collapse-icon');
    const body = card.querySelector('.lr-section-body');
    header.addEventListener('click', () => {
        const isCollapsed = card.classList.toggle('collapsed');
        icon.textContent = isCollapsed ? '▶' : '▼';
        if (isCollapsed) {
            body.style.maxHeight = '0';
            body.style.overflow = 'hidden';
        } else {
            body.style.maxHeight = '';
            body.style.overflow = '';
        }
    });

    sectionsEl.appendChild(card);

    // G2：第一段 section 出現時初始化 toolbar（toolbar 只建一次）
    if (!sectionsEl.querySelector('.lr-toggle-all-toolbar')) {
        _addLRToggleAllToolbar(sectionsEl);
    }

    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * G2：「全部折疊 / 全部展開」toolbar（DR parity）。
 * 插入在 sectionsEl 頂部，第一個 section 出現時呼叫一次。
 */
function _addLRToggleAllToolbar(sectionsEl) {
    const toolbar = document.createElement('div');
    toolbar.className = 'lr-toggle-all-toolbar';
    let allCollapsed = false;
    const btn = document.createElement('button');
    btn.className = 'lr-btn-toggle-all';
    btn.textContent = '全部折疊';
    btn.addEventListener('click', () => {
        allCollapsed = !allCollapsed;
        sectionsEl.querySelectorAll('.lr-section-card').forEach(c => {
            const icon = c.querySelector('.lr-collapse-icon');
            const body = c.querySelector('.lr-section-body');
            if (allCollapsed) {
                c.classList.add('collapsed');
                if (icon) icon.textContent = '▶';
                if (body) { body.style.maxHeight = '0'; body.style.overflow = 'hidden'; }
            } else {
                c.classList.remove('collapsed');
                if (icon) icon.textContent = '▼';
                if (body) { body.style.maxHeight = ''; body.style.overflow = ''; }
            }
        });
        btn.textContent = allCollapsed ? '全部展開' : '全部折疊';
    });
    toolbar.appendChild(btn);
    sectionsEl.insertBefore(toolbar, sectionsEl.firstChild);
}

// Track D D2b (sprint 2026-05-28): LR KG full D3 graph render — reuse DR module
// D-CEO-Q1 LOCKED Option (a): 複用 DR displayKnowledgeGraph + prefix 化
// D-CEO-Q4 LOCKED Option (β): LR 獨立 #lrKGDisplayContainer + DR module 參數化
// (D2a placeholder DOM list 已刪除, displayLRKnowledgeGraph 被取代)
import { displayKnowledgeGraph } from './knowledge-graph.js?v=20260705c';

// Track D D2b Step 1b (sprint 2026-05-28, fix-up round 1 C-2 / R2-I2):
// 移植 DR initKGVisibilityToggle IIFE 到 LR (來源 static/news-search.js:3285-3323)
// D-CEO-Q3 LOCKED: LR ≥ DR 互動 — (e) Hide/Restore + localStorage 持久化必收
// localStorage key 區別: DR 用 'nlweb-kg-hidden', LR 用 'nlweb-lr-kg-hidden'
// — 兩 tab 獨立 hidden state, user 在 DR 收起不影響 LR; 反之亦然
(function initLRKGVisibilityToggle() {
    document.addEventListener('DOMContentLoaded', () => {
        const hideBtn = document.getElementById('lrKGHideBtn');
        const restoreBar = document.getElementById('lrKGRestoreBar');
        const kgContainer = document.getElementById('lrKGDisplayContainer');
        if (!hideBtn || !restoreBar || !kgContainer) return;

        // Restore preference
        let kgHidden = false;
        try {
            kgHidden = localStorage.getItem('nlweb-lr-kg-hidden') === 'true';
        } catch (e) { /* localStorage unavailable */ }

        // Apply stored preference: if hidden, mark container userHidden so restoreBar
        // shows when displayKnowledgeGraph(kg, {containerPrefix:'lrKG'}) is called
        if (kgHidden) {
            kgContainer.dataset.userHidden = 'true';
        }

        hideBtn.addEventListener('click', () => {
            kgContainer.style.display = 'none';
            kgContainer.dataset.userHidden = 'true';
            restoreBar.style.display = 'block';
            try {
                localStorage.setItem('nlweb-lr-kg-hidden', 'true');
            } catch (e) {}
        });

        // DR pattern: restoreBar 整個 div 是 clickable (parallel news-search.js:3314)
        restoreBar.addEventListener('click', () => {
            kgContainer.style.display = 'block';
            kgContainer.dataset.userHidden = 'false';
            restoreBar.style.display = 'none';
            try {
                localStorage.setItem('nlweb-lr-kg-hidden', 'false');
            } catch (e) {}
        });
    });
})();

// Track D D2b Step 1c (sprint 2026-05-28, fix-up round 2 R2-C1):
// 移植 DR kgToggleButton collapse handler 到 LR (來源 static/news-search.js:2376-2390)
// D-CEO-Q3 LOCKED: LR ≥ DR 互動 — (m) Collapse with header retained 必收
// Collapse 與 Hide/Restore 是兩個不同 UX:
//   - Collapse: 保留 header bar 可見, 收合 #lrKGContentWrapper (graph + popovers)
//   - Hide: 整個 #lrKGDisplayContainer 隱藏 + #lrKGRestoreBar 顯示
// localStorage key 'nlweb-lr-kg-collapsed' — LR > DR 擴張 (DR 原版未持久化)
// 沿 D-CEO-Q3 LOCKED「更多」= LR 允許優於 DR (R3 NF#3 紀律驗 DR 無 'nlweb-kg-collapsed' key)
(function initLRKGCollapseToggle() {
    document.addEventListener('DOMContentLoaded', () => {
        const toggleButton = document.getElementById('lrKGToggleButton');
        const wrapper = document.getElementById('lrKGContentWrapper');
        const icon = document.getElementById('lrKGToggleIcon');
        if (!toggleButton || !wrapper) return;

        // Reload 復原 collapsed 狀態
        let isCollapsed = false;
        try {
            isCollapsed = localStorage.getItem('nlweb-lr-kg-collapsed') === 'true';
        } catch (e) { /* localStorage 不可用降級 */ }
        if (isCollapsed) {
            wrapper.style.display = 'none';
            if (icon) icon.textContent = '▶';
            if (toggleButton.childNodes[1]) toggleButton.childNodes[1].textContent = ' 展開';
        }

        toggleButton.addEventListener('click', () => {
            const collapsedNow = wrapper.style.display === 'none';
            wrapper.style.display = collapsedNow ? '' : 'none';
            if (icon) icon.textContent = collapsedNow ? '▼' : '▶';
            if (toggleButton.childNodes[1]) {
                toggleButton.childNodes[1].textContent = collapsedNow ? ' 收起' : ' 展開';
            }
            try {
                localStorage.setItem('nlweb-lr-kg-collapsed', collapsedNow ? 'false' : 'true');
            } catch (e) {}
        });
    });
})();

export function showLRExport(content, format, citationSources, citationFormat) {
    console.log('[Live Research] Export ready, format:', format);
    const exportEl = document.getElementById('lrExport');
    if (!exportEl) return;
    exportEl.style.display = '';

    const fmt = (format || 'markdown').toLowerCase();
    let bodyHTML;
    if (fmt === 'markdown' || fmt === 'md') {
        // O2 / O2-TF: 內文 + references master list 的 [N] 同過 linker（第三觸點）。
        const parsed = marked.parse(String(content || ''));
        const linked = addLRCitationLinks(parsed, citationSources, citationFormat);
        bodyHTML = DOMPurify.sanitize(linked);
    } else {
        bodyHTML = `<pre style="white-space:pre-wrap;word-break:break-word;">${DOMPurify.sanitize(String(content || ''))}</pre>`;
    }

    exportEl.innerHTML = `
        <div class="lr-export-title">研究報告匯出</div>
        <div class="lr-export-content">${bodyHTML}</div>
        <div class="lr-export-actions">
            <button class="lr-btn-primary" id="lrBtnDownload">下載報告</button>
            <button class="lr-btn-secondary" id="lrBtnCopyExport">複製文字</button>
        </div>`;

    // Bind download/copy
    const rawContent = String(content || '');
    const dl = document.getElementById('lrBtnDownload');
    if (dl) {
        dl.addEventListener('click', () => {
            const blob = new Blob([rawContent], { type: 'text/markdown;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'live-research-report.md';
            a.click();
            URL.revokeObjectURL(a.href);
        });
    }
    const cp = document.getElementById('lrBtnCopyExport');
    if (cp) {
        cp.addEventListener('click', () => {
            navigator.clipboard.writeText(rawContent).then(() => {
                cp.textContent = '已複製';
                setTimeout(() => { cp.textContent = '複製文字'; }, 2000);
            }).catch(err => console.error('[Live Research] Copy failed:', err));
        });
    }

    exportEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ============================================================================
// Task 1: 報告顯示 — 主路徑直接讀 final_report_markdown + fallback 重組
// ============================================================================

/**
 * 回顧模式重建 KG D3 視覺化（主路徑 + fallback 共用）。
 * KG 視覺是 D3 圖、非 markdown，不在 final_report_markdown 字串裡，必須另呼。
 * resetLiveResearchUI 已清 #lrKGDisplayContainer + SVG，故 completed 回顧必須重呼。
 * null / 空 entities+relationships → 跳過（不可畫空容器）。
 * [V] displayKnowledgeGraph route 到 kgLR instance（containerPrefix:'lrKG'），
 *   closure 固定 prefix，不污染 DR；自身會 un-hide 容器（除非 userHidden）。
 */
function displayKGForReview(lrState) {
    const kg = lrState.knowledge_graph;  // [V] 已是 object 或 null，不需 parse
    const hasKG = kg && typeof kg === 'object'
        && ((Array.isArray(kg.entities) && kg.entities.length > 0)
            || (Array.isArray(kg.relationships) && kg.relationships.length > 0));
    if (hasKG) {
        displayKnowledgeGraph(kg, { containerPrefix: 'lrKG' });
    }
    // null / 空 KG：不呼，#lrKGDisplayContainer 維持 reset 後的 hidden。
}

/**
 * 回顧模式報告顯示入口。
 * 主路徑：final_report_markdown 非空 → 直接 showLRExport（後端原字串，逐字一致，零重組）。
 * fallback：欄位上線前的舊 session（字串空）→ renderLRReviewReport 前端重組（標明差異）。
 * 兩路都呼 displayKGForReview 重建 KG 視覺。
 */
function showLRExportFromState(lrState) {
    const persisted = (lrState && typeof lrState.final_report_markdown === 'string')
        ? lrState.final_report_markdown : '';
    const citationFormat = (lrState.user_voice && lrState.user_voice.citation_style === 'author_year')
        ? 'author_year' : 'numeric';

    if (persisted.trim()) {
        // 主路徑：後端組好的整份字串，與當初 export 逐字一致（含 H1 + references + KG section）。
        // citationSources 只把 inline [N] 連結化（前端互動層，不改字串文字內容）。
        let citationSources = {};
        try {
            citationSources = buildLRCitationSources(lrState);
        } catch (err) {
            console.warn('[LR Review] citationSources 重建失敗，報告仍渲染（[N] 不連結化）', err);
        }
        showLRExport(persisted, 'markdown', citationSources, citationFormat);
        displayKGForReview(lrState);
        return;
    }
    // fallback：舊 session 無 final_report_markdown → 前端重組（可見差異）。
    renderLRReviewReport(lrState);
}

/**
 * fallback path（僅欄位上線前的舊 session：final_report_markdown 空）。
 * 從原料前端重組報告（H1 + sections + references + KG section）。
 * **與當初下載檔可能略有差異**（前端重組無法保證與後端逐字一致——這正是路 3 主路徑
 * 要繞過的問題；新 session 一律走主路徑）。故顯眼 banner 標明，不靜默。
 * 已知限制（不強求修）：section 擺位 / references 字串 / 換行 / KG metadata.generated_at
 * 時戳可能與原 export 不同。
 */
function renderLRReviewReport(lrState) {
    const container = getLRReviewContainer();
    if (!container) return;
    let citationSources, references, kgSectionMd, researchQuestion = '';
    try {
        citationSources = buildLRCitationSources(lrState);
        references = buildLRReferencesBlock(lrState);
        kgSectionMd = buildLRKGSectionMarkdown(lrState);
        const cm = parseLRJsonField(lrState.context_map_json, 'context_map');
        researchQuestion = (cm && typeof cm === 'object' && cm.research_question)
            ? String(cm.research_question).trim() : '';
    } catch (err) {
        container.innerHTML = lrReviewErrorNotice('最終報告（回顧重建版）', err);
        return;
    }
    const sections = Array.isArray(lrState.written_sections) ? lrState.written_sections : [];
    if (sections.length === 0) {
        container.innerHTML = lrReviewEmptyNotice('最終報告');
        return;
    }
    const parts = [];
    if (researchQuestion) parts.push(`# ${researchQuestion}`, '');
    sections.forEach((s, i) => {
        const title = String(s.title || `第 ${i + 1} 段`);
        parts.push(`## ${title}`, '', String(s.content || ''), '');
        if (s.methodology_note) {
            parts.push(`> ⚠ 撰寫說明：${String(s.methodology_note)}`, '');
        }
    });
    if (references) parts.push(references);
    if (kgSectionMd) parts.push(kgSectionMd);
    const fullMarkdown = parts.join('\n');

    // 顯眼 banner：回顧重建版，與原下載檔可能略有差異（no silent fail）。
    container.innerHTML = '';
    const banner = document.createElement('div');
    banner.className = 'lr-review-fallback-banner';
    banner.textContent = '此為回顧重建版報告（此 session 在報告持久化功能上線前完成）。'
        + '內容與您當初下載的檔案可能在排版、參考文獻格式或知識圖譜時戳上略有差異；'
        + '如需與原始下載檔完全一致的版本，請以當初下載的檔案為準。';
    container.appendChild(banner);

    const citationFormat = (lrState.user_voice && lrState.user_voice.citation_style === 'author_year')
        ? 'author_year' : 'numeric';
    showLRExport(fullMarkdown, 'markdown', citationSources, citationFormat);
    displayKGForReview(lrState);
}

/**
 * 前端等價重建 KG markdown section（僅 fallback path 用）。
 * null / 空 entities+relationships → 回 ''（與後端 _build_kg_export_payload 回 None 一致）。
 * metadata.generated_at 用「回顧重組當下」時戳（fallback 已知限制：與原 export 不同）。
 * @returns {string} KG section markdown（無 KG → ''）
 */
function buildLRKGSectionMarkdown(lrState) {
    const kg = lrState.knowledge_graph;  // [V] 已是 object 或 null，不需 parse
    if (!kg || typeof kg !== 'object') return '';
    const entities = Array.isArray(kg.entities) ? kg.entities : [];
    const relationships = Array.isArray(kg.relationships) ? kg.relationships : [];
    if (entities.length === 0 && relationships.length === 0) return '';
    const payload = {
        entities,
        relationships,
        metadata: {
            generated_at: new Date().toISOString(),  // fallback：非原時戳（已知差異）
            entity_count: entities.length,
            relationship_count: relationships.length,
        },
    };
    return '\n\n---\n\n## 知識圖譜 (Knowledge Graph)\n\n'
        + '研究過程萃取的實體與關係（含 evidence_ids 對應上方 references 編號）：\n\n'
        + '```json\n'
        + JSON.stringify(payload, null, 2)
        + '\n```\n';
}

// ============================================================================
// Task 2/3: stage navigation + dispatch
// ============================================================================

/**
 * 回顧模式：把 click 綁到 stage dots / labels。每次 restore 呼叫。
 * blocker 4：listener 讀 module-local _lrReviewState（呼叫端先設好），
 * 不在 closure 捕捉 lrState → 切 session 後不會 render 前一個 state。
 */
function wireLRStageNavigation(lrState) {
    _lrReviewState = lrState;  // 每次 restore 覆寫成最新 state
    const maxStage = lrState.current_stage ?? 0;
    const all = [
        ...document.querySelectorAll('.lr-stage-dot'),
        ...document.querySelectorAll('.lr-stage-labels span'),
    ];
    all.forEach(el => {
        const n = parseInt(el.dataset.stage, 10);
        el.classList.remove('lr-stage-selected', 'lr-stage-clickable', 'lr-stage-unreached');
        if (!n) return;
        if (n > maxStage) { el.classList.add('lr-stage-unreached'); return; }
        el.classList.add('lr-stage-clickable');
        if (el.dataset.lrNavWired === '1') return;  // 只防重複綁 listener
        el.dataset.lrNavWired = '1';
        el.addEventListener('click', () => {
            if (!el.classList.contains('lr-stage-clickable')) return;
            document.querySelectorAll('.lr-stage-dot, .lr-stage-labels span')
                .forEach(e => e.classList.remove('lr-stage-selected'));
            el.classList.add('lr-stage-selected');
            const snap = getLRLoadedSnapshot();
            if (Array.isArray(snap) && snap.length) {
                renderLRStageDialog(n, snap);          // v3 snapshot 模式：lazy render 對話 → #lrStageReview
            } else {
                loadLRStageReview(n, _lrReviewState);  // 舊 session fallback（含 renderLRContextMap JSON 視圖）
            }
        });
    });
}

/**
 * 依 stageNum 載入該 stage 回顧內容。
 *   1/2 → renderLRContextMap；3 → renderLRStyleFeatures；4 → renderLROutline；
 *   5/6 → showLRExportFromState（主路徑讀 final_report_markdown / fallback 重組）。
 * Track F 查證另以折疊入口呈現（appendLRCriticReviewEntry，Task 8）。
 */
function loadLRStageReview(stageNum, lrState) {
    const labels = { 1: '研究架構', 2: '資料蒐集與分析', 3: '文筆風格', 4: '章節大綱', 5: '章節撰寫', 6: '匯出報告' };
    switch (stageNum) {
        case 1:
        case 2: renderLRContextMap(lrState); break;
        case 3: renderLRStyleFeatures(lrState); break;
        case 4: renderLROutline(lrState); break;
        case 5:
        case 6: showLRExportFromState(lrState); break;  // 路 3：主路徑 / fallback 自動判斷
        default: {
            const c = getLRReviewContainer();
            if (c) c.innerHTML = lrReviewEmptyNotice(labels[stageNum] || `階段 ${stageNum}`);
        }
    }
}

// ============================================================================
// Task 4: Stage 1/2 renderer — 研究架構 + 證據池 + 已執行搜尋
// ============================================================================

function renderLRContextMap(lrState) {
    const container = getLRReviewContainer();
    if (!container) return;
    let cm, pool;
    try {
        cm = parseLRJsonField(lrState.context_map_json, 'context_map');
        pool = parseLRJsonField(lrState.evidence_pool_json, 'evidence_pool');
    } catch (err) {
        container.innerHTML = lrReviewErrorNotice('研究架構', err);
        return;
    }
    const poolArr = pool && typeof pool === 'object' ? Object.values(pool) : [];
    const searches = Array.isArray(lrState.executed_searches) ? lrState.executed_searches : [];

    if (!cm && poolArr.length === 0 && searches.length === 0) {
        container.innerHTML = lrReviewEmptyNotice('研究架構');
        return;
    }

    const cmHTML = cm
        ? `<div class="lr-review-block"><h4>研究架構</h4>
             <pre class="lr-review-json">${escapeHTML(JSON.stringify(cm, null, 2))}</pre></div>`
        : '';

    // SF-C：外部連結用 DOM API + scheme 白名單，不用 encodeURI 直塞 innerHTML。
    const safeLink = (url, text) => {
        const u = String(url || '').trim();
        const isHttp = /^https?:\/\//i.test(u);
        if (!isHttp) return escapeHTML(text);
        return `<a href="${escapeHtmlAttr(u)}" target="_blank" rel="noopener noreferrer">${escapeHTML(text)}</a>`;
    };
    const evHTML = poolArr.length
        ? `<div class="lr-review-block"><h4>證據池（${poolArr.length}）</h4><ul class="lr-evidence-list">`
          + poolArr.map(e => {
              const title = String((e && (e.title || e.headline)) || '(無標題)');
              const url = (e && (e.url || e.link)) || '';
              const snip = String((e && (e.snippet || e.summary)) || '');
              return `<li><div class="lr-evidence-title">${safeLink(url, title)}</div>`
                   + (snip ? `<div class="lr-evidence-snip">${escapeHTML(snip)}</div>` : '') + `</li>`;
          }).join('') + `</ul></div>`
        : '';

    const sHTML = searches.length
        ? `<div class="lr-review-block"><h4>已執行搜尋（${searches.length}）</h4><ul class="lr-search-list">`
          + searches.map(q => `<li>${escapeHTML(String(typeof q === 'string' ? q : (q && q.query) || JSON.stringify(q)))}</li>`).join('')
          + `</ul></div>`
        : '';

    container.innerHTML = cmHTML + evHTML + sHTML;
}

// ============================================================================
// Task 5: Stage 3 renderer — 文筆風格
// ============================================================================

function renderLRStyleFeatures(lrState) {
    const container = getLRReviewContainer();
    if (!container) return;
    let sf;
    try {
        sf = parseLRJsonField(lrState.style_features_json, 'style_features');
    } catch (err) {
        container.innerHTML = lrReviewErrorNotice('文筆風格', err);
        return;
    }
    const voice = lrState.user_voice && typeof lrState.user_voice === 'object' ? lrState.user_voice : null;
    if (!sf && !voice) {
        container.innerHTML = lrReviewEmptyNotice('文筆風格');
        return;
    }
    const block = (label, obj) => obj
        ? `<div class="lr-review-block"><h4>${escapeHTML(label)}</h4>
             <pre class="lr-review-json">${escapeHTML(JSON.stringify(obj, null, 2))}</pre></div>`
        : '';
    container.innerHTML = block('文筆風格設定', sf) + block('使用者語氣', voice);
}

// ============================================================================
// Task 6: Stage 4 renderer — 章節大綱
// ============================================================================

function renderLROutline(lrState) {
    const container = getLRReviewContainer();
    if (!container) return;
    let outline;
    try {
        outline = parseLRJsonField(lrState.book_outline_json, 'book_outline');
    } catch (err) {
        container.innerHTML = lrReviewErrorNotice('章節大綱', err);
        return;
    }
    if (!outline) {
        container.innerHTML = lrReviewEmptyNotice('章節大綱');
        return;
    }
    const sections = Array.isArray(outline) ? outline
                   : (Array.isArray(outline.sections) ? outline.sections
                   : (Array.isArray(outline.chapters) ? outline.chapters : null));
    if (!sections) {
        container.innerHTML = `<div class="lr-review-block"><h4>章節大綱</h4>
            <pre class="lr-review-json">${escapeHTML(JSON.stringify(outline, null, 2))}</pre></div>`;
        return;
    }
    const items = sections.map((s, i) => {
        const title = escapeHTML(String((s && (s.title || s.heading)) || `第 ${i + 1} 章`));
        const desc = escapeHTML(String((s && (s.description || s.summary || s.goal)) || ''));
        return `<li><strong>${title}</strong>${desc ? `<div class="lr-outline-desc">${desc}</div>` : ''}</li>`;
    }).join('');
    container.innerHTML = `<div class="lr-review-block"><h4>章節大綱（${sections.length} 章）</h4>
        <ol class="lr-outline-list">${items}</ol></div>`;
}

// ============================================================================
// Task 7: Track F renderer — 查證 / 評審回顧（blocker 6：統一名 ...Into）
// ============================================================================

/**
 * 渲染 Track F 查證/一致性紀錄到指定 element（blocker 6：統一名 ...Into）。
 * critic_section_reviews / consistency_drift_log 在 to_dict 已是 object，不需 parse。
 * @param {HTMLElement} targetEl  渲染目標（折疊區 body，非主報告容器）
 */
/**
 * 渲染單段查證 review（dict from model_dump()）為人類可讀 HTML。
 * 欄位：verdict / claim_issues[].{claim_type,claim_text,severity,explanation} / overall_explanation
 * @param {Object} review - critic_section_reviews[k]（已是 dict，不需 parse）
 * @param {number} segNum - 1-based 段號（用於顯示）
 * @returns {string} HTML string（escapeHTML 包所有 LLM 來源文字）
 */
function _renderSingleCriticReview(review, segNum) {
    if (!review || typeof review !== 'object') {
        // 不可 silent fail：結構異常時明確告知
        return `<li class="lr-critic-item lr-critic-item--error">`
            + `<strong>第 ${segNum} 段</strong>`
            + `<span class="lr-critic-meta">此段查核紀錄格式異常，無法顯示。</span>`
            + `</li>`;
    }

    const verdict = typeof review.verdict === 'string' ? review.verdict : '';
    const overallExplanation = typeof review.overall_explanation === 'string'
        ? review.overall_explanation : '';
    const claimIssues = Array.isArray(review.claim_issues) ? review.claim_issues : [];

    // verdict 對應 CSS class 與標籤
    const verdictClass = verdict === 'REJECT' ? 'lr-critic-reject'
                       : verdict === 'WARN'   ? 'lr-critic-warn'
                       : verdict === 'PASS'   ? 'lr-critic-pass'
                       : 'lr-critic-unknown';
    const verdictLabel = verdict === 'REJECT' ? '❌ 查核未通過'
                       : verdict === 'WARN'   ? '⚠ 有待確認說法'
                       : verdict === 'PASS'   ? '✓ 通過查核'
                       : verdict ? escapeHTML(verdict) : '（未知）';

    // 段頭：「第 N 段：M 處待確認」或「第 N 段：通過查核」
    const issueCount = claimIssues.length;
    const headingSuffix = verdict === 'WARN' || verdict === 'REJECT'
        ? `：${issueCount} 處待確認`
        : '';

    // overall_explanation：比照即時路徑 warn banner 文案風格
    const overallHTML = overallExplanation
        ? `<div class="lr-critic-overall">${escapeHTML(overallExplanation)}</div>`
        : '';

    // 逐條 claim_issues 渲染
    let issuesHTML = '';
    if (issueCount > 0) {
        const claimTypeLabels = {
            numeric: '數字類',
            temporal: '時間類',
            causal: '因果類',
            comparative: '比較類',
            predictive: '預測類',
            evaluative: '評價類',
            other: '其他',
        };
        const issueItems = claimIssues.map((issue, idx) => {
            if (!issue || typeof issue !== 'object') {
                return `<li class="lr-critic-issue lr-critic-issue--error">`
                    + `第 ${idx + 1} 筆查核紀錄格式異常。</li>`;
            }
            const typeKey = typeof issue.claim_type === 'string' ? issue.claim_type : 'other';
            const typeLabel = escapeHTML(claimTypeLabels[typeKey] || typeKey);
            const claimText = typeof issue.claim_text === 'string'
                ? escapeHTML(issue.claim_text) : '';
            const explanation = typeof issue.explanation === 'string'
                ? escapeHTML(issue.explanation) : '';
            const sevClass = issue.severity === 'reject' ? 'lr-critic-sev-reject' : 'lr-critic-sev-warn';
            return `<li class="lr-critic-issue ${sevClass}">`
                + `<span class="lr-critic-issue-type">[${typeLabel}]</span>`
                + (claimText ? ` <span class="lr-critic-issue-claim">${claimText}</span>` : '')
                + (explanation ? ` — <span class="lr-critic-issue-exp">${explanation}</span>` : '')
                + `</li>`;
        }).join('');
        issuesHTML = `<ul class="lr-critic-issues">${issueItems}</ul>`;
    }

    return `<li class="lr-critic-item ${verdictClass}">`
        + `<div class="lr-critic-header">`
        + `<strong>第 ${segNum} 段${escapeHTML(headingSuffix)}</strong>`
        + ` <span class="lr-critic-verdict-badge">${verdictLabel}</span>`
        + `</div>`
        + overallHTML
        + issuesHTML
        + `</li>`;
}

/**
 * 渲染單筆 consistency_drift_log entry 為人類可讀 HTML。
 * 欄位：drift_level / drift_description / recommended_action / stage / iteration / timestamp
 * @param {Object|string} entry
 * @returns {string} HTML string
 */
function _renderSingleDriftEntry(entry) {
    if (typeof entry === 'string') {
        return `<li class="lr-drift-item">${escapeHTML(entry)}</li>`;
    }
    if (!entry || typeof entry !== 'object') {
        return `<li class="lr-drift-item lr-drift-item--error">此筆一致性紀錄格式異常。</li>`;
    }
    const level = typeof entry.drift_level === 'string' ? entry.drift_level : '';
    const desc = typeof entry.drift_description === 'string' ? entry.drift_description : '';
    const action = typeof entry.recommended_action === 'string' ? entry.recommended_action : '';
    const stage = typeof entry.stage === 'string' ? entry.stage : '';
    const iter = entry.iteration !== undefined ? String(entry.iteration) : '';

    // 只在有實質漂移時顯示詳情（drift_level='none' 通常無訊息價值）
    const levelLabel = level === 'none' ? '無漂移'
                     : level === 'minor' ? '輕微偏移'
                     : level ? escapeHTML(level) : '未知';
    const metaParts = [];
    if (stage) metaParts.push(escapeHTML(stage));
    if (iter) metaParts.push(`第 ${escapeHTML(iter)} 輪`);
    const meta = metaParts.length ? `<span class="lr-drift-meta">（${metaParts.join('，')}）</span>` : '';

    let detail = '';
    if (desc) detail += `<span class="lr-drift-desc">${escapeHTML(desc)}</span>`;
    if (action && action !== 'continue') {
        detail += (detail ? ' ' : '') + `<span class="lr-drift-action">建議：${escapeHTML(action)}</span>`;
    }

    return `<li class="lr-drift-item">`
        + `<strong>${levelLabel}</strong>${meta}`
        + (detail ? `<div class="lr-drift-detail">${detail}</div>` : '')
        + `</li>`;
}

function renderLRCriticReviewsInto(targetEl, lrState) {
    if (!targetEl) return;
    const reviews = (lrState.critic_section_reviews && typeof lrState.critic_section_reviews === 'object')
        ? lrState.critic_section_reviews : {};
    const drift = Array.isArray(lrState.consistency_drift_log) ? lrState.consistency_drift_log : [];
    const keys = Object.keys(reviews);
    if (keys.length === 0 && drift.length === 0) {
        targetEl.innerHTML = lrReviewEmptyNotice('查證與評審');
        return;
    }
    const revHTML = keys.length
        ? `<div class="lr-review-block"><h4>各段查證（${keys.length}）</h4><ul class="lr-critic-list">`
          + keys.map(k => _renderSingleCriticReview(reviews[k], parseInt(k, 10) + 1)).join('')
          + `</ul></div>`
        : '';
    const driftHTML = drift.length
        ? `<div class="lr-review-block"><h4>一致性追蹤（${drift.length}）</h4><ul class="lr-drift-list">`
          + drift.map(d => _renderSingleDriftEntry(d)).join('')
          + `</ul></div>`
        : '';
    targetEl.innerHTML = revHTML + driftHTML;
}

/**
 * 回顧模式：把 6 個 stage dot/label/connector 全標 completed（blocker 5）。
 * 不可用 updateLRStageProgress(7)（>6 no-op）或 (6)（dot 6 停在 active）。
 */
export function markAllStagesCompleted() {
    document.querySelectorAll('.lr-stage-dot').forEach(dot => {
        dot.classList.remove('active');
        dot.classList.add('completed');
    });
    document.querySelectorAll('.lr-stage-connector').forEach(conn => conn.classList.add('completed'));
    document.querySelectorAll('.lr-stage-labels span').forEach(lbl => {
        lbl.classList.remove('active');
        lbl.classList.add('completed');
    });
}

/** 在回顧容器下方加「查證紀錄」折疊區（點開才 render）。 */
function appendLRCriticReviewEntry(lrState) {
    const host = document.getElementById('lrStageReview');
    if (!host) return;
    host.style.display = '';
    const details = document.createElement('details');
    details.className = 'lr-critic-entry';
    details.innerHTML = `<summary>查證與一致性紀錄</summary><div class="lr-critic-body"></div>`;
    host.appendChild(details);
    details.addEventListener('toggle', () => {
        if (details.open) {
            const body = details.querySelector('.lr-critic-body');
            renderLRCriticReviewsInto(body, lrState);  // blocker 6：統一名
        }
    });
}

// ============================================================================
// Session resume — pure DOM restore (LR #19)
// ============================================================================

/**
 * Restore the LR UI from a persisted liveResearchState (resume path).
 *
 * INVARIANT: This function NEVER calls performLiveResearch / continueLiveResearch
 * / makes any HTTP request. It is a pure DOM restore used by loadSavedSession.
 *
 * @param {Object} lrState - Deserialized liveResearchState from server
 *   (snake_case keys, as returned by GET /api/sessions/{id})
 */
export function restoreLRCheckpointFromState(lrState, lrServerId = null, switchToken = null) {
    // B5: stale restore guard — if a newer session-switch has been scheduled,
    // this call is outdated; bail out silently. Only applies when switchToken is
    // supplied (session-switch path). Single-restore paths (page load, #19 resume)
    // call without switchToken (null) and are unaffected.
    if (switchToken !== null && switchToken !== getLRSwitchToken()) {
        console.log('[LR] stale restore skipped (token', switchToken, '!= current', getLRSwitchToken(), ')');
        return;
    }

    if (!lrState || typeof lrState !== 'object') {
        console.warn('[Live Research] restoreLRCheckpointFromState: no valid state, skipping restore');
        return;
    }

    const stage = lrState.current_stage ?? 0;
    const status = lrState.stage_status ?? '';

    console.log('[Live Research] restoreLRCheckpointFromState: stage=', stage, 'status=', status);

    // Reset LR UI containers to clean state (no SSE stream start)
    resetLiveResearchUI();

    // C (in-house W-1 + Gemini #1 漏觸 fix)：resume / session-switch restore 後，
    // _currentLRStage 必須對齊被 resume 的真實 stage（resetLiveResearchUI 剛把它歸 0）。
    // 否則 resume 一個已在 Stage 5 的 session 後，之後真 recollect 退回 emit stage=1，
    // 因 _currentLRStage 還是 0 → 1<0=False → 漏清，幽靈舊章節殘留。
    // 放在 resetLiveResearchUI() 之後（避免被 reset 的歸 0 蓋回）。
    if (typeof stage === 'number' && stage >= 1 && stage <= 6) {
        _currentLRStage = stage;
    }

    // LR #19 順序修正：setLRSessionId 必須在 resetLiveResearchUI() 之後，
    // 否則 reset 的 unconditional clearLRSessionId() 會把它清成 null，
    // 導致 continue 送 lr_session_id=null → 後端 fallback analytics id → crash。
    if (lrServerId) {
        setLRSessionId(lrServerId);
        console.log('[Live Research] resume: setLRSessionId (after reset) =', lrServerId);
    }

    // Show LR tab and make results section active (mirrors performLiveResearch setup)
    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.classList.add('active');
    const lrTab = document.querySelector('.tab[data-view="live-research"]');
    if (lrTab) lrTab.click();

    // Hide main search input (LR uses lrCheckpointReply, same as during active LR)
    const searchContainer = document.getElementById('searchContainer');
    if (searchContainer) searchContainer.style.display = 'none';

    // B1 (NO SILENT FAIL): the LR view is now visible and #lrChat is mounted. If an
    // auth-dead relogin hint was deferred earlier (showLRReloginNeeded ran while #lrChat
    // was absent), surface it now so it is never silently dropped. Idempotent + no-op
    // when nothing is deferred. Must run before the branch-specific early returns below.
    flushDeferredLRReloginHint();

    // plan: lr-sse-reconnect-resume — 傳 offline_capped 啟用三狀態分流（5e）。
    const resumeClass = classifyLRResumeState(stage, status, lrState.offline_capped);
    if (resumeClass === 'not_started') {
        // not_started = empty session that should not resume mid-flow.
        addLRChatMessage('assistant',
            `<em>${escapeHTML('此 Live 研究 session 尚未開始。請開新對話。')}</em>`);
        return;
    }
    if (resumeClass === 'completed') {
        // 回顧模式：不重跑 pipeline（維持 restore read-only invariant）。
        // v3：優先用 lr_dialog_snapshot 逐條重播當時對話（user 看到啥就回顧到啥）；
        // 無 snapshot（功能上線前完成的舊 session）→ 明確 banner 降級到舊結構化 render（不 silent）。
        const snap = getLRLoadedSnapshot();    // loadSavedSession 在 restore 前已 setLRLoadedSnapshot
        const hasSnapshot = Array.isArray(snap) && snap.length && lrStagesInSnapshot(snap).length;

        if (hasSnapshot) {
            addLRChatMessage('assistant',
                `<em>${escapeHTML('此 Live 研究已完成。點上方任一階段，即可回顧該階段的研究對話；下方為完整報告。')}</em>`);
        } else {
            // no-silent-fail：明確告知為何降級（不是壞掉、也不是空的）。
            addLRChatMessage('system',
                escapeHTML('此 session 在對話持久化功能上線前完成，僅能回顧結構化研究內容（無法重現原始對話流）。'));
        }

        markAllStagesCompleted();          // 全 stage dot 標 completed（blocker 5）
        wireLRStageNavigation(lrState);    // 綁 click（blocker 4：每次覆寫 _lrReviewState）
                                           //   有 snapshot → renderLRStageDialog；無 → 舊 loadLRStageReview
        showLRExportFromState(lrState);    // 路 3：主路徑讀 final_report_markdown 直接顯示 /
                                           //   fallback 重組（舊 session）+ KG 視覺重建
        appendLRCriticReviewEntry(lrState);  // Track F 折疊入口

        // 第一眼只見 6 個 stage toggle + 開場提示 + 下方報告；#lrStageReview 維持 display:none。
        // D-8 擴充點：未來開 reply 框只需在此呼 showLRCheckpoint(...)，renderer 不需改。
        return;
    }

    if (resumeClass === 'offline_capped') {
        // plan 5e (iii)：達離線防呆上限被停。顯示「研究已暫停」+「繼續研究」按鈕。
        // 按鈕**使用者點擊**才走 continueLiveResearch（非自動，遵 5c read-only invariant）。
        const reasonMap = {
            next_checkpoint: '已跑到下一個段落停點',
            wall_seconds: '離線時間過長',
        };
        const reasonText = reasonMap[lrState.offline_cap_reason] || '離線保護';
        addLRChatMessage(
            'assistant',
            `<em>${escapeHTML(`研究已暫停（離線保護，原因：${reasonText}）。`)}</em>`
        );
        // 沿用 checkpoint reply UI 讓使用者主動「繼續研究」（送 continue 由使用者點擊觸發）。
        showLRCheckpoint(stage, '要繼續這份研究嗎？回覆訊息或按「讀豹決定」即可從暫停處接續。', '讀豹決定', null);
        if (stage === 5) _rerenderStage5Sections(lrState);
        return;
    }
    // resumeClass === 'in_progress' / 'checkpoint' → 落到下方 resume-notice 路徑。
    // 'checkpoint'：後端已停在 checkpoint 等回答，resume-notice + showLRCheckpoint 即正確。
    // 'in_progress'：後端仍在跑（state 未到 checkpoint），顯示可繼續輸入的 resume notice。
    //
    // A1 fix (2026-06-19): mid-flight resume MUST also replay the prior dialog
    // snapshot (the completed branch already does). The snapshot is loaded into
    // memory by loadSavedSession → setLRLoadedSnapshot BEFORE this runs. Replay
    // is pure DOM (read-only INVARIANT: no /continue, no pipeline re-run).
    const _midflightSnap = getLRLoadedSnapshot();
    if (snapshotHasReplayableEntries(_midflightSnap)) {
        // Replay the COMPLETE snapshot — including a trailing `type==='checkpoint'`
        // bubble. That bubble holds the REAL AI-generated proposal (options/evidence
        // the user must review), NOT a duplicate of the resume notice below. (3rd-round
        // Gemini AR + Zoe prod DB verify, session b08080f8.) The earlier
        // `dropTrailingCheckpoint` was REMOVED — dropping it would permanently erase
        // the only copy of the real proposal (data loss). The proposal bubble (history)
        // and the resume-notice bubble (operation entry-point, showLRCheckpoint below)
        // are DIFFERENT boxes with DIFFERENT functions and BOTH must show.
        const replayed = _replayLRSnapshotIntoChat(_midflightSnap);
        console.log('[Live Research] resume(mid-flight):', resumeClass, '— replayed', replayed, 'snapshot bubbles (full snapshot, including trailing checkpoint proposal)');
    } else {
        // NO SILENT FAIL: sessions created before snapshot persistence shipped
        // have no dialog snapshot. Tell the user why history is absent — never blank.
        // CANDIDATE A: this is a `system` (transient) box. `system` is NOT in
        // LR_REAL_CONTENT_TYPES, so addLRChatMessage does NOT mark it data-lr-content →
        // it is excluded from serialize BY DEFAULT. No per-box tagging needed and none
        // is added (the old black-list `.lr-no-serialize` line is REMOVED). This auto-
        // covers both prior harms: (a) the banner never accumulates in the snapshot, and
        // (b) it never gets serialized → the session never falsely flips to "has snapshot"
        // → the next resume still draws a fresh banner (no false "history" bubble).
        addLRChatMessage('system',
            escapeHTML('此 session 在對話快照功能上線前建立，無法重現先前的對話；以下為從中斷處繼續的進度。'));
        console.warn('[Live Research] resume(mid-flight): no replayable snapshot — degraded to resume notice only');
    }

    // Build stage-appropriate resume notice
    const stageLabels = {
        1: '階段 1 — 研究架構提案',
        2: '階段 2 — 資料蒐集與分析',
        3: '階段 3 — 文筆風格設定',
        4: '階段 4 — 章節大綱',
        5: '階段 5 — 章節撰寫',
        6: '階段 6 — 匯出',
    };
    const stageLabel = stageLabels[stage] || `階段 ${stage}`;
    const resumeNotice = `**（從中斷處繼續）${stageLabel}**\n\n` +
        `你的研究進度已保存。你可以：\n` +
        `- 直接輸入訊息繼續這個階段\n` +
        `- 按「讀豹決定」讓讀豹自動繼續`;

    // Show checkpoint bubble with resume notice + reply UI
    showLRCheckpoint(stage, resumeNotice, '讀豹決定', null);

    // If Stage 5 and there are written sections, re-render them
    if (stage === 5) _rerenderStage5Sections(lrState);
}

// plan: lr-sse-reconnect-resume — 抽出 Stage 5 已寫段落 re-render（resume + offline_capped 共用）。
function _rerenderStage5Sections(lrState) {
    const writtenSections = lrState.written_sections ?? [];
    writtenSections.forEach((section, idx) => {
        if (section && section.content) {
            addLRSection(
                idx,
                section.title || `第 ${idx + 1} 段`,
                section.content,
                section.sources ?? [],
                section.methodology_note ?? null
            );
        }
    });
}

// ============================================================================
// SSE handler
// ============================================================================

export async function handleLiveResearchSSE(response, triggeringLRSid = null) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const loadingState = document.getElementById('loadingState');
    // plan: lr-sse-reconnect-resume — 追蹤是否收到終止性事件（checkpoint / export）。
    // 若 stream 結束卻沒收到任何終止事件 = 異常斷線（後端仍在跑），顯示可恢復狀態。
    let sawTerminalEvent = false;

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            const messages = buffer.split('\n\n');
            buffer = messages.pop(); // keep incomplete message

            for (const message of messages) {
                if (!message.trim()) continue;

                const lines = message.split('\n');
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    let data;
                    try {
                        data = JSON.parse(line.slice(6));
                    } catch (e) {
                        console.error('[Live Research] Failed to parse SSE:', e, line);
                        continue;
                    }

                    console.log('[Live Research] SSE event:', data.message_type || data.type, data);

                    const type = data.message_type || data.type || '';

                    if (type === 'live_research_session_created') {
                        setLRSessionId(data.session_id);
                        console.log('[Live Research] Server session created:', getLRSessionId());
                        // LR 雙 row 收斂：把後端 row A UUID 採納為當前 session 的 _serverId，
                        // 取消前端冗餘 POST（防 row B），使 live_research_state 與 session payload 同 row。
                        if (typeof window.adoptLRServerSession === 'function') {
                            window.adoptLRServerSession(data.session_id);
                        } else {
                            console.warn('[Live Research] window.adoptLRServerSession 未掛載，雙 row 收斂跳過（resume 可能失效）');
                        }

                    } else if (type === 'low_relevance_warning') {
                        console.warn('[Relevance] Low relevance (LR):', data.content);
                        showResearchRelevanceWarning(data.content, 'relevance');

                    } else if (type === 'low_keyword_match_warning') {
                        console.warn('[Relevance] Low keyword match (LR):', data.content);
                        showResearchRelevanceWarning(data.content, 'keyword');

                    } else if (type === 'live_research_narration') {
                        // UX-1 D-6: derive dynamic activity text from narration content
                        const narrText = data.text || '';
                        const derivedActivity = deriveActivityFromNarration(narrText);
                        if (derivedActivity) _currentLRActivity = derivedActivity;
                        hideLRTypingIndicator();
                        addLRChatMessage('narration', narrText);
                        showLRTypingIndicator();
                        // Bug fix 2026-05-16 (defensive): if we are still awaiting a
                        // checkpoint reply but the reply UI got hidden by
                        // continueLiveResearch and the backend forgot to re-emit a
                        // checkpoint, restore the reply UI so user is not stuck.
                        if (_lrAwaitingCheckpointReply) {
                            const replyEl = document.getElementById('lrCheckpointReply');
                            if (replyEl && replyEl.style.display === 'none') {
                                console.warn('[Live Research] narration received while awaiting reply — restoring reply UI defensively');
                                replyEl.style.display = '';
                                const input = document.getElementById('lrReplyInput');
                                if (input) { input.value = ''; input.focus(); }
                            }
                        }

                    } else if (type === 'live_research_stage_change') {
                        // UX-1 D-6: update stage-aware indicator text
                        const stageNum = parseInt(data.stage, 10);
                        // recollect 退回偵測：新 stage < 當前 session/run 的 _currentLRStage
                        // 且曾到過 Stage 5 → 清除 Stage 5 舊 section cards + chat 泡泡。
                        // _currentLRStage 已由 reset(歸0) / restore(設真實stage) 綁 session
                        // boundary（Step 2/3），此比對不再跨 session 污染。
                        if (stageNum >= 1 && stageNum <= 6) {
                            if (stageNum < _currentLRStage && _currentLRStage >= 5) {
                                clearLRStage5Artifacts();
                            }
                            _currentLRStage = stageNum;
                            _currentLRActivity = '';  // reset activity when stage changes
                            updateLRTypingIndicatorText();
                        }
                        updateLRStageProgress(data.stage);

                    } else if (type === 'live_research_writer_status') {
                        // Stage 5 per-section writer status — typing indicator updates.
                        // Schema (backend orchestrator._emit_writer_status):
                        //   started:      { status, total_sections, completed }
                        //   section_done: { status, total_sections, completed, section_title }
                        //   all_done:     { status, total_sections, completed }
                        // Note: stop button removed (2026-06-04). VP-7 per-section
                        // checkpoint is the sole interruption path.
                        const wStatus = data.status;
                        const wTotal = data.total_sections;
                        const wDone = data.completed;
                        const wTitle = data.section_title;

                        if (wStatus === 'started') {
                            if (wTotal) {
                                _currentLRActivity = `正在寫第 ${wDone || 0}/${wTotal} 段...`;
                                updateLRTypingIndicatorText();
                            }
                        } else if (wStatus === 'section_done') {
                            // VP-7：每段完成後 writer 進 paused 狀態等 user reply。
                            // mini-checkpoint 緊接著會 emit，showLRCheckpoint() 處理 reply UI。
                            if (wTotal) {
                                _currentLRActivity = wTitle
                                    ? `第 ${wDone}/${wTotal} 段「${wTitle}」完成，等你回覆...`
                                    : `第 ${wDone}/${wTotal} 段完成，等你回覆...`;
                                updateLRTypingIndicatorText();
                            }
                        }
                        // all_done: typing indicator hide handled by subsequent checkpoint/export event

                    } else if (type === 'live_research_checkpoint') {
                        sawTerminalEvent = true;  // plan: 收到 checkpoint = 正常停點，非斷線
                        if (loadingState) loadingState.classList.remove('active');
                        hideLRTypingIndicator();
                        showLRCheckpoint(
                            data.stage,
                            data.proposal,
                            data.auto_continue_option,
                            data.evidence_list || [],  // P0 #5: pass evidence_list ([] on old sessions)
                            data.show_new_sample_button === true,  // Stage 3 風格 checkpoint 才顯示「重新提供範本」
                            data.evidence_total,  // evidence_pool 完整筆數（舊 session undefined → 退回「N 筆資料」）
                            { isRealContent: true }   // candidate A: real AI proposal → store in snapshot
                        );
                        _saveLRSnapshot('checkpoint', { triggeringLRSid });  // re-serialize #lrChat now that this stage's dialog is appended (D-7: pass captured stream id)

                    } else if (type === 'live_research_section') {
                        addLRSection(data.section_index, data.title, data.content, data.sources, data.methodology_note, data.citation_sources, data.citation_format);
                        // CEO P0 UX fix (2026-05-19): inline render prose in chat so user
                        // can review without switching tabs. Without this, user only sees
                        // "第 K/N 段完成" narration + checkpoint reply UI — cannot give
                        // revise feedback on prose they never saw (Stage 5 spec §4.7.1).
                        // G1 upsert：chat bubble 同理，同 section_index 已存在則 update-in-place
                        // G-M1 refactor (2026-05-29): 改用 addLRChatMessage(options.dataset)
                        // 消除 inline 重複的 avatar/wrapper DOM 結構；若 section_index 已在 DOM
                        // 中（revise 重跑），直接 update-in-place bubble 內容，不走 addLRChatMessage。
                        try {
                            const sectionMd = `**第 ${(parseInt(data.section_index, 10) + 1) || '?'} 段：${data.title || ''}**\n\n${data.content || ''}`;
                            const chat = document.getElementById('lrChat');
                            const existingBubble = chat && chat.querySelector(`[data-lr-section-index="${data.section_index}"]`);
                            if (existingBubble) {
                                // Update-in-place：更新 bubble 內容（revise 重跑場景）
                                const bubbleEl = existingBubble.querySelector('.lr-msg-bubble');
                                if (bubbleEl) bubbleEl.innerHTML = DOMPurify.sanitize(marked.parse(sectionMd));
                            } else {
                                // 新 bubble：走 addLRChatMessage，由 options.dataset 注入 data-lr-section-index
                                addLRChatMessage('section', sectionMd, { dataset: { lrSectionIndex: data.section_index } });
                            }
                        } catch (e) {
                            console.warn('[Live Research] inline section render failed:', e);
                        }

                    } else if (type === 'live_research_export') {
                        sawTerminalEvent = true;  // plan: 收到 export = 正常完成，非斷線
                        if (loadingState) loadingState.classList.remove('active');
                        hideLRTypingIndicator();
                        setProcessingState(false);
                        showLRExport(data.content, data.format, data.citation_sources, data.citation_format);
                        // Track D D2b (sprint 2026-05-28): KG full D3 graph render
                        // D-AMB-3 LOCKED Option (d) 雙路: SSE event 帶 knowledge_graph
                        // payload (None / dict)。
                        // D-CEO-Q4 LOCKED Option (β): containerPrefix='lrKG' →
                        // displayKnowledgeGraph 操作 #lrKGDisplayContainer 等 LR id
                        if (data.knowledge_graph) {
                            displayKnowledgeGraph(data.knowledge_graph, { containerPrefix: 'lrKG' });
                        }
                        // LR Bug 3 fix (2026-05-19): Stage 6 export emitted → LR run
                        // officially complete. Release the guard so subsequent
                        // UserStateSync resets (logout / user switch) can clear
                        // currentLRSessionId normally.
                        setLRInProgress(false);
                        // D-6: final snapshot. await dispatch (handleLiveResearchSSE is async, :2051) so the PUT
                        // is fired before the handler returns — best-effort vs instant reload; backend state
                        // (_persist_checkpoint_boundary) + restore fallback already prevent data loss.
                        await _saveLRSnapshot('export', { immediate: true, triggeringLRSid });

                    } else if (type === 'research_phase') {
                        // FIX-7a (2026-05-29): bab_phase4 是每 iteration 都會觸發的中間事件，
                        // 不是整個研究的終止訊號。用 per-phase 的非終止字眼，避免 user 誤以為
                        // 研究已結束。「完成」→「本輪更新完成」（僅 bab_phase4）。
                        const phaseLabels = {
                            'bab_phase0': '建立初始研究結構',
                            'bab_phase1': '推導搜尋計畫',
                            'bab_phase2': '執行資料蒐集',
                            'bab_phase3': '深入分析與交叉檢驗',
                            'bab_phase4': '本輪結構調整',
                        };
                        // bab_phase4 用非終止狀態字眼（本輪更新）；其他 phase 維持「完成」。
                        const statusLabels = {
                            'started': '開始中',
                            'completed': '完成',
                        };
                        const phase4StatusLabels = {
                            'started': '開始中',
                            'completed': '本輪更新完成',
                        };
                        const isBabPhase4 = data.phase === 'bab_phase4';
                        const phaseLabel = phaseLabels[data.phase] || data.phase || '';
                        const statusLabel = (isBabPhase4 ? phase4StatusLabels : statusLabels)[data.status] || data.status || '';
                        const txt = phaseLabel + (statusLabel ? '⋯' + statusLabel : '');
                        if (txt) addLRChatMessage('narration', txt);

                    } else if (type === 'error') {
                        console.error('[Live Research] Server error:', data.error || data.message);
                        hideLRTypingIndicator();
                        addLRChatMessage('error', data.error || data.message || '發生未知錯誤');

                    } else if (type === 'begin-nlweb-response' || type === 'begin_nlweb_response') {
                        if (data.conversation_id) setCurrentConversationId(data.conversation_id);

                    } else if (type === 'intermediate_result') {
                        // Ignore or show as narration
                        if (data.text) addLRChatMessage('narration', data.text);

                    } else if (type === 'clarification_required') {
                        console.log('[Live Research] Clarification required:', data.clarification);
                        // Build a user-friendly clarification message in Traditional Chinese
                        const clarObj = data.clarification;
                        let clarHTML;
                        if (typeof clarObj === 'object' && clarObj !== null && clarObj.questions) {
                            // Full structured clarification — render questions as list
                            const instruction = clarObj.instruction || '為了更精準地進行研究，請提供更多資訊：';
                            let questionsHTML = '';
                            clarObj.questions.forEach(q => {
                                questionsHTML += `<p><strong>${DOMPurify.sanitize(q.question || q.text || '')}</strong></p>`;
                                if (q.options && q.options.length) {
                                    questionsHTML += '<ul>';
                                    q.options.forEach(opt => {
                                        const optLabel = typeof opt === 'object' ? (opt.label || opt.text || String(opt)) : String(opt);
                                        questionsHTML += `<li>${DOMPurify.sanitize(optLabel)}</li>`;
                                    });
                                    questionsHTML += '</ul>';
                                }
                            });
                            clarHTML = `<div class="lr-clarification-block"><p>${DOMPurify.sanitize(instruction)}</p>${questionsHTML}<p class="lr-clarification-hint">請在下方輸入你的回答，讀豹會重新開始研究。</p></div>`;
                        } else {
                            // Simple text clarification
                            const clarText = typeof clarObj === 'object' && clarObj !== null
                                ? (clarObj.instruction || clarObj.text || clarObj.question || '讀豹需要更多資訊才能繼續研究。')
                                : String(clarObj || '讀豹需要更多資訊才能繼續研究。');
                            clarHTML = DOMPurify.sanitize(marked.parse(clarText));
                        }
                        // Display clarification in LR chat area — real content (AI asking the
                        // user for info), so mark it for the snapshot.
                        addLRChatMessage('checkpoint', clarHTML, { isRealContent: true });
                        // Show reply UI so user can respond via continueLiveResearch
                        if (loadingState) loadingState.classList.remove('active');
                        hideLRTypingIndicator();
                        setProcessingState(false);
                        const replyEl = document.getElementById('lrCheckpointReply');
                        if (replyEl) {
                            replyEl.style.display = '';
                            const input = document.getElementById('lrReplyInput');
                            if (input) { input.value = ''; input.focus(); }
                        }
                    }
                }
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') {
            // plan: lr-sse-reconnect-resume — SSE 斷線但研究仍在後端跑。
            // 不可顯示終止性 error（後端沒死）；改顯示可恢復狀態 + 標記斷線供喚醒重連。
            console.warn('[Live Research] Stream error (research continues on server):', e);
            _lrConnectionLost = true;
            showLRConnectionInterrupted();
            sawTerminalEvent = true;  // 已處理斷線，避免下方重複提示
        }
    } finally {
        try { reader.cancel(); } catch (_) {}
        if (loadingState) loadingState.classList.remove('active');
        hideLRTypingIndicator();
        setProcessingState(false);
    }

    // plan: stream 正常結束（done）但未收到任何終止事件（checkpoint / export）=
    // 異常斷線（後端仍在跑到 checkpoint）。顯示可恢復狀態 + 標記斷線供喚醒重連。
    // 不 clear currentLRSessionId（resetLiveResearchUI 的 skipIfInflight 已保護）。
    if (!sawTerminalEvent) {
        _lrConnectionLost = true;
        showLRConnectionInterrupted();
    }
}

// ============================================================================
// Main entry points
// ============================================================================

export async function performLiveResearch(query) {
    console.log('=== Live Research Mode (6-Stage) ===');

    // Reset UI
    resetLiveResearchUI();
    // 初始研究提問顯示為 user 泡泡（stage 0），讓 DOM snapshot 收進回顧；同時是 live UX 改善
    // （user 看得到自己問了什麼）。此時 _currentLRStage === 0（reset 後）→ wrapper.dataset.lrStage='0'。
    if (query && String(query).trim()) {
        addLRChatMessage('user', String(query));
    }
    // LR Bug 3 fix (2026-05-19): mark LR inflight so UserStateSync.clearUserScopedState
    // (triggered by background 401 → refresh-fail → _handleAuthFailure) does NOT wipe
    // currentLRSessionId mid-run. Cleared on Stage 6 export, or fresh resetLiveResearchUI.
    setLRInProgress(true);
    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.classList.add('active');
    const lrTab = document.querySelector('.tab[data-view="live-research"]');
    if (lrTab) lrTab.click();
    // Hide main search input while LR is active — user interacts via lrCheckpointReply
    const searchContainer = document.getElementById('searchContainer');
    if (searchContainer) searchContainer.style.display = 'none';
    // LR uses in-chat typing indicator instead of top spinner (UX-1 D-3 + D-6)
    resetLRTypingState();
    showLRTypingIndicator();
    setProcessingState(true);

    // Save to history
    pushConversationHistory(query);
    markSessionDirty();  // RCA Fix 1: new live research query is new content
    if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();

    // Call new /api/live_research endpoint
    const searchParams = new URLSearchParams(window.location.search);
    const isMockLR = searchParams.get('mock_lr') === 'true';
    const isDryRunLR = searchParams.get('dry_run_lr') === 'true';
    const url = new URL('/api/live_research', window.location.origin);
    const body = JSON.stringify({
        query: query,
        session_id: getCurrentSessionId(),
        site: getSelectedSitesParam(),
        // F1 (2026-06-08): CEO 拍板 LR web search default-on（無 UI toggle）。
        // 解鎖 loop_engine 已 land 的 web 能力；內部仍由 analyst source_strategy
        // + Track C C3 國際 keyword 四閘 gate 智能觸發，純台灣題不會打 Google。
        enable_web_search: true,
        // F2 (2026-06-11): CEO 拍板 enable_gap_enrichment default-on（全 4 類一起開）。
        // 後端 loop_engine._process_gap_resolutions_lr 4 類（llm_knowledge / wikipedia /
        // web_search / internal）已 ready（commit 2427d515），前端補接線解鎖。
        // Default-on 紀律：gate/guard 類 default-off（安全閾值），enrichment 類 default-on
        // （符合 CEO 期望 prod 行為）。
        enable_gap_enrichment: true,
        ...(isMockLR ? { mock: true } : {}),
        ...(isDryRunLR ? { dry_run: 'true' } : {})
    });

    try {
        // RCA fix 2026-05-19: 改走 authenticatedFetch 啟用 401 refresh-then-retry，
        // 避免 idle 期間 access_token cookie 過期後 raw fetch 無 Bearer header
        // → middleware dev-bypass 靜默放行 → user_id='' → R5 narration。
        // authenticatedFetch 不會 await body（line 196/236 直接 return Response），
        // 對 SSE streaming 兼容。
        console.log('[Live Research] Initial via authenticatedFetch');
        const response = await window.authManager.authenticatedFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.message || `HTTP ${response.status}`);
        }

        await handleLiveResearchSSE(response, getLRSessionId());  // D-7: capture triggering stream's LR session id (perform; may be null pre-adopt — acceptable)
    } catch (e) {
        console.error('[Live Research] Error:', e);
        const loadingState = document.getElementById('loadingState');
        if (loadingState) loadingState.classList.remove('active');
        hideLRTypingIndicator();
        setProcessingState(false);
        addLRChatMessage('error', '研究啟動失敗：' + e.message);
    }
}

export async function continueLiveResearch(userMessage, autoContinue, navAction = '') {
    console.log('[Live Research] Continue — autoContinue:', autoContinue, 'navAction:', navAction, 'message:', userMessage);

    // LR Bug 3 fix (2026-05-19): re-assert inflight flag — continueLiveResearch is
    // called per mini-checkpoint reply throughout the LR run, all before Stage 6 export.
    setLRInProgress(true);
    // Hide reply UI
    const replyEl = document.getElementById('lrCheckpointReply');
    if (replyEl) replyEl.style.display = 'none';
    // Bug fix 2026-05-16: clear awaiting flag — server will re-emit checkpoint if still needed
    _lrAwaitingCheckpointReply = false;
    // LR uses in-chat typing indicator instead of top spinner (UX-1 D-3 + D-6)
    // Note: keep currentLRStage from prior stage_change; reset activity only
    _currentLRActivity = '';
    showLRTypingIndicator();

    // Show user's reply in chat
    const STAGE3_NEW_SAMPLE_SENTINEL = '__LR_STAGE3_NEW_SAMPLE__';
    if (navAction) {
        // backward-nav 路徑：語意化系統訊息已由 news-search.js 按鈕 handler 加，
        // 此處不再加「（讀豹決定）」避免重複/誤導（plan: lr-backward-nav）。
    } else if (!autoContinue && userMessage === STAGE3_NEW_SAMPLE_SENTINEL) {
        // 「重新提供範本」按鈕：顯示語意化系統訊息，而非裸 sentinel 字串。
        addLRChatMessage('system', '（重新提供範本）');
    } else if (!autoContinue && userMessage) {
        addLRChatMessage('user', userMessage);
    } else {
        addLRChatMessage('system', '（讀豹決定）');
    }

    const searchParamsContinue = new URLSearchParams(window.location.search);
    const isMockLR = searchParamsContinue.get('mock_lr') === 'true';
    const isDryRunLRContinue = searchParamsContinue.get('dry_run_lr') === 'true';
    const url = new URL('/api/live_research/continue', window.location.origin);
    const body = JSON.stringify({
        session_id: getCurrentSessionId(),     // frontend session ID (auth/analytics)
        lr_session_id: getLRSessionId(),       // server-generated UUID for state persistence
        user_message: userMessage || '',
        auto_continue: autoContinue || false,
        // F2 (2026-06-11): Stage 2 per-topic BAB 跑在 continue request（orchestrator.py:835）。
        // 修前 continue body 不帶這兩個 flag → handler.enable_web_search/enable_gap_enrichment
        // = False → Stage 2 BAB 永不跑 web/gap（re-audit B6）。修後補帶，與 performLiveResearch
        // 對齊——前端為 source of truth，不依賴 stage_state 持久化（形態 a 決策）。
        enable_web_search: true,
        enable_gap_enrichment: true,
        // backward-nav（plan: lr-backward-nav）：只在非空時帶，保持 backward compat。
        ...(navAction ? { nav_action: navAction } : {}),
        ...(isMockLR ? { mock: true } : {}),
        ...(isDryRunLRContinue ? { dry_run: 'true' } : {})
    });

    try {
        // RCA fix 2026-05-19: 改走 authenticatedFetch 啟用 401 refresh-then-retry。
        // 真因：idle 11+ 分鐘 cookie expire → raw fetch 無 Bearer header
        // → middleware dev-bypass 靜默放行 → user_id='' → 「找不到研究 session」。
        // 改 authenticatedFetch 後：401 自動 trigger refreshToken → 新 access_token
        // → retry continue → user_id 正確。Refresh fail 走 _handleAuthFailure
        // （Option C lrInProgress guard 保護 _user mutation）。
        // SSE 兼容性：authenticatedFetch 不 await body（line 196/236 直接 return
        // Response object），response.body.getReader() stream 可正常讀。
        console.log('[Live Research] Continue via authenticatedFetch');
        const response = await window.authManager.authenticatedFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body
        });

        if (response.status === 401) {
            // CEO P0 UX fix (2026-05-19): authenticatedFetch refresh-then-retry 失敗
            // → _handleAuthFailure 已 trigger（login modal shown + state reset）。
            // 顯示 friendly narration 而非 raw "HTTP 401" — 對齊 spec §5.3。
            const loadingState = document.getElementById('loadingState');
            if (loadingState) loadingState.classList.remove('active');
            hideLRTypingIndicator();
            setProcessingState(false);
            addLRChatMessage('error', '登入已過期，請重新登入後再繼續研究。');
            return;
        }

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.message || `HTTP ${response.status}`);
        }

        await handleLiveResearchSSE(response, getLRSessionId());  // D-7: capture triggering stream's LR session id (continue; non-null — sent in body)
    } catch (e) {
        console.error('[Live Research] Continue error:', e);
        const loadingState = document.getElementById('loadingState');
        if (loadingState) loadingState.classList.remove('active');
        hideLRTypingIndicator();
        setProcessingState(false);
        // UX 止血（2026-06-20）：continue 失敗（如 HTTP 500）時，continueLiveResearch 開頭
        // 已把 reply UI 隱藏（:2587-2590 replyEl.display='none' + _lrAwaitingCheckpointReply=false）。
        // 成功路徑靠 server re-emit checkpoint → showLRCheckpoint 復原 reply UI；error 路徑
        // 不會收到 checkpoint，若不手動復原 reply UI，user 會卡死無重試入口（lr-e2e-rca-2026-05-16
        // 同類根因：handler 隱藏 reply UI 後未復原）。此處復原 reply 容器 + awaiting 旗標，
        // 讓 user 能再送一次 continue（按鈕/標籤維持前一個 showLRCheckpoint 的最後渲染狀態）。
        const replyEl = document.getElementById('lrCheckpointReply');
        if (replyEl) {
            replyEl.style.display = '';
            const input = document.getElementById('lrReplyInput');
            if (input) input.focus();
        }
        _lrAwaitingCheckpointReply = true;
        // 友善文案：明確告知可重試，避免 user 誤以為研究已終止（no silent fail：仍保留 error 來源）。
        addLRChatMessage('error', '繼續研究時連線出了狀況（' + e.message + '）。研究仍在進行，請再送一次繼續，或點「讀豹決定」讓讀豹接手。');
    }
}
