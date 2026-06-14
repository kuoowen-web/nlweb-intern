// static/js/core/auth-manager.js
//
// D-1 Module Header — Auth Manager
//   Owned state:
//     - authManager._user                  (user-scoped, source of truth for JWT identity)
//     - authManager._accessToken           (user-scoped)
//     - authManager._refreshPromise        (in-flight de-dupe)
//     - AuthManager.USER_SCOPED_KEYS       (static array — single source of truth for keys)
//
//   Triggers writing this state (7-trigger A-G):
//     - A (Login): authManager.login() → window.UserStateSync.runInitSync()
//     - C (Logout): authManager.logout() → _handleAuthFailure() → window.UserStateSync.fullReset()
//     - D (401/refresh fail): authenticatedFetch 401 → refreshToken catch → _handleAuthFailure()
//     - B/F: state-sync.js / page-bootstrap.js call applyInit / fullReset which mutates
//       authManager._user via existing window.UserStateSync IIFE (Path B sequencing).
//
//   External read interface:
//     - export { authManager, injectStateSync }
//     - authManager.isLoggedIn() / getCurrentUser() / getAccessToken() / authenticatedFetch()
//
// D-3 Circular Dep Resolution (Path B 變體):
//   This module exports `injectStateSync(stateSyncModule)`. main.js bootstrap calls it
//   synchronously after evaluating both auth-manager.js and state-sync.js. AuthManager
//   methods then access UserStateSync APIs via lazy getter `getUserStateSync()` /
//   `getUserStateSyncError()` (returns _stateSyncRef.X if injected, else undefined).
//
//   Path B note: UserStateSync 來源仍是 news-search.js classic script IIFE
//   (透過 window.UserStateSync). state-sync.js export is a thin alias forwarding.
//   Phase 7+ 才會把 UserStateSync IIFE 真實搬到 state-sync.js. See state-sync.js header.
//
// D-13 Compliance:
//   This module is INERT on import.
//   - AuthManager constructor is verified inert here:
//     - reads from localStorage (`getItem` only, no `setItem`)
//     - removes pollution entries (`removeItem` on malformed JSON)
//     - sets in-memory fields
//     - NO DOM register, NO fetch, NO setItem.
//
//     The `removeItem` calls in _init() are recovery-from-corruption, not fresh state
//     writes — they only execute IF localStorage already contained malformed data
//     before this module loaded. They are inert on a clean storage state and bounded
//     to dirty-state cleanup. Verifier EXEMPT_CONSTRUCTIONS allowlist permits the
//     `export const authManager = new AuthManager()` singleton construction.
//
//   All other side effects (login fetch, refresh fetch, storage write inside methods)
//   only execute when caller invokes methods, never at import time.
//
// Phase 3 Path B (2026-05-21):
//   Extracted from static/news-search.js line 43-282. Original class body comment out
//   in news-search.js. main.js sets window.authManager = authManager bridge so existing
//   callsites in news-search.js (~50 sites) continue to resolve `authManager` via the
//   classic-script global lookup.

// D-3 sync injection (resolves circular dep state-sync ↔ auth-manager)
// main.js calls injectStateSync({ UserStateSync, UserStateSyncError, assertUserIdentity })
// during bootstrap. AuthManager methods invoke via lazy getter so module-load order is
// flexible (auth-manager.js can evaluate before state-sync.js without errors).
let _stateSyncRef = null;

export function injectStateSync(stateSyncModule) {
    _stateSyncRef = stateSyncModule;
}

function getUserStateSync() {
    return _stateSyncRef && _stateSyncRef.UserStateSync;
}

// (UserStateSyncError currently unused inside AuthManager; the helper exists for
// symmetry with state-sync.js export shape and for future migration phases.)
// function getUserStateSyncError() {
//     return _stateSyncRef && _stateSyncRef.UserStateSyncError;
// }

export class AuthManager {
    // List of localStorage keys that are user-scoped and MUST be cleared
    // when a different user logs in (origin-scoped storage means cross-user
    // leakage if not cleared). Device-scoped UI prefs (nlweb-large-font,
    // nlweb-kg-hidden) are intentionally excluded.
    static USER_SCOPED_KEYS = [
        'taiwanNewsSavedSessions',
        'taiwanNewsFolders',
        'taiwanNewsSessionsMigrated',
        'nlweb_source_folders',
        'nlweb_file_folders',
        'nlweb_selected_files',
    ];

    constructor() {
        this._accessToken = null;
        this._user = null;
        this._refreshPromise = null;
        this._init();
    }

    _init() {
        // Try to load user from localStorage
        const stored = localStorage.getItem('authUser');
        if (stored) {
            try {
                this._user = JSON.parse(stored);
            } catch (e) {
                localStorage.removeItem('authUser');
            }
        }
        const storedToken = localStorage.getItem('authAccessToken');
        if (storedToken && storedToken !== 'undefined') {
            this._accessToken = storedToken;
        } else {
            localStorage.removeItem('authAccessToken');
        }
    }

