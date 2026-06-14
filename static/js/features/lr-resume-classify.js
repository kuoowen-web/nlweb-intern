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
 * @param {number} stage  - lrState.current_stage (0-6)
 * @param {string} status - lrState.stage_status
 * @returns {'completed'|'not_started'|'in_progress'}
 */
export function classifyLRResumeState(stage, status) {
    // Fully ran through Stage 6 and marked completed = export finished.
    if (stage >= 6 && status === 'completed') return 'completed';
    // current_stage 0 = backend "未開始" sentinel.
    if (stage === 0) return 'not_started';
    // Stages 1-5 (any status) or Stage 6 not-yet-completed = resume mid-flow.
    return 'in_progress';
}
