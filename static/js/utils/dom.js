// static/js/utils/dom.js
//
// D-1 Module Header — State Ownership Contract
//   Owned state:    none (pure utility module)
//   Trigger writes: not applicable
//   External read:  named exports below
//
// D-13 compliance: This module is INERT on import.
//   All side effects only execute inside exported initializer called by main.js bootstrap.
//   (This module has no initializer — it only exports pure helpers.)
//
// Phase 1 (2026-05-21):
//   Extracted from static/news-search.js line ~1491 (`function matchSessionId`).
//   Dual-source coexistence strategy: original function in news-search.js remains
//   active (Phase 1 does NOT delete legacy active code). Phase 8 sweep will remove
//   the duplicate after all callers have been migrated to import from this module.

/**
 * Compare two session IDs for equality, coercing both to string.
 * Handles mixed number/string IDs (Date.now() local vs UUID server vs DOM dataset).
 *
 * @param {string|number|null|undefined} a
 * @param {string|number|null|undefined} b
 * @returns {boolean}
 */
export function matchSessionId(a, b) {
    if (a == null || b == null) return false;
    return String(a) === String(b);
}
