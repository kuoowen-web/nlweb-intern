// static/js/features/mode.js
//
// D-1 Module Header — Mode Owner
//   Owned state:    _currentMode ('search' | 'deep_research' | 'chat' | 'live_research')
//   Trigger writes: mode-button click handlers, resetConversation, loadSavedSession
//   External read:  via getCurrentMode() — search / DR / LR / chat code paths read mode
//                   to branch behavior; saveCurrentSession serializes mode into session.
//
// D-3 Cross-Module Communication:
//   Static imports only. No circular deps — this module is pure leaf.
//
// D-13 Compliance:
//   No top-level side effects on import (no IIFE, no DOM, no fetch, no window writes).
//   Only declarations + function definitions + exports.
//
// v4.0 Commit 1 (2026-05-24): NEW — extracted from news-search.js outer let `currentMode`
//   (declaration was line 1610; 16 callsites + 1 decl line = 17 raw matches per
//   Phase 0 §2.4). Replaces classic-script `let currentMode` boundary.

let _currentMode = 'search';

export function getCurrentMode() {
    return _currentMode;
}

export function setCurrentMode(mode) {
    const old = _currentMode;
    _currentMode = mode;
    if (old !== mode) {
        window.dispatchEvent(new CustomEvent('mode-change', { detail: { old, new: mode } }));
    }
}

export function clearCurrentMode() {
    setCurrentMode('search');
}

export function onModeChange(handler) {
    window.addEventListener('mode-change', handler);
}
