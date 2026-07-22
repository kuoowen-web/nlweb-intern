/**
 * LR dialog snapshot — pure helpers (no DOM globals, no HTTP, no module-load side effects).
 *
 * Extracted into a standalone module so they can be unit-tested directly via
 * `node --test` (the full live-research.js import graph pulls in browser globals
 * like localStorage and cannot load under plain node). live-research.js imports
 * and re-exports these.
 *
 * Entry shape: { type, stage, html, dataset, ts }
 */

/**
 * Serialize a #lrChat-like root element into a snapshot entry array.
 * Pure: reads no globals, writes no DOM, makes no HTTP. Each bubble innerHTML
 * is passed through `purify.sanitize` (DOMPurify boundary #1 — C4).
 *
 * @param {Element|null} root  container element (real #lrChat or a test stub)
 * @param {{sanitize: (h:string)=>string}|null} purify  DOMPurify (or compatible)
 * @returns {Array<{type:string, stage:number, html:string, dataset:object, ts:number}>}
 */
export function serializeLRChatRoot(root, purify) {
    if (!root) return [];
    const sanitize = (purify && purify.sanitize) ? (h) => purify.sanitize(h) : (h) => h;
    const out = [];
    // 只取直接子層、且帶 data-lr-content 的 message wrapper（candidate A 正向白名單）：
    // 只序列化真對話內容（user/narration/section + opt-in checkpoint），所有暫態框（restore/
    // reconnect/relogin/error/echo notice）預設不帶 data-lr-content → 預設被排除。
    // 這樣 _saveLRSnapshot / saveCurrentSession 永不持久化暫態框，terminal session 重開
    // serialize 為 []（empty-guard 保住真 prior snapshot，根治 data loss）。
    root.querySelectorAll(':scope > .lr-chat-message[data-lr-content]').forEach((wrapper) => {
        const bubble = wrapper.querySelector('.lr-msg-bubble');
        // type：從 className 第二段取（"lr-chat-message narration" → "narration"）
        const cls = (wrapper.className || '').split(/\s+/);
        const type = cls[1] || 'system';
        const stageRaw = wrapper.dataset ? wrapper.dataset.lrStage : undefined;
        const parsed = parseInt(stageRaw, 10);
        const stage = Number.isInteger(parsed) ? parsed : 0;
        out.push({
            type,
            stage,
            html: sanitize(bubble ? bubble.innerHTML : ''),   // checkpoint raw HTML 在此被 sanitize（C4）
            dataset: { ...(wrapper.dataset || {}) },           // 保留 data-lr-section-index 等
            ts: Date.now(),
        });
    });
    return out;
}

/**
 * 有對話的 stage 列表（去重升冪）；stage 0（初始提問）併入 stage 1，不單獨成 toggle。
 */
export function lrStagesInSnapshot(snapshot) {
    const set = new Set();
    for (const e of (Array.isArray(snapshot) ? snapshot : [])) {
        const s = Number.isInteger(e.stage) ? e.stage : 0;
        if (s >= 1) set.add(s);
        else set.add(1);                 // stage 0 → 歸 stage 1（Task 3 初始提問跟「建立結構」一起看）
    }
    return [...set].sort((a, b) => a - b);
}

/**
 * 某 stage 的 entry 子集（保序）；stage 1 額外包含 stage 0 的初始提問。
 */
export function lrSnapshotForStage(snapshot, stageNum) {
    const arr = Array.isArray(snapshot) ? snapshot : [];
    if (stageNum === 1) {
        return arr.filter(e => e.stage === 1 || !(Number.isInteger(e.stage) && e.stage >= 1));
    }
    return arr.filter(e => e.stage === stageNum);
}

/**
 * True when there is a snapshot worth replaying as conversation bubbles.
 * Intentionally only checks "is it a non-empty array" — it does NOT require
 * any particular stage tagging. (lrStagesInSnapshot collapses stage-0 entries
 * into stage 1, so stage grouping is a SEPARATE concern; this gate must NOT
 * borrow lrStagesInSnapshot's stage logic, or a snapshot whose bubbles are all
 * stage 0 would be wrongly judged empty.)
 *
 * @param {Array} snapshot  loaded snapshot (entries from serializeLRChatRoot)
 * @returns {boolean}
 */
export function snapshotHasReplayableEntries(snapshot) {
    return Array.isArray(snapshot) && snapshot.length > 0;
}

// ── R8 BLOCKER 2 — restore-canned checkpoint operation strings ──────────────────
// A replayed `checkpoint` bubble can be a REAL AI proposal OR a LEGACY restore-canned
// box (the OLD store-everything serializer captured a canned "從中斷處繼續" resume box
// via the dirty-save path — prod-confirmed in b08080f8). Their wrapper markup is
// IDENTICAL (both drawn by showLRCheckpoint: `lr-checkpoint-label` + `lr-checkpoint-proposal`);
// the ONLY difference is the inner text — a canned box's `.lr-checkpoint-proposal` holds an
// operation prompt, a real box holds AI-generated research options. These substrings are the
// VERBATIM operation prompts from the restore path (verified read of live-research.js):
//   • '從中斷處繼續'      — mid-flight resume notice (resumeNotice)
//   • '要繼續這份研究嗎'  — offline_capped checkpoint
//   • '從暫停處接續'      — offline_capped checkpoint tail
//   • '研究已暫停'        — offline_capped assistant notice (defensive — that one is
//                          type 'assistant' not 'checkpoint', but kept so any future canned
//                          checkpoint reusing this phrasing is also caught)
// They are chosen to be unique enough that a REAL AI proposal never contains them (prod-
// verified: across 17 prod checkpoints, the 16 real proposals match NONE of these substrings;
// the 1 canned box matches '從中斷處繼續'). Use the FULL multi-character substrings, never the
// bare word '繼續' — a real proposal CAN contain '繼續' (e.g. "還是可以進入寫作準備？").
//
// Defined here (pure, no DOM) so the replay round-trip test can import it directly:
// live-research.js cannot load under plain node (it pulls browser globals at import time),
// so _isReplayRealContent + this constant live in this pure module and live-research.js
// imports them for use inside _appendReplayedBubbles.
export const LR_CHECKPOINT_CANNED_STRINGS = ['從中斷處繼續', '要繼續這份研究嗎', '從暫停處接續', '研究已暫停'];

