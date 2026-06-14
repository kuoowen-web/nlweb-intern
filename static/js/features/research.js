// static/js/features/research.js
//
// D-1 Module Header — Research Owner (state only — commit 5)
//   Owned state:
//     - _currentResearchReport (object|null — DR report {report, sources, query, ...})
//     - _currentArgumentGraph (array|null — reasoning graph nodes)
//     - _currentChainAnalysis (object|null — chain analysis metadata)
//
//   Trigger writes:
//     - performDeepResearch SSE handlers (final report assembly)
//     - performLiveResearch end stage
//     - loadSavedSession restore (from session.researchReport / sessionHistory entry)
//     - resetConversation / deleteSavedSession clear all 3
//     - resetLiveResearchUI clear all 3
//     - UserStateSync.clearUserScopedState (IIFE) clears on logout
//     - KG edit rerun pre-clears chainAnalysis + argumentGraph (re-sets after)
//
//   External read:
//     - DR render hot path (~22 reads of currentResearchReport in renderResearchReport)
//     - free-chat reference context guard
//     - share/export generators (3 formats)
//     - saveCurrentSession serializes via getX()
//
// D-3 Cross-Module Communication:
//   Static imports only. Pure leaf.
//
// D-13 Compliance:
//   No top-level side effects.
//
// v4.0 Commit 5 (2026-05-24): State-only migration. Bridge removed: window.currentResearchReport
//   getter (was news-search.js:1805-1808). features/sessions-list.js previously read via this
//   bridge — now uses `import { getResearchReport } from features/research.js` directly.
//   Function bodies (renderResearchReport / displayReasoningChain / performDeepResearch)
//   stay in news-search.js until Phase 8 sweep (commits 12 / 17 per plan §3.0).
//
// Plan §3.5 note on memory semantics: setX stores REFERENCE (does NOT clone) per "Common
//   pitfalls" — caller owns clone if needed. Helpers do not deep-copy on set; callers
//   that need ownership separation must pass {...obj} themselves.

// ============================================================================
// currentResearchReport — DR/LR final report object
// ============================================================================
let _currentResearchReport = null;

export function getResearchReport() {
    return _currentResearchReport;
}

export function setResearchReport(r) {
    _currentResearchReport = r;
}

export function clearResearchReport() {
    _currentResearchReport = null;
}

// ============================================================================
// currentArgumentGraph — reasoning graph nodes
// ============================================================================
let _currentArgumentGraph = null;

export function getArgumentGraph() {
    return _currentArgumentGraph;
}

export function setArgumentGraph(g) {
    _currentArgumentGraph = g;
}

export function clearArgumentGraph() {
    _currentArgumentGraph = null;
}

// ============================================================================
// currentChainAnalysis — chain analysis metadata
// ============================================================================
let _currentChainAnalysis = null;

export function getChainAnalysis() {
    return _currentChainAnalysis;
}

export function setChainAnalysis(c) {
    _currentChainAnalysis = c;
}

export function clearChainAnalysis() {
    _currentChainAnalysis = null;
}
