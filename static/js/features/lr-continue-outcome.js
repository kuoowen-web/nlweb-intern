// static/js/features/lr-continue-outcome.js
//
// Pure-function module — NO imports, NO DOM, NO fetch, NO side effects.
// Importable by `node --test` without a DOM (mirrors lr-resume-classify.js /
// lr-reconnect-auth.js pattern).
//
// plan: lr-reconnect-continue-takeover (2026-08-20)
//
// Why this exists — 斷線後「繼續研究」連按三次都失敗的兩條路徑，都出在「前端把不同的
// 失敗全部揉成同一種顯示」：
//
//   1. POST /api/live_research/continue 回 429（斷線後舊背景 task 仍佔住 per-user
//      並行 slot）→ 舊碼走 `!response.ok` → throw → catch 顯示「連線出了狀況…請再送
//      一次繼續」。文案叫使用者立刻重送，但伺服器要的是「等」——於是重送重送再重送，
//      三次全掛。正解：429 是**可自癒的忙碌**，前端該自己退避重試並誠實說明在等什麼。
//
//   2. 醒來重連（read-only GET state + 重繪）在「已經有 SSE 串流在跑」時仍會觸發，
//      restoreLRCheckpointFromState 會 resetLiveResearchUI() 並用 DB 裡的舊 state 重繪
//      → 剛按下去正在跑的續跑被洗掉、畫面退回中斷點，看起來就是「繼續沒生效」。
//      正解：串流在跑時不重連（live 串流才是權威來源）。
//
// 這兩個判斷抽成純函式，才有東西可以被機械測試鎖住（純改 UI 分支沒有防線）。

/** 429 自動退避重試的上限次數（超過就交還給使用者，不再無聲重試）。 */
export const LR_CONTINUE_MAX_AUTO_RETRIES = 2;

/** 每次自動重試前的等待毫秒數（index = 已重試次數）。有界、遞增、不吃伺服器給的 30s
 *  （30s 對「按了繼續在等畫面」的使用者太久，且 slot 通常在舊 task 收尾當下就釋放）。*/
export const LR_CONTINUE_RETRY_DELAYS_MS = [3000, 8000];

// 使用者可見文案集中在此（沿 lr_copy / lrReconnectAuthCopy 紀律）。
export const lrContinueCopy = {
    // 忙碌自動重試中 —— 非 error 語氣：研究沒死，是上一段連線還在收尾。
    busyRetrying:
        '<em>先前中斷的那段研究還在伺服器上收尾，正在自動幫你接回（不需要重按）…</em>',
    // 自動重試用盡 —— 誠實說明現況與下一步，不再叫使用者盲目重送。
    busyGaveUp:
        '<em>先前中斷的那段研究仍在收尾，暫時無法接回。進度已保存，請稍候片刻再按一次「繼續」' +
        '（若持續如此，重新整理頁面後從已保存的進度繼續）。</em>',
};

/**
 * 判斷 POST /continue 的 HTTP 結果該怎麼處理。
 *
 * @param {object} p
 * @param {number} p.status          HTTP status code
 * @param {number} [p.attempt]       已經自動重試過幾次（0 = 第一次送出）
 * @param {number} [p.maxAutoRetries]
 * @returns {{action:'stream'|'auth_expired'|'auto_retry'|'busy_giveup'|'error',
 *            delayMs?: number}}
 */
export function classifyContinueHttpOutcome(p) {
    const status = p && p.status;
    const attempt = (p && p.attempt) || 0;
    const maxAutoRetries = (p && p.maxAutoRetries != null)
        ? p.maxAutoRetries : LR_CONTINUE_MAX_AUTO_RETRIES;

    if (status >= 200 && status < 300) return { action: 'stream' };
    // 401：authenticatedFetch 的 refresh-then-retry 已經失敗過了 → 登入問題，不是忙碌。
    if (status === 401) return { action: 'auth_expired' };
    // 429：並行 slot 被上一段（斷線後仍在收尾的）研究佔住 —— 可自癒，退避重試。
    if (status === 429) {
        if (attempt < maxAutoRetries) {
            const delayMs = LR_CONTINUE_RETRY_DELAYS_MS[
                Math.min(attempt, LR_CONTINUE_RETRY_DELAYS_MS.length - 1)
            ];
            return { action: 'auto_retry', delayMs };
        }
        return { action: 'busy_giveup' };
    }
    return { action: 'error' };
}

/**
 * 醒來重連（read-only 拉 state 重繪）是否該執行。
 *
 * INVARIANT：`streamInflight` 為真時一律不重連 —— 正在跑的 SSE 串流是最新事實，
 * 用 DB 舊 state 重繪會把 live 進度洗掉（restoreLRCheckpointFromState 內含
 * resetLiveResearchUI）。這正是「按了繼續卻退回中斷點」的機制。
 *
 * @param {{connectionLost:boolean, streamInflight:boolean, hasSession:boolean}} p
 * @returns {boolean}
 */
export function shouldRunWakeReconnect(p) {
    if (!p) return false;
    if (!p.connectionLost) return false;    // 沒真的斷過就不要動畫面
    if (p.streamInflight) return false;     // live 串流優先，不可被舊 state 覆蓋
    if (!p.hasSession) return false;        // 無 session 無從 restore
    return true;
}
