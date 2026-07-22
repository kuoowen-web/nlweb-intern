// static/js/core/state-sync.js
//
// D-1 Module Header — State Sync Coordinator (REAL IIFE — Path A completion, commit 11)
//
//   Owned state:
//     - UserStateSync IIFE (single source-of-truth for clearing + applying user-scoped state)
//     - _inflight Promise (de-dupes concurrent runInitSync calls)
//     - _backrefs bundle (D-V3 injection point for LR closures)
//
//   Triggers writing this state (7-trigger A-G via UserStateSync API):
//     - A (Login): authManager.login() → UserStateSync.runInitSync({ keepInviteToken: true })
//     - B (Identity change): page-bootstrap.checkAuthOnLoad detects cached.user_id ≠ JWT.user_id
//                            → UserStateSync.runInitSync({ keepInviteToken: false })
//     - C (Logout) / D (401 / refresh fail): authManager._handleAuthFailure()
//                                            → UserStateSync.fullReset({ keepInviteToken: false })
//     - F (page load + visibilitychange): page-bootstrap.bootstrapPage()
//
//   External read interface:
//     - export { UserStateSync, UserStateSyncError, assertUserIdentity, injectStateSyncBackref }
//
// D-3 Cross-Module Communication (D-V3 strict):
//   This module imports clearX/hydrateX helpers from feature modules. It does NOT
//   directly import features/live-research.js (would create circular dependency via
//   live-research.js → state-sync.js injection point). LR state access goes through
//   _backrefs bundle injected by live-research.js at module init time.
//
// D-13 Compliance:
//   This module is INERT on import — only declarations + export bindings + inert
//   default _backrefs (no-op functions). The IIFE itself runs once at module eval
//   time but only constructs closures — no DOM / network / storage writes.
//
// v4.0 Commit 11 (2026-05-24, Path A completion):
//   IIFE faithfully ported from news-search.js:1825-2078 (pre-commit-11 state).
//   Critical invariants preserved (per plan §3.11 6 P0 regression risks):
//     1. LR Bug 3 guards — clearUserScopedState skips clearLRSessionId during LR active;
//        applyInit skips hydrateAuthUser during LR active (preserves authManager._user).
//     2. taiwanNewsSessionsMigrated stamp — folded into hydrateSavedSessions (sessions-list.js).
//     3. assertUserIdentity(cached, fresh) — 2-arg signature preserved.
//     4. sessionManager.clearOnUserSwitch() does NOT exist — inline _cancelPendingSave()
//        + _postedRecently.clear() preserved.
//     5. document.dispatchEvent('user-state-synced', { detail: { user: payload.user } }) —
//        document (NOT window) + { user } (NOT { userId }) shape preserved.
//     6. window.resetConversation() / window.updateAuthUI() — explicit window prefix
//        (module scope does NOT fall through to global; per Gemini Final Review Finding 2).
//
// Replaces thin-alias mode of commits 3-10. news-search.js IIFE + UserStateSyncError class
// + assertUserIdentity function all DELETED in this commit.

import { authManager, AuthManager, hydrateAuthUser } from './auth-manager.js';
// Helper imports for clearX/hydrateX (commits 1-10 owner modules)
import {
    clearSavedSessions, hydrateSavedSessions,
    clearCurrentLoadedSessionId, clearSessionHistory,
    clearSharedSessions, hydrateSharedSessions,
    renderLeftSidebarSessions
} from '../features/sessions-list.js';
import {
    clearConversationHistory, clearAccumulatedArticles, clearCurrentConversationId
} from '../features/search.js?v=20260717a';
import { clearChatHistory } from '../features/chat.js?v=20260714a';
import { clearPinnedMessages, clearPinnedNewsCards } from '../features/pins.js';
import {
    clearResearchReport, clearArgumentGraph, clearChainAnalysis
} from '../features/research.js';
import { clearShareContentOverride } from '../features/sharing.js';
import {
    clearFolders, clearSourceFolders, clearFileFolders, clearSelectedFileIds
} from '../features/folders.js';
import { clearAnalyticsQueryId } from '../utils/analytics.js';
import { clearCurrentMode } from '../features/mode.js';
// D-V14: _sessionDirty owned by session-manager.js (NOT sessions-list.js).
// Per Gemini Final Review Finding 1: import getSessionManager (factory function),
// NOT a `sessionManager` named instance (session-manager.js does NOT export the instance).
import { getSessionManager, clearSessionDirty } from '../features/session-manager.js';

// ============================================================================
// UserStateSyncError + assertUserIdentity — moved from news-search.js
// (lines 57-71 in news-search.js pre-commit-11).
// ============================================================================

