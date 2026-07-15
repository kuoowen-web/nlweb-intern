// static/js/core/page-bootstrap.js
//
// D-1 Module Header — Page Lifecycle Bootstrap
//   Owned state:
//     - DOM lifecycle event registrations (visibilitychange listener register)
//
//   **Codex Round 3 Important 5 — Trigger F ownership 明確分工**：
//     所有 DOM event 註冊都在這個 module；state-sync.js 不可註冊 event (只提供 runInitSync API)。
//
//   Triggers initiated here:
//     - B (identity change): checkAuthOnLoad detects cached.user_id ≠ JWT.user_id → window.UserStateSync.runInitSync()
//     - F (page reload cold-start): main.js DOMContentLoaded → bootstrapPage() → checkAuthOnLoad
//     - F (tab visible warm-start): visibilitychange → window.UserStateSync.runInitSync() (on mismatch) /
//       window._hydrateFromSoftRefreshInit (on soft-refresh path)
//
//   External read interface:
//     - export { bootstrapPage }
//
// Imports:
//   - import { authManager } from './auth-manager.js' (read JWT identity)
//   - import { UserStateSync, UserStateSyncError, assertUserIdentity } from './state-sync.js'
//     (thin alias forwards to news-search.js IIFE)
//
// D-13 Compliance:
//   This module is INERT on import. All DOM event registrations only happen
//   inside the exported `bootstrapPage()` called by main.js — NOT in module top-level.
//
// Phase 3 Path B (2026-05-21):
//   Extracted from static/news-search.js:
//   - `async function checkAuthOnLoad` (was line 1009-1117)
//   - `document.addEventListener('visibilitychange', ...)` handler (was line 1130-1210)
//   - DOMContentLoaded handler's `await checkAuthOnLoad()` call (was line 1121)
//
//   Path B sequencing constraint: the original visibilitychange handler's soft-refresh
//   body directly mutates `savedSessions` (let array) and reassigns `_sharedSessionsCache`
//   (let null/array) — both are outer-scope `let` declarations in news-search.js classic
//   script. ES modules cannot reassign classic-script `let` bindings across the script
//   type boundary. To preserve correctness without forcing Phase 4a (which migrates these
//   state owners to features/sessions-list.js), we collapse the soft-refresh body into a
//   single setter `window._hydrateFromSoftRefreshInit(init)` defined in news-search.js.
//   Phase 4a will replace this setter with exported `hydrateSavedSessions(sessions)` and
//   `hydrateSharedSessionsCache(shared)` from features/sessions-list.js.

import { authManager } from './auth-manager.js';
import { UserStateSync, UserStateSyncError, assertUserIdentity } from './state-sync.js';
// v4.0 Commit 10 (2026-05-24): atomic hydrate from owner module (D-V4 H2 fix) —
// replaces `window._hydrateFromSoftRefreshInit` bridge on visibilitychange soft refresh.
import { hydrateFromSoftRefreshInit } from '../features/sessions-list.js';
// v4.0 Commit 7 (2026-05-24): LR Bug 3 guards now read isLRInProgress() from the
// owner module instead of `window.lrInProgress` (bridge removed in this commit).
// D-V6 import direction OK: page-bootstrap.js depends on features/live-research.js
// (which depends on core/state-sync.js — no cycle since state-sync.js does not
// import this module).
import { isLRInProgress } from '../features/live-research.js?v=20260714a';

/**
 * Trigger B + F (Task 7 + page-load identity guard).
 * Reads /api/auth/me, compares to cached authUser, triggers full reset on mismatch.
 * Idempotent: safe to call multiple times (the soft-refresh path is a no-op if
 * already in sync).
 */
