"""SSE event 分類 single source of truth（多維度）。

🔧 AR R1 B1：這些集合語意正交，不可互相 assert 相等：
- 持久化維度（不進 sessionHistory）≠ 前端渲染維度（不 render）≠ typed union 成員。
一個 event 可以「該 render 但不進 history」（如 remember）——強行對齊會靜默丟 live event。

一致性防護（見 test_sse_registry_consistency.py / test_sse_models.py）：
- SERVER_HISTORY_SANITIZE_TYPES == session_service._BAD_MESSAGE_TYPES（維度 1 契約）
- FRONTEND_SKIP_RENDER_TYPES == sse-types.js SKIP_TYPES（維度 2 契約，test-time 跨讀）
- FRONTEND_KNOWN_TYPES == sse-dispatch.js KNOWN_TYPES（維度 3 契約，test-time 跨讀）
- LIVE_MODEL_TYPES == core.sse.models._REGISTRY keys（維度 4 契約，牙 2）
禁止斷言 SERVER_HISTORY_SANITIZE_TYPES == FRONTEND_SKIP_RENDER_TYPES（兩維度正交）。
"""

# 維度 1：後端持久化清洗黑名單（不進 sessionHistory）。session_service._BAD_MESSAGE_TYPES 由此衍生。
# 🔧 照 session_service.py:600-609 現況逐條抄（不增不減，搬家非改語義）。
# 🔧R4（R3-SF-1）：含 tool_routing——刻意保留在此 sanitize 集（防禦性歷史清洗，
# 可清舊 sessionHistory 誤存的 tool_routing envelope + 防回歸污染）。與 Task 2「清
# tool_routing live 路徑（emit/render）」不矛盾：兩維度正交（正是拆多維度 registry 的理由）。
SERVER_HISTORY_SANITIZE_TYPES = frozenset({
    "asking_sites", "tool_selection", "decontextualization",
    "pre_check_results", "site_querying", "tool_routing",
    "research_phase", "intermediate_result", "progress",
    "begin-nlweb-response", "end-nlweb-response", "complete",
    "error", "remember", "time_filter_relaxed",
    "author_search_no_results", "clarification_required",
    "low_relevance_warning", "low_keyword_match_warning",
    "empty_results",
})

# 維度 2：前端消費時「不 render」的純中間噪音（渲染維度）。前端 SKIP_TYPES 與此對齊。
# ⚠️ 遠小於維度 1——remember/time_filter_relaxed/clarification_required 不在此（它們要 render）。
# 與 static/js/features/sse-types.js 的 SKIP_TYPES 逐字對齊（test-time 跨讀對帳）。
FRONTEND_SKIP_RENDER_TYPES = frozenset({
    "asking_sites", "tool_selection", "decontextualization",
    "pre_check_results", "site_querying", "research_phase",
    "progress", "end-nlweb-response", "error",
})

# 維度 3：前端有明確 render/handle 路徑的型別（含維度 1∩render 的反例三個）。
# 與 static/js/features/sse-dispatch.js 的 KNOWN_TYPES 逐字對齊（test-time 跨讀對帳）。
FRONTEND_KNOWN_TYPES = frozenset({
    "begin-nlweb-response", "remember", "intermediate_result",
    "clarification_required", "time_filter_relaxed", "low_relevance_warning",
    "low_keyword_match_warning", "author_search_no_results", "empty_results",
    "complete", "articles", "summary", "answer", "nlws", "injection_blocked",
    "final_result", "research_error", "research_interrupted",
    "deep_research_session_created", "live_research_session_created",
    "live_research_narration", "live_research_stage_change",
    "live_research_checkpoint", "live_research_section",
    "live_research_writer_status", "live_research_export",
})

# 維度 4：進 typed union（有 Pydantic model）的 live event（§0.6 清單）。
# == core.sse.models._REGISTRY keys（牙 2 對帳；此處手列，test 斷言雙向相等）。
LIVE_MODEL_TYPES = frozenset({
    "begin-nlweb-response", "end-nlweb-response", "progress", "complete",
    "error", "result", "intermediate_result", "research_phase", "articles",
    "answer", "nlws", "summary", "remember", "clarification_required",
    "time_filter_relaxed", "low_relevance_warning", "low_keyword_match_warning",
    "author_search_no_results", "empty_results", "injection_blocked",
    "asking_sites", "final_result", "deep_research_session_created",
    "research_error", "research_interrupted", "live_research_session_created",
    "live_research_narration", "live_research_stage_change",
    "live_research_checkpoint", "live_research_section",
    "live_research_writer_status", "live_research_export",
})