export class UserStateSyncError extends Error {
    constructor(message, code) {
        super(message);
        this.name = 'UserStateSyncError';
        this.code = code || 'MISMATCH';
    }
}

/**
 * Assert that cached identity matches fresh identity. 2-arg signature.
 * @param {object|null} cached - cached user (must have .id)
 * @param {object|null} fresh - freshly verified user (must have .id)
 * @throws {UserStateSyncError} on missing cached / missing fresh / id mismatch.
 */
export function assertUserIdentity(cached, fresh) {
    if (!cached || !cached.id) {
        throw new UserStateSyncError('cached identity missing', 'MISSING_CACHED');
    }
    if (!fresh || !fresh.id) {
        throw new UserStateSyncError('fresh identity missing', 'MISSING_FRESH');
    }
    if (cached.id !== fresh.id) {
        throw new UserStateSyncError(
            `user_id mismatch: cached=${cached.id} fresh=${fresh.id}`,
            'MISMATCH'
        );
    }
    return true;
}

// ============================================================================
// D-V3 injectStateSyncBackref — LR closure bundle injection point
//
// live-research.js calls this at module init time to register a small bundle of
// live closures. UserStateSync.clearUserScopedState (LR Bug 3 guard) +
// applyInit (LR Bug 3 mirror) read via _backrefs.X() — never direct import.
//
// Initial defaults are inert (no-op safe) so the inject point is safe to call
// before live-research.js has loaded (load-order race-safe).
// ============================================================================
let _backrefs = {
    isLRInProgress: () => false,
    getLRSessionId: () => null,
    clearLRSessionId: () => {},
};

export function injectStateSyncBackref(refs) {
    _backrefs = { ..._backrefs, ...refs };
}

// Expose for any callsite that needs post-inject runtime read.
export function getStateSyncBackrefs() {
    return _backrefs;
}

// ============================================================================
// UserStateSync IIFE — ported from news-search.js:1825-2078
// ============================================================================