/**
 * Content-aware replay-side real-content judge (R8 BLOCKER 2). Decides whether a
 * REPLAYED bubble should carry data-lr-content, so the type/content rule is the SOLE
 * authoritative source of the marker (paired with the BLOCKER-1 delete of inherited lrContent).
 *
 *   • user / narration / section → ALWAYS real.
 *   • checkpoint → real ONLY when its html does NOT contain a restore-canned operation string.
 *       Restore-canned checkpoint boxes share the SAME markup as real proposals, so type alone
 *       cannot tell them apart on the replay side. A LEGACY snapshot can hold a canned checkpoint
 *       (prod b08080f8 has exactly 1, captured by the old serializer's dirty-save path) — leaving
 *       it unmarked makes it self-heal on the next dirty-save, exactly like a legacy `system` entry.
 *       Real proposals do NOT match these substrings (prod-verified), so they stay marked.
 *   • system / assistant / error → never real (legacy transient garbage → self-heal).
 *
 * NOTE this REPLACES the earlier "replay checkpoint is UNCONDITIONALLY marked" rule. The
 * earlier rule rested on the (now-disproven) premise that no snapshot can contain a canned
 * checkpoint; prod evidence shows the legacy store-everything serializer DID capture one.
 *
 * @param {string} type  serialized entry type
 * @param {string} html  serialized entry html (needed to distinguish canned vs real checkpoint)
 * @returns {boolean}
 */
export function _isReplayRealContent(type, html) {
    if (type === 'user' || type === 'narration' || type === 'section') return true;
    if (type === 'checkpoint') {
        return !LR_CHECKPOINT_CANNED_STRINGS.some((s) => (html || '').includes(s));
    }
    return false;   // system / assistant / error → transient → self-heal
}

/**
 * Decide which snapshot to persist, guarding against an empty serialize
 * clobbering a previously-saved non-empty snapshot.
 *
 * Returns { snapshot, preserved }:
 *   - preserved === true  → caller skipped the overwrite (kept `existing`); caller MUST log (no-silent-fail).
 *   - preserved === false → caller writes `snapshot` normally.
 *
 * Rule (D-4): preserve ONLY when fresh is empty AND existing is non-empty.
 *
 * INVARIANT — do NOT change `=== 0` to `>= existing.length`:
 *   A non-empty-but-SHORTER fresh snapshot is a LEGITIMATE result of
 *   recollect / revise / delete-section (the user genuinely shortened the
 *   dialog; old content is voided) and MUST overwrite. Only an *empty*
 *   serialize (not-LR-mode / transient-empty DOM / cross-session timing) is
 *   the thing to guard against. A length-comparison rule would silently
 *   discard legitimate shortenings. (Confirmed by 3-way AR, 2026-06-19.)
 *
 * @param {Array} fresh     freshly-serialized snapshot ([] when not-LR-mode or empty DOM)
 * @param {Array|null|undefined} existing  snapshot currently stored on the session entry
 * @returns {{snapshot: Array, preserved: boolean}}
 */
export function resolveLRSnapshotForSave(fresh, existing) {
    const freshArr = Array.isArray(fresh) ? fresh : [];
    const existingArr = Array.isArray(existing) ? existing : [];
    if (freshArr.length === 0 && existingArr.length > 0) {
        return { snapshot: existingArr, preserved: true };  // empty-clobber guard ONLY
    }
    return { snapshot: freshArr, preserved: false };
}

/**
 * D-7 (REDESIGNED): decide whether a snapshot save should proceed.
 * Compares the id of the STREAM that triggered the save (captured at stream
 * start, stable for the stream's life) against the currently-loaded session.
 * A stale background stream carries its OWN old id → after a session switch it
 * mismatches the now-loaded session → skip. This is NOT the mutable module
 * global (which the v2 version wrongly used and would FALSE-PASS).
 *
 * Skip only when BOTH ids are truthy AND they mismatch. (Null triggeringLRSid —
 * e.g. mock or pre-adopt — does not skip; the empty-overwrite guard + loaded-id
 * checks downstream still protect correctness.)
 *
 * @param {string|null} triggeringLRSid  captured stream LR session id
 * @param {string|number|null} loadedId  getCurrentLoadedSessionId()
 * @param {(a,b)=>boolean} matchSessionId  id-equality fn (window.matchSessionId)
 * @returns {boolean} true → proceed with save; false → skip
 */
export function shouldSaveLRSnapshot(triggeringLRSid, loadedId, matchSessionId) {
    if (triggeringLRSid && loadedId && !matchSessionId(triggeringLRSid, loadedId)) {
        return false;  // stale stream from a different session
    }
    return true;
}

/**
 * Strip 繼承的 data-lr-content marker 再複製 dataset（replay / 回顧重建共用）。
 * R8 BLOCKER 1 紀律：重建 wrapper 整包複製 dataset 會帶進繼承 marker，
 * 需 strip 後由判定重打（replay 路徑）或不打（回顧唯讀路徑）。
 * Pure function（無 DOM、無副作用）——node --test 可直測。
 */
export function stripLRContentDataset(persistedDataset) {
    const copy = { ...(persistedDataset || {}) };
    delete copy.lrContent;
    return copy;
}
