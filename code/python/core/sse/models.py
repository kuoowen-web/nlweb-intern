"""Pydantic v2 SSE envelope models.

Design rule (see plan §0.1): each model's ``model_dump(by_alias=True,
exclude_none=True)`` MUST reproduce the *current* wire dict byte-shape — this
is verified STATICALLY by the Task 5 fixture round-trip tests (model ↔ wire
consistency), NOT invoked at runtime. 🔧R4 (R3-BLK-B): runtime ``_typed_validate``
is **validate-only** and returns the original payload unchanged (it does NOT
model_dump), so ``send_sse()`` with the typed flag ON is byte-identical to flag
OFF regardless of explicit-null keys — the model_dump round-trip lives only in
the fixture tests. Hyphenated ``message_type`` values (e.g.
"begin-nlweb-response") are literal strings, NOT enum members.
"""
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class SseEnvelope(BaseModel):
    """Common base for every browser-facing SSE envelope.

    ``model_config`` extra='allow' during migration: keeps model_validate
    from rejecting a real payload that carries a field a specific model
    hasn't yet enumerated — critical for the 鐵律 (flag OFF byte-equivalence).
    Tightened to 'forbid' per-model only after that model's emit site is
    migrated.

    🔧 AR R1 B3/B4: the wire-metadata fields injected by add_message_metadata
    (message_senders.py:287-328) are enumerated HERE (not left to extra='allow'
    to swallow), so path=full models model *the wire truth* and B4's
    ``type(...) is not SseEnvelope`` coverage really has teeth.

    🔧R3 (R2-SF2) senderInfo alias 完整化：add_message_metadata 依 use_system_sender
    寫**兩個不同 key**——``sender_info`` (snake，nlweb_assistant 預設) 或 ``senderInfo``
    (camelCase，system sender)（message_senders.py:313-320 親驗）。舊版只有一個
    ``Field(alias="sender_info")`` + populate_by_name，**不會**把 wire 的 ``senderInfo``
    映進來（populate_by_name 只讓「欄位名本身」可填，不含 camel alias）→ system sender 的
    ``senderInfo`` 仍靠 extra=allow 吞（違 B3/B4）。修法：**建模兩個獨立 optional 欄位**，
    各對一個 wire key，by_alias round-trip 對兩種 wire 都逐字對得上（單欄 + AliasChoices
    會 dump 成單一 alias，破 senderInfo 那半的 byte 等價，故用兩欄）。
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Envelope-level identity (Trigger G). Optional: anonymous traffic omits it.
    user_id: Optional[str] = None
    timestamp: Optional[int] = None
    conversation_id: Optional[str] = None
    # 🔧 B3: wire-metadata injected by add_message_metadata (path=full events).
    # Modelled (not swallowed by extra=allow) so .wire.json round-trips exactly.
    message_id: Optional[str] = None
    # 🔧R3 SF2: two separate keys — nlweb_assistant path uses snake sender_info,
    # system-sender path uses camel senderInfo. Model both for byte-exact round-trip.
    sender_info: Optional[dict[str, Any]] = Field(default=None, alias="sender_info")
    sender_info_system: Optional[dict[str, Any]] = Field(default=None, alias="senderInfo")


class BeginNlwebResponse(SseEnvelope):
    message_type: Literal["begin-nlweb-response"] = "begin-nlweb-response"
    query: Optional[str] = None
    query_id: Optional[str] = None
    # DR rerun variant carries these instead of query_id (api.py:1072):
    is_rerun: Optional[bool] = None
    original_query_id: Optional[str] = None


class EndNlwebResponse(SseEnvelope):
    message_type: Literal["end-nlweb-response"] = "end-nlweb-response"
    error: Optional[bool] = None


class Progress(SseEnvelope):
    message_type: Literal["progress"] = "progress"
    stage: Optional[str] = None
    message: Optional[str] = None
    percent: Optional[int] = None


class Complete(SseEnvelope):
    message_type: Literal["complete"] = "complete"


class ErrorEnvelope(SseEnvelope):
    message_type: Literal["error"] = "error"
    error: Optional[Any] = None
    message: Optional[str] = None
    status: Optional[Any] = None


class Result(SseEnvelope):
    # generate_mode != unified: ranked news cards accumulated on the client.
    message_type: Literal["result"] = "result"
    content: Optional[Any] = None


class IntermediateResult(SseEnvelope):
    # reasoning layer (orchestrator + critic) via _send_progress -> send_message.
    # NEVER carries `text` (that LR frontend branch is a dead read — see plan §Task 9).
    message_type: Literal["intermediate_result"] = "intermediate_result"
    stage: str
    user_message: Optional[str] = None
    progress: Optional[int] = None


class ResearchPhase(SseEnvelope):
    # DR: phase in {filter_and_prepare, actor_critic_loop, writer, format_result, rerun}
    # LR: phase in bab_phase0-4. Same type, different phase vocab.
    message_type: Literal["research_phase"] = "research_phase"
    phase: Optional[str] = None
    status: Optional[str] = None


class Articles(SseEnvelope):
    # unified generate_mode: news cards (content is a list, possibly JSON string).
    message_type: Literal["articles"] = "articles"
    content: Optional[Any] = None


class Answer(SseEnvelope):
    # unified generate_mode.
    message_type: Literal["answer"] = "answer"
    answer: Optional[str] = None
    items: Optional[Any] = None


class Nlws(SseEnvelope):
    # non-unified generate_mode; @type: GeneratedAnswer.
    message_type: Literal["nlws"] = "nlws"
    answer: Optional[str] = None
    items: Optional[Any] = None
    type_: Optional[str] = Field(default=None, alias="@type")


class Summary(SseEnvelope):
    # unified generate_mode; @type: Summary.
    message_type: Literal["summary"] = "summary"
    content: Optional[str] = None
    type_: Optional[str] = Field(default=None, alias="@type")


class Remember(SseEnvelope):
    message_type: Literal["remember"] = "remember"
    item_to_remember: Optional[str] = None


class ClarificationRequired(SseEnvelope):
    message_type: Literal["clarification_required"] = "clarification_required"
    clarification: Optional[Any] = None
    query: Optional[str] = None


# ── warning family: all use `content` (plan §Task 5 content-vs-message) ──
class TimeFilterRelaxed(SseEnvelope):
    message_type: Literal["time_filter_relaxed"] = "time_filter_relaxed"
    content: Optional[str] = None


class LowRelevanceWarning(SseEnvelope):
    message_type: Literal["low_relevance_warning"] = "low_relevance_warning"
    content: Optional[str] = None


class LowKeywordMatchWarning(SseEnvelope):
    message_type: Literal["low_keyword_match_warning"] = "low_keyword_match_warning"
    content: Optional[str] = None


class AuthorSearchNoResults(SseEnvelope):
    message_type: Literal["author_search_no_results"] = "author_search_no_results"
    content: Optional[str] = None


class EmptyResults(SseEnvelope):
    message_type: Literal["empty_results"] = "empty_results"
    content: Optional[str] = None


class InjectionBlocked(SseEnvelope):
    # uses `message` (not `content`) — plan §Task 5 keeps current field name.
    message_type: Literal["injection_blocked"] = "injection_blocked"
    message: Optional[str] = None


class AskingSites(SseEnvelope):
    # live-emit but frontend intentionally ignores (progress noise).
    message_type: Literal["asking_sites"] = "asking_sites"
    content: Optional[str] = None


class FinalResult(SseEnvelope):
    # DR final report — the optional-heaviest envelope.
    message_type: Literal["final_result"] = "final_result"
    final_report: Optional[Any] = None
    confidence_level: Optional[Any] = None
    methodology: Optional[Any] = None
    sources: Optional[Any] = None
    argument_graph: Optional[Any] = None
    reasoning_chain_analysis: Optional[Any] = None
    knowledge_graph: Optional[Any] = None
    verification_status: Optional[Any] = None
    verification_message: Optional[str] = None
    dr_session_id: Optional[str] = None


class DeepResearchSessionCreated(SseEnvelope):
    message_type: Literal["deep_research_session_created"] = "deep_research_session_created"
    session_id: Optional[str] = None


class ResearchError(SseEnvelope):
    message_type: Literal["research_error"] = "research_error"
    error: Optional[Any] = None


class ResearchInterrupted(SseEnvelope):
    message_type: Literal["research_interrupted"] = "research_interrupted"
    message: Optional[str] = None


# ── Live Research (LR) events ──
class LiveResearchSessionCreated(SseEnvelope):
    message_type: Literal["live_research_session_created"] = "live_research_session_created"
    session_id: Optional[str] = None


class LiveResearchNarration(SseEnvelope):
    message_type: Literal["live_research_narration"] = "live_research_narration"
    text: Optional[str] = None


class LiveResearchStageChange(SseEnvelope):
    message_type: Literal["live_research_stage_change"] = "live_research_stage_change"
    stage: Optional[Any] = None


class LiveResearchCheckpoint(SseEnvelope):
    # model uses REAL shape; mock is a degraded subset -> mock-missing fields Optional.
    message_type: Literal["live_research_checkpoint"] = "live_research_checkpoint"
    stage: Optional[Any] = None
    proposal: Optional[Any] = None
    context_map_summary: Optional[Any] = None
    auto_continue_option: Optional[Any] = None
    evidence_list: Optional[Any] = None
    evidence_total: Optional[int] = None
    show_new_sample_button: Optional[bool] = None


class LiveResearchSection(SseEnvelope):
    message_type: Literal["live_research_section"] = "live_research_section"
    section_index: Optional[int] = None
    title: Optional[str] = None
    content: Optional[Any] = None
    sources: Optional[Any] = None
    citation_sources: Optional[Any] = None
    citation_format: Optional[Any] = None
    methodology_note: Optional[Any] = None


class LiveResearchWriterStatus(SseEnvelope):
    # dynamic payload (.update): status in {started, section_done, all_done}.
    message_type: Literal["live_research_writer_status"] = "live_research_writer_status"
    status: Optional[str] = None
    total_sections: Optional[int] = None
    completed: Optional[int] = None
    section_title: Optional[str] = None


class LiveResearchExport(SseEnvelope):
    message_type: Literal["live_research_export"] = "live_research_export"
    content: Optional[Any] = None
    format: Optional[Any] = None
    citation_sources: Optional[Any] = None
    citation_format: Optional[Any] = None
    knowledge_graph: Optional[Any] = None


def parse_sse_envelope(data: dict) -> SseEnvelope:
    """Best-effort typed parse. Falls back to base SseEnvelope for
    message_types not yet modelled (open-set discipline — see plan §0.4).
    Never raises on unknown type."""
    mt = data.get("message_type")
    model = _REGISTRY.get(mt, SseEnvelope)
    return model.model_validate(data)


_REGISTRY: dict = {
    "begin-nlweb-response": BeginNlwebResponse,
    "end-nlweb-response": EndNlwebResponse,
    "progress": Progress,
    "complete": Complete,
    "error": ErrorEnvelope,
    "result": Result,
    "intermediate_result": IntermediateResult,
    "research_phase": ResearchPhase,
    "articles": Articles,
    "answer": Answer,
    "nlws": Nlws,
    "summary": Summary,
    "remember": Remember,
    "clarification_required": ClarificationRequired,
    "time_filter_relaxed": TimeFilterRelaxed,
    "low_relevance_warning": LowRelevanceWarning,
    "low_keyword_match_warning": LowKeywordMatchWarning,
    "author_search_no_results": AuthorSearchNoResults,
    "empty_results": EmptyResults,
    "injection_blocked": InjectionBlocked,
    "asking_sites": AskingSites,
    "final_result": FinalResult,
    "deep_research_session_created": DeepResearchSessionCreated,
    "research_error": ResearchError,
    "research_interrupted": ResearchInterrupted,
    "live_research_session_created": LiveResearchSessionCreated,
    "live_research_narration": LiveResearchNarration,
    "live_research_stage_change": LiveResearchStageChange,
    "live_research_checkpoint": LiveResearchCheckpoint,
    "live_research_section": LiveResearchSection,
    "live_research_writer_status": LiveResearchWriterStatus,
    "live_research_export": LiveResearchExport,
}
