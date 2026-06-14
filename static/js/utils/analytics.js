// static/js/utils/analytics.js
//
// D-1 Module Header — State Ownership Contract
//   Owned state:    currentSessionId (tab-scoped via sessionStorage key 'nlweb_session_id')
//   Trigger writes: tab-scoped — exempt from 7 user-scoped triggers (D-2026-05-13)
//   External read:  named exports below
//
// D-13 compliance: This module is INERT on import.
//   getCurrentSessionId() is lazy — only generates/reads on first call.
//   No top-level DOM/storage/network side effects.
//
// Phase 1 (2026-05-21):
//   Extracted from static/news-search.js line ~1510-1554
//   (analyticsTracker instantiation + currentSessionId sessionStorage init).
//
//   Dual-source coexistence strategy:
//     - Original `const analyticsTracker = new AnalyticsTrackerSSE(...)` at line 1510
//       remains in news-search.js (Phase 1 does NOT delete legacy active code).
//     - Original currentSessionId block at line 1542-1554 remains active in news-search.js.
//     - This module exposes a NEW lazy helper getCurrentSessionId() that reads the
//       same sessionStorage key. Phase 8 sweep removes the duplicates.
//
//   AnalyticsTrackerSSE class itself comes from static/analytics-tracker-sse.js
//   (loaded as classic <script> in HTML — IIFE/global, not an ES module).

const SESSION_ID_KEY = 'nlweb_session_id';

/**
 * Get (or lazily generate) the tab-scoped session ID for analytics / A/B testing.
 * Persists until the browser tab closes (sessionStorage scope).
 *
 * Tab-scoped, NOT user-scoped — survives user logout/login within same tab.
 * This is intentional: tracks the analytical "session" (window of usage), not the auth session.
 *
 * @returns {string} session id like 'sess_xxxxxxxxxxxx'
 */
export function getCurrentSessionId() {
    let id = sessionStorage.getItem(SESSION_ID_KEY);
    if (id) return id;

    const uuid = (typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, c =>
            (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
    id = 'sess_' + uuid.replace(/-/g, '').substring(0, 12);
    sessionStorage.setItem(SESSION_ID_KEY, id);
    return id;
}

/**
 * Get the global analyticsTracker instance, if AnalyticsTrackerSSE class is loaded.
 * Returns null if the class hasn't loaded yet (e.g., script order issue) — caller
 * must handle null and not silently swallow analytics events.
 *
 * Phase 1: returns the existing global `analyticsTracker` from news-search.js if present
 * (it's a `const` declared in global script scope at line ~1510). Phase 4+ may
 * relocate the singleton into this module.
 *
 * @returns {object|null}
 */
export function getAnalyticsTracker() {
    // The legacy news-search.js declares `const analyticsTracker` in global scope.
    // `const` declarations are NOT attached to `window`, so we cannot reach it from
    // a module via window.analyticsTracker. Phase 1 callers should continue to use
    // the legacy in-script reference. This export is a placeholder; Phase 4+ will
    // own the singleton in this module.
    // eslint-disable-next-line no-undef
    return (typeof window !== 'undefined' && window.analyticsTracker) || null;
}

// ============================================================================
// v4.0 Commit 9 (2026-05-24): currentAnalyticsQueryId migration
//
// Owned state: _analyticsQueryId (string|null)
//
// Source: news-search.js line 1676 `let currentAnalyticsQueryId = null`.
//
// Trigger writes:
//   - Search SSE handlers set query_id when backend emits it (3 sites — search /
//     deep-research / final-buffer fallback in news-search.js).
//   - UserStateSync.clearUserScopedState IIFE clears on user switch (line 1918).
//
// External read:
//   - Click + MutationObserver tracking in news-search.js (truthy guards before
//     analyticsTracker.trackClick / trackResultDisplayed calls).
//
// D-13: No top-level side effects. Module-private `let _analyticsQueryId` lives in
//   module scope; readers/writers go through helpers below.
// ============================================================================

let _analyticsQueryId = null;

/**
 * Get the current analytics query id (string|null). Truthy when an SSE search /
 * deep-research stream has emitted backend query_id; null when idle or post-clear.
 *
 * @returns {string|null}
 */
export function getAnalyticsQueryId() {
    return _analyticsQueryId;
}

/**
 * Set the analytics query id. Called by SSE handlers when backend emits query_id.
 *
 * @param {string|null} id
 */
export function setAnalyticsQueryId(id) {
    _analyticsQueryId = id;
}

/**
 * Clear the analytics query id (set to null). Called by UserStateSync on user switch.
 */
export function clearAnalyticsQueryId() {
    _analyticsQueryId = null;
}
