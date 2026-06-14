// static/js/main.js
//
// D-1 Module Header — Bootstrap Entrypoint
//   Owned state:    none (entrypoint module — orchestrates other modules' init)
//   Trigger writes: not applicable (delegates to module initializers)
//   External read:  no exports (this is the entry; nothing imports it)
//
// D-3 Cross-Module Communication:
//   This module uses ONLY static imports. No `await import(...)` / no dynamic import
//   / no top-level await. Circular dependency (if any future module needs it) will be
//   resolved via injectStateSync({...}) sync inject pattern in this file.
//
// D-4 Load Order:
//   HTML loads main.js with <script type="module" src="/static/js/main.js?v=...">.
//   This module controls all internal ES-module import ordering. External classic
//   <script src> (D3.js v7 / feedback-utils.js / clarity-init.js / news-search.js /
//   analytics-tracker-sse.js / phase-gate-probe.js) remain HTML-loaded.
//
// D-13 Entrypoint Exemption:
//   main.js is the bootstrap entrypoint and is EXEMPT from D-13 "inert on import"
//   rule. Its purpose is to execute bootstrap side effects (imports, bridge setup,
//   window.addEventListener('DOMContentLoaded', ...) etc.). But it MUST still abide
//   by these constraints:
//     - Only execute bootstrap-related side effects, NOT feature-level logic.
//     - Do NOT call runInitSync / applyInit / clearUserScopedState at module top-level
//       (these MUST be inside DOMContentLoaded handler or later trigger A-G).
//
// Phase 1 (2026-05-21):
//   This is the initial skeleton. Phase 2+ progressively imports core modules
//   (auth, session, state-sync) and wires them into the bootstrap sequence.
//
//   Sanity-log import is intentional — proves the ES-module load chain reaches
//   utils/dom.js end-to-end (D-12 entrypoint reachability proof). Phase 2+ removes
//   the sanity log once real module wiring takes over.

import { matchSessionId } from './utils/dom.js';

// Phase 3 Path B imports (2026-05-21)
import { authManager, injectStateSync, AuthManager } from './core/auth-manager.js';
import { UserStateSync, UserStateSyncError, assertUserIdentity } from './core/state-sync.js';
import { bootstrapPage } from './core/page-bootstrap.js';

// Phase 4a Path B imports (2026-05-21)
import { renderLeftSidebarSessions, renderSharedSessions, initSessionsList } from './features/sessions-list.js';

// Phase 4b Path B imports (2026-05-21)
import { initSessionManager } from './features/session-manager.js';

// v4.0 Commit 1 imports (2026-05-24) — state migration: currentMode owner module.
// main.js does not USE these directly; the import here is to trigger module init
// in case browser dependency-order matters. news-search.js imports from features/mode.js
// directly for read/write helpers since it is now a type="module" script (post commit 0c).
import './features/mode.js';

// v4.0 Commit 2 imports (2026-05-24) — search trio owner module.
import './features/search.js';

// v4.0 Commit 3 imports (2026-05-24) — chat owner module.
import './features/chat.js';

// v4.0 Commit 4 imports (2026-05-24) — pins owner module.
import './features/pins.js';

// v4.0 Commit 5 imports (2026-05-24) — research trio owner module.
import './features/research.js';

// v4.0 Commit 6 imports (2026-05-24) — sharing owner module.
import './features/sharing.js';

// v4.0 Commit 7 imports (2026-05-24) — live-research owner module + D-V3 inject wire.
// Note: live-research.js has ONE permitted top-level side effect — calls
// injectStateSyncBackref({...}) at module init to register backref bundle into
// state-sync.js. state-sync.js MUST be imported (above) before live-research.js so
// its module-local _backrefs binding is initialized when the inject call fires.
import './features/live-research.js?v=20260611a';

// v4.0 Commit 8 imports (2026-05-24) — folders owner module (folders quad + folder mode flag).
import './features/folders.js';

// v4.0 Commit 19 imports (2026-05-25, Phase 8 part C) — file-kb owner module.
import './features/file-kb.js';

// v4.0 Commit 9 imports (2026-05-24) — analytics extension (currentAnalyticsQueryId owned by utils/analytics.js).
import './utils/analytics.js';