    // Task 13 cleanup: _clearUserScopedStorageIfUserChanged removed.
    // Superseded by UserStateSync.runInitSync, which calls fullReset
    // (= clearUserScopedState + resetMainUI) before applyInit. The
    // helper became a double-clear once Task 5 routed login() through
    // runInitSync. UserStateSync.clearUserScopedState already iterates
    // AuthManager.USER_SCOPED_KEYS plus authUser / authAccessToken.

    isLoggedIn() {
        // BP-1: access_token is in httpOnly cookie (not in JS), so only check _user
        return !!this._user;
    }

    getCurrentUser() {
        return this._user;
    }

    getAccessToken() {
        return this._accessToken;
    }

    async login(email, password) {
        const UserStateSync = getUserStateSync();
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
            credentials: 'same-origin'
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Login failed');
        // Trigger A (login). clear+fetch+apply goes through
        // UserStateSync.runInitSync (called below). The legacy
        // _clearUserScopedStorageIfUserChanged helper has been removed
        // in Task 13 — fullReset inside runInitSync covers the same
        // intent (cross-user clear) without the double-clear.
        // BP-1: access_token is in httpOnly cookie, not in response body
        this._accessToken = data.access_token || null;
        this._user = data.user;
        if (this._accessToken) {
            localStorage.setItem('authAccessToken', this._accessToken);
        } else {
            localStorage.removeItem('authAccessToken');
        }
        // Trigger A: full reset + GET /api/user/init + apply.
        // localStorage.authUser write happens inside applyInit;
        // do NOT duplicate the write here. keepInviteToken=true so a
        // pending invite (sessionStorage) survives login.
        try {
            await UserStateSync.runInitSync({ keepInviteToken: true });
        } catch (e) {
            console.error('[login] runInitSync failed; falling back to legacy authUser persist:', e);
            // Fallback: still persist authUser so subsequent reload can recover.
            try { localStorage.setItem('authUser', JSON.stringify(this._user)); } catch (_) {}
        }
        return data;
    }