async function checkAuthOnLoad() {
    try {
        let res = await authManager.authenticatedFetch('/api/auth/me');
        if (res.status === 401) {
            // Try to refresh token before giving up (prevents login modal flash on valid sessions)
            try {
                await authManager.refreshToken();
                res = await authManager.authenticatedFetch('/api/auth/me');
            } catch (refreshErr) {
                // Refresh failed — fall through to show login modal
            }
        }
        if (res.status === 401) {
            // Y-2/Y-3 fix: 401 path must fully clear stale auth state.
            // Previously only hideMainUI + showAuthModal — but authManager._user
            // remained populated from localStorage cache, so isLoggedIn() returned
            // true. The subsequent loadSessions then silently fell back to
            // localStorage and loaded the *previous* user's sessions (cross-user
            // leak via in-memory savedSessions). _handleAuthFailure clears _user,
            // localStorage, savedSessions, re-renders, hides UI, and shows modal.
            authManager._handleAuthFailure();
            return;
        }
        if (res.ok) {
            const data = await res.json();
            if (data.user) {
                // Trigger B (user identity change): use assertUserIdentity
                // invariant helper. On MISMATCH, full reset + init sync
                // (NOT case-by-case clearing). On MISSING_CACHED (first
                // load), just persist authUser and proceed. On
                // MISSING_FRESH (server returned no user.id) — abnormal,
                // log + fall through to login modal.
                const cached = (() => {
                    try { return JSON.parse(localStorage.getItem('authUser') || 'null'); }
                    catch (_) { return null; }
                })();

                let mismatch = false;
                let freshMissing = false;
                try {
                    assertUserIdentity(cached, data.user);
                } catch (e) {
                    if (e instanceof UserStateSyncError) {
                        if (e.code === 'MISMATCH') {
                            console.warn('[checkAuthOnLoad] user identity mismatch, triggering full reset:', e.message);
                            mismatch = true;
                        } else if (e.code === 'MISSING_CACHED') {
                            // First-time load on this browser; normal — no cached user to compare.
                        } else if (e.code === 'MISSING_FRESH') {
                            console.error('[checkAuthOnLoad] /api/auth/me returned user without id; refusing to apply:', e.message);
                            freshMissing = true;
                        } else {
                            throw e;
                        }
                    } else {
                        throw e;
                    }
                }

                if (freshMissing) {
                    // Backend anomaly: treat as auth failure to surface visibly.
                    authManager._handleAuthFailure();
                    return;
                }

                // LR Bug 3 root fix (2026-05-19, 對稱補 dac83ce): page reload resets
                // lrInProgress to false (module-level let), so this guard is normally
                // a no-op here. Kept for symmetry with the other two _user mutation
                // points (visibilitychange line ~1140, applyInit line ~1754) so future
                // refactors that move LR state into persistent storage don't silently
                // regress this invariant.
                // v4.0 Commit 7 (2026-05-24): read via isLRInProgress() from owner module
                // (was `window.lrInProgress` — bridge removed in this commit).
                if (isLRInProgress()) {
                    const cachedUid = authManager._user && authManager._user.id;
                    const incomingUid = data.user && data.user.id;
                    console.warn('[UserStateSync] LR active — keeping authManager._user (user_id=' + cachedUid + ', incoming user_id=' + incomingUid + ')');
                } else {
                    authManager._user = data.user;
                }
                if (mismatch) {
                    // Full sync: clear stale state, fetch fresh /api/user/init, apply.
                    try {
                        await UserStateSync.runInitSync({ keepInviteToken: false });
                    } catch (e) {
                        console.error('[checkAuthOnLoad] runInitSync after mismatch failed:', e);
                        // Persist new authUser as fallback so subsequent reload can recover.
                        try { localStorage.setItem('authUser', JSON.stringify(data.user)); } catch (_) {}
                    }
                } else {
                    // Same user (or first load) — soft path: just persist authUser.
                    // Sidebar refresh is handled by the existing post-checkAuthOnLoad
                    // sessionManager.loadSessions() block (still in news-search.js
                    // DOMContentLoaded handler, runs after bootstrapPage()).
                    try { localStorage.setItem('authUser', JSON.stringify(data.user)); } catch (_) {}
                }
            }
            // Legacy global UI functions still in news-search.js. classic-script
            // function declarations auto-attach to window in non-strict mode.
            if (typeof window.hideAuthModal === 'function') window.hideAuthModal();
            if (typeof window.showMainUI === 'function') window.showMainUI();
            if (typeof window.updateAuthUI === 'function') window.updateAuthUI();
        }
    } catch (e) {
        console.warn('[AuthGuard] /api/auth/me failed:', e);
        // On network error, allow UI if we have cached auth
        if (!authManager.isLoggedIn()) {
            if (typeof window.hideMainUI === 'function') window.hideMainUI();
            if (typeof window.showAuthModal === 'function') window.showAuthModal('login');
        }
    } finally {
        // _authReadyResolve is a classic-script outer `let` resolver in news-search.js
        // (await _authReady is used by other code that gates on auth). Access via window.
        if (typeof window._authReadyResolve === 'function') {
            window._authReadyResolve();
        }
    }
}

/**
 * Trigger F warm-start: tab-visibility identity check + soft refresh.
 * Mirrors checkAuthOnLoad's Trigger B semantics on every visibilitychange to
 * 'visible' — but lighter (skip if not logged in / network error / 401 already
 * handled by authenticatedFetch).
 */