// D-3 sync injection — resolves circular dep state-sync ↔ auth-manager.
// Path B variant: UserStateSync 仍是 news-search.js classic-script IIFE
// (透過 window.UserStateSync). state-sync.js export is a thin alias forwarding
// (verified in news-search.js: window.UserStateSync = UserStateSync attach added
// at end of IIFE). Phase 7+ moves UserStateSync IIFE into state-sync.js.
injectStateSync({ UserStateSync, UserStateSyncError, assertUserIdentity });

// Bridge for not-yet-migrated callers in news-search.js (Phase 8 sweep removes).
// authManager has moved out of news-search.js — the legacy `const authManager = new AuthManager()`
// declaration is now commented out. ~50 callsites in news-search.js reference
// bare `authManager`, which classic-script scope chain resolves via window. So
// main.js must attach the exported singleton to window before news-search.js's
// DOMContentLoaded handler runs (which references authManager indirectly).
window.authManager = authManager;
// Phase 3 Path B (2026-05-21): UserStateSync IIFE in news-search.js still references
// the class itself for AuthManager.USER_SCOPED_KEYS static access (clearUserScopedState).
// Class moved to ES module; expose to window so the legacy IIFE callsite resolves.
// Phase 7+ moves UserStateSync IIFE into state-sync.js and this bridge can drop.
window.AuthManager = AuthManager;

// Phase 4b Path B (2026-05-21): SessionManager class moved to features/session-manager.js.
// Phase 3 fix's window._initSessionManager bridge superseded by initSessionManager export.
// Call at module top-level (after authManager bridge, before any DOMContentLoaded handler)
// so news-search.js's parse-time `if (window.sessionManager && ...)` references and
// DOMContentLoaded handlers see a valid window.sessionManager.
initSessionManager();
// UserStateSync / UserStateSyncError / assertUserIdentity are already attached to
// window by news-search.js itself (parse-time IIFE / class / function declarations
// followed by explicit window.X = X). main.js does NOT re-attach them.

// Phase 4a Path B (2026-05-21): bridge sessions-list renders to window so legacy
// callsites in news-search.js (renderLeftSidebarSessions() at lines ~1260, 1369,
// 1862, 1962, 8882, 9170, 10851, 10859, 10876, 10889, 10899, 11561; renderSharedSessions
// — none active in news-search.js after this phase) resolve via classic-script
// scope-chain lookup. Phase 4b+ migrates these callsites to the owner module.
window.renderLeftSidebarSessions = renderLeftSidebarSessions;
window.renderSharedSessions = renderSharedSessions;

// Phase 1 sanity log — proves module entry executed and utils/dom.js import resolved.
console.log('[main.js] module entry loaded; matchSessionId =', typeof matchSessionId);
console.log('[main.js] Phase 3 wired: authManager + UserStateSync (alias) + bootstrapPage');
console.log('[main.js] Phase 4a wired: features/sessions-list (renderLeft/SharedSessions, initSessionsList)');
console.log('[main.js] Phase 4b wired: features/session-manager (SessionManager class + initSessionManager)');

// D-11 Programmatic Probe — record module init count for phase-gate sentinel 4.
// Probe is gated by ?phaseProbe=1 query param or localStorage __nlweb_dev_mode flag,
// so production users see zero overhead (probe object is absent).
if (typeof window !== 'undefined' && window.__nlwebProbe) {
    window.__nlwebProbe.recordInit('main');
}

// Phase 3 Path B: bootstrap auth identity guard + Trigger F warm-start listener
// inside DOMContentLoaded so news-search.js's DOMContentLoaded handler (which still
// runs after main.js's because both `defer` to the same event) sees a stable
// authManager._user / window.UserStateSync state.
window.addEventListener('DOMContentLoaded', () => {
    bootstrapPage();  // internally awaits checkAuthOnLoad + registers visibilitychange

    // Phase 4a Path B: features/sessions-list init runs AFTER bootstrapPage so that
    // window.authManager / window.sessionManager (set by initSessionManager() at
    // module top-level — Phase 4b superseded Phase 3's _initSessionManager bridge) /
    // window.savedSessions (set by news-search.js classic parse) are all available
    // when initSessionsList() registers tab handlers + does initial render.
    initSessionsList();

    if (window.__nlwebProbe) {
        ['core/auth-manager', 'core/state-sync', 'core/page-bootstrap',
         'features/sessions-list', 'features/session-manager'].forEach(m =>
            window.__nlwebProbe.recordInit(m)
        );
    }
});