    async register(email, password, name) {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, name })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Registration failed');
        return data;
    }

    async refreshToken() {
        // Deduplicate concurrent refresh calls
        if (this._refreshPromise) return this._refreshPromise;
        this._refreshPromise = (async () => {
            try {
                const res = await fetch('/api/auth/refresh', {
                    method: 'POST',
                    credentials: 'same-origin'
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Refresh failed');
                // BP-1: access_token may be in httpOnly cookie, not in response body.
                // Fix (2026-05-27): when body has no access_token (cookie-only refresh path),
                // clear the stale in-memory token so that the subsequent authenticatedFetch
                // retry does NOT attach an expired Bearer header. middleware auth.py treats
                // Bearer as authoritative over cookie, so sending a stale Bearer would cause
                // the retry to receive another 401 even though the refresh cookie is valid.
                // Clearing _accessToken makes authenticatedFetch line 224 skip the
                // Authorization header, letting middleware fall back to the fresh cookie.
                // Side-effect check: no EventSource in this codebase passes auth_token as
                // query param — all SSEs use public URLs or user_id only — so clearing
                // _accessToken here has no SSE breakage risk.
                if (data.access_token) {
                    this._accessToken = data.access_token;
                    localStorage.setItem('authAccessToken', this._accessToken);
                } else {
                    // Cookie-only path: nuke stale token to prevent retry poisoning.
                    this._accessToken = null;
                    localStorage.removeItem('authAccessToken');
                    console.log('[refreshToken] cookie-only refresh; cleared stale _accessToken so retry uses cookie');
                }
                return data;
            } catch (e) {
                this._handleAuthFailure();
                throw e;
            } finally {
                this._refreshPromise = null;
            }
        })();
        return this._refreshPromise;
    }

    async logout() {
        try {
            await fetch('/api/auth/logout', {
                method: 'POST',
                credentials: 'same-origin'
            });
        } catch (e) { /* ignore network error; still clear locally */ }
        // Trigger C: full clear + UI reset + show login modal.
        // Delegated to _handleAuthFailure (single fullReset call),
        // which handles cancelPendingSave → clear → UI reset → modal.
        this._handleAuthFailure();
    }

    async authenticatedFetch(url, options = {}) {
        const UserStateSync = getUserStateSync();
        if (!options.headers) options.headers = {};
        if (this._accessToken) {
            options.headers['Authorization'] = `Bearer ${this._accessToken}`;
        }
        options.credentials = 'same-origin';

        let res = await fetch(url, options);

        // If 401, try refresh once (BP-1: always try, cookie may have expired)
        if (res.status === 401) {
            try {
                const cachedUserId = this._user?.id || null;
                await this.refreshToken();

                // Task 12 (D-4): distinguish same-user token rotation vs
                // user identity change. Decode the new access_token's JWT
                // payload (no signature verify — backend already validated)
                // and compare payload.user_id against the cached _user.id.
                // Same → silent refresh, no UI flash. Different → user
                // identity change (e.g. cookies swapped, multi-account
                // browser session) → trigger A via runInitSync.
                if (this._accessToken && cachedUserId) {
                    try {
                        const parts = this._accessToken.split('.');
                        if (parts.length === 3) {
                            // base64url decode (atob handles standard base64;
                            // JWT uses base64url so normalise +/- and pad).
                            const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
                            const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
                            const payload = JSON.parse(atob(b64 + pad));
                            const newUid = payload.user_id || payload.sub || null;
                            if (newUid && newUid !== cachedUserId) {
                                console.warn(`[authenticatedFetch] refresh changed user identity: cached=${cachedUserId} → new=${newUid}; triggering runInitSync`);
                                // Fire-and-forget so the original request can still proceed;
                                // the init-sync will re-render sidebar/UI to the new user.
                                UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                                    console.error('[authenticatedFetch:refresh-identity-change] runInitSync failed:', err));
                            }
                            // Same user (or no newUid) → silent rotation, no-op.
                        }
                    } catch (decodeErr) {
                        console.warn('[authenticatedFetch] JWT payload decode failed; skipping identity check:', decodeErr);
                    }
                }

                // After refresh: only attach Bearer if we actually have a token.
                // When refreshToken() used cookie-only path, _accessToken is null —
                // intentionally omit the header so middleware uses the fresh cookie.
                if (this._accessToken) {
                    options.headers['Authorization'] = `Bearer ${this._accessToken}`;
                } else {
                    delete options.headers['Authorization'];
                }
                res = await fetch(url, options);
            } catch (e) {
                // CEO P0 UX fix (2026-05-19): refresh fail 必須 trigger
                // _handleAuthFailure（顯示 login modal + reset state），不可靜默
                // return 401 — 否則 caller 看 raw "HTTP 401" 顯示給 user。
                // 對齊 spec §5.3 token expire mid-LR 紀律：refresh 失敗 → 跳「請重新登入」。
                console.error('[authenticatedFetch] refresh failed; triggering _handleAuthFailure:', e);
                this._handleAuthFailure();
            }
        }
        return res;
    }

    async forgotPassword(email) {
        const res = await fetch('/api/auth/forgot-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Request failed');
        return data;
    }

    _handleAuthFailure() {
        const UserStateSync = getUserStateSync();
        // Trigger C (logout) + Trigger D (401 / refresh fail). Both share
        // the same effect: full clear + UI reset + show login modal.
        // UserStateSync.fullReset handles cancelPendingSave → localStorage
        // / sessionStorage / in-memory globals / sessionManager internals
        // → resetMainUI in one call (single source-of-truth for clearing).
        // Call sequence: cancel timer (inside fullReset) → clear state →
        // null AuthManager fields → reset UI → show modal.
        try {
            UserStateSync.fullReset({ keepInviteToken: false });
        } catch (e) {
            console.error('[_handleAuthFailure] UserStateSync.fullReset error:', e);
        }
        this._accessToken = null;
        this._user = null;
        // Legacy global UI functions still live in news-search.js (Phase 2b not yet
        // executed). classic-script function declarations are auto-attached to window,
        // so plain `updateAuthUI` lookup resolves via global scope from within an ES
        // module too (window members are visible as bare references in module code).
        if (typeof window.updateAuthUI === 'function') window.updateAuthUI();
        // Auth guard: hide main UI and show login modal
        if (typeof window.hideMainUI === 'function') window.hideMainUI();
        if (typeof window.showAuthModal === 'function') window.showAuthModal('login');
    }
}

// D-13 EXEMPT_CONSTRUCTIONS allowlist permits this single-line stateless construction.
// The constructor only reads localStorage and assigns in-memory fields; no fetch / DOM
// register / external side effects. See verifier comment in tools/frontend_ownership_check.py.
export const authManager = new AuthManager();

// v4.0 Commit 11 (2026-05-24): hydrateAuthUser — set authManager._user + persist to
// localStorage. Mirror of IIFE applyInit lines 1970-1971 (news-search.js prior to relocate).
// Called by state-sync.js UserStateSync.applyInit when LR Bug 3 guard does NOT fire
// (i.e., normal applyInit path — replaces inflight user identity).
//
// Per plan §3.11 "Helper function locations": this helper extracted to auth-manager.js
// because authManager._user is owned here; the IIFE itself shouldn't directly mutate
// authManager._user (encapsulation).
export function hydrateAuthUser(user) {
    if (!user) return;
    authManager._user = user;
    try {
        localStorage.setItem('authUser', JSON.stringify(user));
    } catch (e) {
        console.error('[hydrateAuthUser] localStorage.authUser persist failed:', e);
    }
}
