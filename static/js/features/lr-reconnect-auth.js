// static/js/features/lr-reconnect-auth.js
//
// Pure helpers for LR wake-reconnect auth handling. NO DOM, NO fetch, NO side
// effects, NO import of live-research.js. Imported (bare specifier) by
// live-research.js; unit-tested standalone via node --test (mirrors
// lr-snapshot.js / lr-resume-classify.js pattern).
//
// Why this exists: _doLRReconnect must NOT route the session GET through
// authManager.authenticatedFetch, because on a dead refresh token that path
// fires _handleAuthFailure → login modal, burying the "研究仍在背景進行中"
// disconnect bubble. This module isolates the retry-vs-degrade decision so the
// reconnect path can degrade gently (keep the bubble + show a relogin hint)
// without a screen-blocking modal and without touching global auth behavior.

// Centralized copy (mirrors lr_copy discipline: user-facing strings in one place).
export const lrReconnectAuthCopy = {
    // Non-terminal, reassuring. NOT an "error". Tells the user the research is
    // safe in the background and they can pick it back up after logging in.
    reloginNeeded:
        '<em>登入逾期，研究仍安全保存在背景。請重新登入後即可接回先前的研究進度。</em>',
};

/**
 * Decide what _doLRReconnect should do, from the HTTP outcome of the session GET
 * (and optional refresh + retry). Pure: numbers in, plain object out.
 *
 * @param {object} p
 * @param {number} p.initialStatus  status of the first GET /api/sessions/{sid}
 * @param {boolean} p.refreshAttempted  whether a bare /api/auth/refresh was tried
 * @param {boolean} [p.refreshOk]   whether that refresh returned ok
 * @param {number|null} [p.retryStatus]  status of the retried GET (null if no retry)
 * @returns {{outcome:'ok'|'auth_dead'|'transient',
 *            keepConnectionLost:boolean, showRelogin:boolean}}
 */
export function classifyReconnectFetchOutcome(p) {
    const initial = p.initialStatus;
    // Success on first try.
    if (initial >= 200 && initial < 300) {
        return { outcome: 'ok', keepConnectionLost: false, showRelogin: false };
    }
    // 401 path: we attempted (or should treat as) a refresh + retry.
    if (initial === 401) {
        if (p.refreshAttempted && p.refreshOk) {
            const retry = p.retryStatus;
            if (retry != null && retry >= 200 && retry < 300) {
                return { outcome: 'ok', keepConnectionLost: false, showRelogin: false };
            }
            // refresh succeeded but retry still unauthorized → auth genuinely dead.
            return { outcome: 'auth_dead', keepConnectionLost: true, showRelogin: true };
        }
        // refresh not attempted, or refresh failed → auth dead (refresh token expired/revoked).
        return { outcome: 'auth_dead', keepConnectionLost: true, showRelogin: true };
    }
    // Any other non-2xx (network 0, 500, 503, etc.): transient. Stay disconnected,
    // retry on the next online/visibilitychange wake. No relogin hint (not an auth problem).
    return { outcome: 'transient', keepConnectionLost: true, showRelogin: false };
}
