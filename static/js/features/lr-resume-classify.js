// static/js/features/lr-resume-classify.js
//
// Pure-function module — NO imports, NO DOM, NO side effects.
// Can be imported by Node.js test runners without a DOM environment.
//
// Classifies how a persisted LR session should resume, based ONLY on the
// backend's real stage_status vocabulary (pending / in_progress / checkpoint
// / completed) and current_stage (0 = not started, 6 = export stage).
//
// NOTE: The backend NEVER emits 'complete' or 'exported' as stage_status
// (see stage_state.py:133 + advance_to_stage/set_checkpoint/complete_stage).
// Completion is detected via current_stage>=6 + status 'completed', NOT magic
// strings.
//
// COUPLING NOTE: stage >= 6 is the "completed" threshold because Stage 6 is
// the export stage and complete_stage() on Stage 6 marks the full pipeline done.
// If a Stage 7+ is ever added or the export flow changes, this threshold must
// be revisited here.

/**
 * @param {number} stage   - lrState.current_stage (0-6)
 * @param {string} status  - lrState.stage_status
 * @param {boolean} [offlineCapped] - lrState.offline_capped (新欄位；舊 row 為 undefined→false)
 * @returns {'completed'|'not_started'|'offline_capped'|'checkpoint'|'in_progress'}
 *
 * plan: lr-sse-reconnect-resume (2026-06-15) — 加 'checkpoint' / 'offline_capped' 類別，
 * 支援 wake-reconnect 三狀態分流（仍在跑 / 已到 checkpoint / 被離線保護停）。
 * backward compat：offlineCapped 參數 optional；舊 caller 不傳 → undefined → 不進 capped
 * 分支；舊 state row 無 offline_capped → undefined → 不誤判 capped。
 */
export function classifyLRResumeState(stage, status, offlineCapped) {
    // Fully ran through Stage 6 and marked completed = export finished.
    if (stage >= 6 && status === 'completed') return 'completed';
    // current_stage 0 = backend "未開始" sentinel.
    if (stage === 0) return 'not_started';
    // 離線防呆上限被停 — 早於 checkpoint 判斷（capped state 可能停在非 checkpoint 點）。
    if (offlineCapped === true) return 'offline_capped';
    // 已停在 checkpoint 等使用者回答。
    if (status === 'checkpoint') return 'checkpoint';
    // Stages 1-5 in_progress / pending = 仍在跑。
    return 'in_progress';
}