export const UserStateSync = (() => {
    let _inflight = null;

    /**
     * Clear ALL user-scoped state. Faithful port of news-search.js:1827-1906.
     * @param {object} options - { keepInviteToken: boolean = true }
     */
    function clearUserScopedState({ keepInviteToken = true } = {}) {
        // A. localStorage user-scoped keys
        try {
            localStorage.removeItem('authUser');
            localStorage.removeItem('authAccessToken');
            for (const key of AuthManager.USER_SCOPED_KEYS) {
                localStorage.removeItem(key);
            }
        } catch (e) {
            console.error('[UserStateSync] localStorage clear failed:', e);
        }

        // B. sessionStorage
        try {
            if (!keepInviteToken) {
                sessionStorage.removeItem('pendingInviteToken');
            }
            // nlweb_session_id is tab-scoped, NOT cleared.
        } catch (e) {
            console.error('[UserStateSync] sessionStorage clear failed:', e);
        }

        // C. In-memory state owners (clearX helpers per commits 1-10 owner modules)
        try {
            clearSavedSessions();
            clearConversationHistory();
            clearSessionHistory();
            clearChatHistory();
            clearPinnedMessages();
            clearPinnedNewsCards();
            clearAccumulatedArticles();
            clearResearchReport();
            clearArgumentGraph();
            clearChainAnalysis();
            clearShareContentOverride();

            // LR Bug 3 guard (P0 invariant #1): preserve currentLRSessionId during LR active.
            // Mirror source lines 1873-1880 — uses _backrefs since live-research.js lives
            // in features/live-research.js (D-V3 strict — no direct import).
            if (_backrefs.isLRInProgress()) {
                console.warn('[UserStateSync] LR active — keeping currentLRSessionId across reset (lr_session_id=' + _backrefs.getLRSessionId() + ')');
            } else {
                _backrefs.clearLRSessionId();
            }

            clearAnalyticsQueryId();
            clearCurrentLoadedSessionId();
            clearCurrentConversationId();
            clearSessionDirty();  // _sessionDirty owned by session-manager.js (D-V14)
            clearFolders();
            clearSourceFolders();
            clearFileFolders();
            clearSelectedFileIds();
            clearCurrentMode();
            clearSharedSessions();
        } catch (e) {
            console.error('[UserStateSync] in-memory state clear failed:', e);
        }

        // D. sessionManager internals — inline (P0 invariant #4: clearOnUserSwitch
        // method does NOT exist; mirror source lines 1898-1905 inline calls).
        try {
            const mgr = getSessionManager();
            if (mgr) {
                if (typeof mgr._cancelPendingSave === 'function') {
                    mgr._cancelPendingSave();
                }
                if (mgr._postedRecently && typeof mgr._postedRecently.clear === 'function') {
                    mgr._postedRecently.clear();
                }
            }
        } catch (e) {
            console.error('[UserStateSync] sessionManager clear failed:', e);
        }
    }

    /**
     * Reset main UI to blank state. Does NOT clear AuthManager._user
     * (caller decides — login flow keeps it, logout flow clears separately).
     *
     * P0 invariant #6: window.resetConversation() / window.updateAuthUI() —
     * explicit window prefix (module scope does NOT fall through to global;
     * per Gemini Final Review Finding 2). Both functions still live in
     * news-search.js (attached to window via Z prep commit 0a window-attach sweep).
     */
    function resetMainUI() {
        try {
            if (typeof window.resetConversation === 'function') window.resetConversation();
        } catch (e) { console.error('[UserStateSync] resetConversation failed:', e); }
        try {
            renderLeftSidebarSessions();  // imported direct from sessions-list.js
        } catch (e) { console.error('[UserStateSync] renderLeftSidebarSessions failed:', e); }
    }

    /**
     * GET /api/user/init — single round-trip fetch.
     * Throws on network error / non-OK / success=false.
     */
    async function fetchInit() {
        const res = await authManager.authenticatedFetch('/api/user/init', { method: 'GET' });
        if (!res.ok) {
            throw new Error(`/api/user/init returned ${res.status}`);
        }
        const body = await res.json();
        if (!body.success) {
            throw new Error(`/api/user/init success=false: ${body.error || 'unknown'}`);
        }
        return body;
    }

    /**
     * Apply init payload into state owners + render UI.
     * MUST be called after clearUserScopedState (caller's responsibility).
     * Faithful port of news-search.js:1938-2073.
     */
    function applyInit(payload) {
        if (!payload || !payload.user) {
            throw new UserStateSyncError('applyInit: payload.user missing', 'MISSING_FRESH');
        }

        // LR Bug 3 guard mirror (P0 invariant #1 — applyInit branch):
        // preserve authManager._user during LR active. Background runInitSync
        // (401-refresh / SSE mismatch / visibilitychange) mid-LR would overwrite
        // authManager._user with payload.user → next LR continue POST sends
        // different user_id → backend _load_state misses LR row → R5 narration bug.
        // Symmetric to dac83ce's currentLRSessionId guard.
        try {
            if (_backrefs.isLRInProgress()) {
                const cachedUid = authManager._user && authManager._user.id;
                const incomingUid = payload.user && payload.user.id;
                console.warn('[UserStateSync] LR active — keeping authManager._user (user_id=' + cachedUid + ', incoming user_id=' + incomingUid + ')');
                // Do NOT mutate authManager._user; do NOT rewrite localStorage.authUser.
            } else {
                hydrateAuthUser(payload.user);  // sets authManager._user + localStorage.authUser
            }
        } catch (e) {
            console.error('[UserStateSync] authUser persist failed:', e);
        }

        // Hydrate user-scoped state. hydrateSavedSessions internally:
        //   - backfills _serverId for each session
        //   - pushes into _savedSessions array
        //   - localStorage.setItem('taiwanNewsSavedSessions', ...)
        //   - localStorage.setItem('taiwanNewsSessionsMigrated', Date.now()) — P0 invariant #2
        // hydrateSharedSessions internally updates 組織空間 tab badge.
        hydrateSavedSessions(payload.sessions || []);
        hydrateSharedSessions(payload.shared_sessions || []);

        // Render
        try {
            if (typeof window.updateAuthUI === 'function') window.updateAuthUI();
            renderLeftSidebarSessions();  // imported direct from sessions-list.js
        } catch (e) {
            console.error('[UserStateSync] render after applyInit failed:', e);
        }

        // Dispatch event for any other component (P0 invariant #5: document
        // dispatch with { user } detail shape — NOT window dispatch + NOT { userId }).
        try {
            document.dispatchEvent(new CustomEvent('user-state-synced', { detail: { user: payload.user } }));
        } catch (e) { /* non-fatal */ }
    }

    function fullReset(options) {
        clearUserScopedState(options);
        resetMainUI();
    }

    /**
     * Convenience: full reset + init fetch + apply.
     * De-duplicates concurrent calls via in-flight Promise.
     */
    async function runInitSync(options) {
        if (_inflight) return _inflight;
        _inflight = (async () => {
            try {
                fullReset(options);
                const payload = await fetchInit();
                applyInit(payload);
                return payload;
            } finally {
                _inflight = null;
            }
        })();
        return _inflight;
    }

    return { clearUserScopedState, resetMainUI, fetchInit, applyInit, fullReset, runInitSync };
})();
