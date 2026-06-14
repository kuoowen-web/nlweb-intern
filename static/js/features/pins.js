// static/js/features/pins.js
//
// D-1 Module Header — Pins Owner (state only — commit 4)
//   Owned state:
//     - _pinnedMessages (array of {msgId, content, role, timestamp} — Line-style chat pin banner)
//     - _pinnedNewsCards (array of {url, title, ...} — pinned news cards)
//
//   Trigger writes:
//     - togglePinMessage / togglePinNewsCard handlers (splice / shift / push)
//     - resetConversation / loadSavedSession / deleteSavedSession reset both
//     - UserStateSync.clearUserScopedState (IIFE) clears both on logout
//
//   External read:
//     - pin banner render (toggle row + counter badge)
//     - news card render (isPinned guards)
//     - free-conversation POST body (pinned_articles field)
//     - saveCurrentSession serializes via getX()
//
// D-3 Cross-Module Communication:
//   Static imports only. Pure leaf.
//
// D-13 Compliance:
//   No top-level side effects.
//
// v4.0 Commit 4 (2026-05-24): State-only migration. Pin lifecycle functions
//   (togglePinMessage / togglePinNewsCard / pin banner render) stay in news-search.js;
//   they use the accessor pattern getX() to mutate (push/shift/splice/findIndex)
//   on the live reference. Phase 8 sweep moves the lifecycle functions to a feature
//   module if scoped (currently no commit assigned in plan §3.0 — pin lifecycle is
//   small surface, may remain UI handler in news-search.js until commit 19 final cleanup).

// ============================================================================
// pinnedMessages — Line-style chat pin banner
// ============================================================================
let _pinnedMessages = [];

export function getPinnedMessages() {
    return _pinnedMessages;
}

export function setPinnedMessages(arr) {
    _pinnedMessages = Array.isArray(arr) ? arr : [];
}

export function clearPinnedMessages() {
    // Preserve array reference semantics.
    _pinnedMessages.length = 0;
}

// ============================================================================
// pinnedNewsCards — pinned news article cards
// ============================================================================
let _pinnedNewsCards = [];

export function getPinnedNewsCards() {
    return _pinnedNewsCards;
}

export function setPinnedNewsCards(arr) {
    _pinnedNewsCards = Array.isArray(arr) ? arr : [];
}

export function clearPinnedNewsCards() {
    _pinnedNewsCards.length = 0;
}