function registerVisibilityChangeListener() {
    // Trigger F (Task 10): tab-visibility identity invariant.
    // DOMContentLoaded path is already handled by checkAuthOnLoad's
    // Trigger B (Task 7) MISMATCH branch — re-running checkAuthOnLoad
    // here would dedupe naturally but adds an extra /api/auth/me call
    // on every visibility change. Instead, run a lighter inline check
    // mirroring Trigger B semantics: /api/auth/me → assertUserIdentity →
    // mismatch ⇒ runInitSync; match ⇒ soft refresh sessions/shared.
    document.addEventListener('visibilitychange', async () => {
        if (document.visibilityState !== 'visible') return;
        if (!authManager.isLoggedIn()) return;

        let res;
        try {
            res = await authManager.authenticatedFetch('/api/auth/me', { method: 'GET' });
        } catch (e) {
            console.error('[visibilitychange] /api/auth/me network error:', e);
            return;
        }
        if (!res.ok) {
            // 401 path already handled by _handleAuthFailure inside authenticatedFetch.
            return;
        }
        let body;
        try { body = await res.json(); } catch (_) { return; }
        if (!body.success || !body.user) return;

        let mismatch = false;
        try {
            assertUserIdentity(authManager._user, body.user);
        } catch (e) {
            if (e instanceof UserStateSyncError && e.code === 'MISMATCH') {
                mismatch = true;
            } else if (e instanceof UserStateSyncError) {
                // MISSING_CACHED / MISSING_FRESH — treat as no-op for tab-visibility.
                console.warn('[visibilitychange] identity check skipped:', e.code);
                return;
            } else {
                throw e;
            }
        }

        if (mismatch) {
            console.warn('[visibilitychange] identity mismatch, triggering full reset.');
            // LR Bug 3 root fix (2026-05-19, 對稱補 dac83ce): mid-LR tab switch can
            // hit this path if backend identity drifts (token rotation, JWT claim
            // refresh). Preserve authManager._user so next LR continue POST keeps
            // the user_id that owns the lr_session row. runInitSync's applyInit has
            // its own twin guard, but we also skip this pre-emptive assignment to
            // avoid a transient window where _user is the new identity before
            // applyInit runs.
            // v4.0 Commit 7 (2026-05-24): read via isLRInProgress() from owner module.
            if (isLRInProgress()) {
                const cachedUid = authManager._user && authManager._user.id;
                const incomingUid = body.user && body.user.id;
                console.warn('[UserStateSync] LR active — keeping authManager._user (user_id=' + cachedUid + ', incoming user_id=' + incomingUid + ')');
            } else {
                authManager._user = body.user;
            }
            await UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                console.error('[visibilitychange] runInitSync failed:', err));
            return;
        }

        // Same user → soft refresh. Do NOT call applyInit (which appends to
        // savedSessions without clearing). Replace sidebar lists in-place so
        // changes from other tabs (rename, new session) become visible without
        // a full UI reset.
        //
        // Path B note: `savedSessions` (let array) and `_sharedSessionsCache` (let null/array)
        // are classic-script outer scope in news-search.js. ES modules cannot reassign these
        // bindings. The soft-refresh body has been encapsulated in news-search.js as
        // `window._hydrateFromSoftRefreshInit(init)`. Phase 4a will replace this setter with
        // proper exported hydrate functions from features/sessions-list.js.
        try {
            const init = await UserStateSync.fetchInit();
            // v4.0 Commit 10 (2026-05-24): atomic hydrate from owner module (D-V4 H2 fix).
            // Replaces window._hydrateFromSoftRefreshInit bridge. The new helper internally
            // does: Array.isArray guard per field + explicit clearSavedSessions before
            // hydrate (REPLACE semantic mirror of source line 11687) + single rAF render trigger.
            hydrateFromSoftRefreshInit(init);
        } catch (e) {
            console.error('[visibilitychange] soft refresh failed:', e);
        }
    });
}

/**
 * Bootstrap entry — called by main.js inside DOMContentLoaded handler.
 * Orchestrates Trigger B/F cold-start + registers Trigger F warm-start listener.
 *
 * Order matters: checkAuthOnLoad is await'd so subsequent UI code (in news-search.js
 * DOMContentLoaded handler, which runs after main.js's handler resolves) sees a
 * stable auth state.
 */
export async function bootstrapPage() {
    // Trigger B (cold-start identity check) + Trigger F (page-load path)
    await checkAuthOnLoad();
    // Trigger F (warm-start: tab visibility)
    registerVisibilityChangeListener();
}
