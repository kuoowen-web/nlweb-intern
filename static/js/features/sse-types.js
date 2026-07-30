// SSE typed union (JSDoc). Pure data module — no imports, no browser globals,
// no module-eval side effects. Consumed by sse-dispatch.js and checked by
// `tsc --checkJs` (tsconfig.checkjs.json). Mirrors code/python/core/sse/models.py.
// message_type is an OPEN SET (server may add types) — see frontend-spec.md §7.3.
//
// Envelope-common wire fields (injected by add_message_metadata / inject_user_id,
// see core/sse/models.py SseEnvelope) — every modelled member may carry these:
//   user_id, timestamp, conversation_id, message_id, sender_info, senderInfo
// They are declared optional on each @typedef via the shared shape below.

/**
 * Wire-metadata fields common to every SSE envelope (see models.py SseEnvelope).
 * Modelled optional so `tsc --checkJs` does not flag their presence.
 * @typedef {Object} SseEnvelopeMeta
 * @property {string} [user_id]
 * @property {number} [timestamp]
 * @property {string} [conversation_id]
 * @property {string} [message_id]
 * @property {Object<string, unknown>} [sender_info]
 * @property {Object<string, unknown>} [senderInfo]
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'begin-nlweb-response',
 *   query?: string,
 *   query_id?: string,
 *   is_rerun?: boolean,
 *   original_query_id?: string,
 * }} BeginNlwebResponse
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'end-nlweb-response',
 *   error?: boolean,
 * }} EndNlwebResponse
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'progress',
 *   stage?: string,
 *   message?: string,
 *   percent?: number,
 * }} Progress
 */

/**
 * @typedef {SseEnvelopeMeta & { message_type: 'complete' }} Complete
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'error',
 *   error?: unknown,
 *   message?: string,
 *   status?: unknown,
 * }} ErrorEnvelope
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'result',
 *   content?: unknown,
 * }} Result
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'intermediate_result',
 *   stage: string,
 *   user_message?: string,
 *   progress?: number,
 * }} IntermediateResult
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'research_phase',
 *   phase?: string,
 *   status?: string,
 * }} ResearchPhase
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'articles',
 *   content?: unknown,
 * }} Articles
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'answer',
 *   answer?: string,
 *   items?: unknown,
 * }} Answer
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'nlws',
 *   answer?: string,
 *   items?: unknown,
 *   '@type'?: string,
 * }} Nlws
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'summary',
 *   content?: string,
 *   '@type'?: string,
 * }} Summary
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'remember',
 *   item_to_remember?: string,
 * }} Remember
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'clarification_required',
 *   clarification?: unknown,
 *   query?: string,
 * }} ClarificationRequired
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'time_filter_relaxed',
 *   content?: string,
 * }} TimeFilterRelaxed
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'low_relevance_warning',
 *   content?: string,
 * }} LowRelevanceWarning
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'low_keyword_match_warning',
 *   content?: string,
 * }} LowKeywordMatchWarning
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'author_search_no_results',
 *   content?: string,
 * }} AuthorSearchNoResults
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'empty_results',
 *   content?: string,
 * }} EmptyResults
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'injection_blocked',
 *   message?: string,
 * }} InjectionBlocked
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'asking_sites',
 *   content?: string,
 * }} AskingSites
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'final_result',
 *   final_report?: unknown,
 *   confidence_level?: unknown,
 *   methodology?: unknown,
 *   sources?: unknown,
 *   argument_graph?: unknown,
 *   reasoning_chain_analysis?: unknown,
 *   knowledge_graph?: unknown,
 *   verification_status?: unknown,
 *   verification_message?: string,
 *   dr_session_id?: string,
 * }} FinalResult
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'deep_research_session_created',
 *   session_id?: string,
 * }} DeepResearchSessionCreated
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'research_error',
 *   error?: unknown,
 * }} ResearchError
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'research_interrupted',
 *   message?: string,
 * }} ResearchInterrupted
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_session_created',
 *   session_id?: string,
 * }} LiveResearchSessionCreated
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_narration',
 *   text?: string,
 * }} LiveResearchNarration
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_stage_change',
 *   stage?: unknown,
 * }} LiveResearchStageChange
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_checkpoint',
 *   stage?: unknown,
 *   proposal?: unknown,
 *   context_map_summary?: unknown,
 *   auto_continue_option?: unknown,
 *   evidence_list?: unknown,
 *   evidence_total?: number,
 *   show_new_sample_button?: boolean,
 * }} LiveResearchCheckpoint
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_section',
 *   section_index?: number,
 *   title?: string,
 *   content?: unknown,
 *   sources?: unknown,
 *   citation_sources?: unknown,
 *   citation_format?: unknown,
 *   methodology_note?: unknown,
 * }} LiveResearchSection
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_writer_status',
 *   status?: string,
 *   total_sections?: number,
 *   completed?: number,
 *   section_title?: string,
 * }} LiveResearchWriterStatus
 */

/**
 * @typedef {SseEnvelopeMeta & {
 *   message_type: 'live_research_export',
 *   content?: unknown,
 *   format?: unknown,
 *   citation_sources?: unknown,
 *   citation_format?: unknown,
 *   knowledge_graph?: unknown,
 * }} LiveResearchExport
 */

/**
 * Open-set envelope: any object carrying a message_type. Used as the union's
 * escape hatch WITHOUT weakening the modelled members to Object.
 * @typedef {{message_type?: string, [key: string]: unknown}} UnknownSseEnvelope
 */

/**
 * Discriminated union of all known SSE envelopes.
 * 🔧 AR R1 N1：不用 `| Object` 收尾（那會把整個 union 放寬成 Object、型別檢查失效）。
 * 用完整具名 union + UnknownSseEnvelope 表達開放集，型別檢查仍對已知成員有牙。
 * @typedef {(
 *   BeginNlwebResponse | EndNlwebResponse | Progress | Complete | ErrorEnvelope
 *   | Result | IntermediateResult | ResearchPhase | Articles | Answer | Nlws
 *   | Summary | Remember | ClarificationRequired | TimeFilterRelaxed
 *   | LowRelevanceWarning | LowKeywordMatchWarning | AuthorSearchNoResults
 *   | EmptyResults | InjectionBlocked | AskingSites | FinalResult
 *   | DeepResearchSessionCreated | ResearchError | ResearchInterrupted
 *   | LiveResearchSessionCreated | LiveResearchNarration | LiveResearchStageChange
 *   | LiveResearchCheckpoint | LiveResearchSection | LiveResearchWriterStatus
 *   | LiveResearchExport
 *   | UnknownSseEnvelope
 * )} SseEnvelope
 */

// 🔧 AR R1 B1：分類集合是「前端渲染維度」，與 server _BAD_MESSAGE_TYPES（持久化維度）
// 語意正交，不是 mirror（見 §0.3）。此處只放「前端消費時不 render 的純中間噪音」，
// 不含 remember/time_filter_relaxed/clarification_required（那些是 KNOWN、要 render）。
// 一致性由 registry.py 的 FRONTEND_SKIP_RENDER_TYPES 於 test-time 跨讀對帳（Task 8）。
/** @type {ReadonlySet<string>} SKIP = FRONTEND_SKIP_RENDER_TYPES（渲染維度，非持久化黑名單） */
export const SKIP_TYPES = new Set([
  'asking_sites', 'tool_selection', 'decontextualization',
  'pre_check_results', 'site_querying', 'research_phase',
  'progress', 'end-nlweb-response', 'error',
]);
