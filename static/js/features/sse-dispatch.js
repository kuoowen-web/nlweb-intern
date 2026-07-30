// Pure SSE parse + classify. NO browser globals, NO imports with side effects,
// NO module-eval side effects — so `node --test` can import it directly
// (see dr-report-server-persistence-plan.md R9/R10). Consumers (search.js,
// deep-research.js, ...) import this AND do their own DOM/render; this module
// only decides "known / skip / unknown".
import { SKIP_TYPES } from './sse-types.js';

// KNOWN = has a render/handle path in at least one consumer.
// Consistency with registry.py FRONTEND_KNOWN_TYPES is enforced at test-time
// (test_frontend_known_matches_registry, Task 8 Step 5). Kept as
// `export const ... = new Set([...])` so the pytest regex can read its members.
export const KNOWN_TYPES = new Set([
  'begin-nlweb-response', 'remember', 'intermediate_result',
  'clarification_required', 'time_filter_relaxed', 'low_relevance_warning',
  'low_keyword_match_warning', 'author_search_no_results', 'empty_results',
  'complete', 'articles', 'summary', 'answer', 'nlws', 'injection_blocked',
  'final_result', 'research_error', 'research_interrupted',
  'deep_research_session_created', 'live_research_session_created',
  'live_research_narration', 'live_research_stage_change',
  'live_research_checkpoint', 'live_research_section',
  'live_research_writer_status', 'live_research_export',
]);

/** @param {string} line @returns {import('./sse-types.js').SseEnvelope | null} */
export function parseSseLine(line) {
  if (!line || !line.startsWith('data: ')) return null;
  try { return JSON.parse(line.slice(6)); }
  catch (e) { return null; }
}

/** @param {{message_type?: string}} data
 *  @returns {{kind: 'known'|'skip'|'unknown', type: string}} */
export function classifyEnvelope(data) {
  const t = (data && data.message_type) || '';
  if (KNOWN_TYPES.has(t)) return { kind: 'known', type: t };
  if (SKIP_TYPES.has(t)) return { kind: 'skip', type: t };
  return { kind: 'unknown', type: t };
}
