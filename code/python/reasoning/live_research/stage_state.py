"""
LiveResearchStageState — 跨 request 持久化的 Live Research 狀態。

每個 session 有一個 LiveResearchStageState，存在 search_sessions.live_research_state JSONB 欄位。
每次 request 開始時從 DB 讀取，request 結束時寫回。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from reasoning.schemas_enhanced import KnowledgeGraph
    from reasoning.schemas_live import TimeRange

logger = logging.getLogger(__name__)

CitationStyle = Literal["author_year", "numeric", "footnote", "none"]


@dataclass
class UserVoice:
    """跨 stage user 訴求統一容器（Plan: lr-user-voice-container-and-4-fixes）。

    每個 sub-field 由特定 stage handler 寫入、特定 downstream consumer 讀取。
    Schema 設計原則：
    - 所有 field 都 JSON-serializable（str / int / list / dict / Literal）
    - 全部有 default → 舊 session restore 自然兼容
    - to_dict / from_dict 對稱，from_dict 對 missing field 容錯
    - 不收斂 format_specs 既有 raw 字串 → user_voice 是 typed 平行通道

    Field consumers（本 plan 範疇）：
    - citation_style: Stage 4 _parse_stage_4_intent → orchestrator._write_section
      → writer.compose_section(citation_format=...)（Fix B）
    - stage2_feedback: Stage 2 _handle_stage_2_response → audit trail
      （目前流程 Stage 2 之後無法回頭追加搜尋，narration 誠實告知；未來 BAB loop consumer）
    - revise_instructions: Stage 5 _parse_revision_intent → orchestrator._write_section
      → writer.compose_section(revise_instruction=...)（Fix I-1）
      CEO OQ 2 拍板：Dict[int, List[str]] accumulate（同段多次改保留歷史 ordered list）
    """

    # Fix B: Stage 4 user 拍板的引用格式 enum
    # None = user 沒拍板（fallback chain: style_features.citation_format → "numeric"）
    citation_style: Optional[CitationStyle] = None

    # Blocker A (2026-05-19) root fix: Stage 4 user 拍板的中文總字數
    # None = user 沒拍板（writer 沿用 outline planner default 每章 800-1500 字）
    # 寫入路徑：Stage 4 _classify_stage_4_response → format_content.target_word_count
    # 讀取路徑：outline planner prompt（_format_format_specs 注入 budget）+ writer
    target_word_count: Optional[int] = None

    # Fix I-2: Stage 2 BAB checkpoint user 回饋
    # 每 entry 含 {"round": str, "text": str}；append-only，跨 round 累積
    # round = stage2 BAB checkpoint 第幾輪（目前固定 "0"；保留欄位給未來 multi-round）
    stage2_feedback: List[Dict[str, str]] = field(default_factory=list)

    # Fix I-1: Stage 5 per-section revision 指示
    # key = section_index（int），value = ordered list of user revision instructions
    # OQ 2 拍板：同段多次 revise → 全部累積（writer prompt 展示完整修訂 context）
    revise_instructions: Dict[int, List[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "citation_style": self.citation_style,
            "target_word_count": self.target_word_count,
            "stage2_feedback": list(self.stage2_feedback),
            # JSON dict key 必須 str — int key serialize 時轉 str
            "revise_instructions": {
                str(k): list(v) for k, v in self.revise_instructions.items()
            },
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "UserVoice":
        """Deserialize from dict. None / missing fields use defaults。

        Backward compat：
        - revise_instructions value 是 str（舊 schema）→ 自動包成 [str]
        - revise_instructions key 非 int 可解 → skip 該 entry
        - citation_style 非 enum 值 → None
        """
        if not d:
            return cls()
        # int key 從 str restore（容錯非 int 字串 → skip）
        # value：List[str] 為新 schema；str 為舊 schema → 包成 [str]
        revise_raw = d.get("revise_instructions") or {}
        revise: Dict[int, List[str]] = {}
        for k, v in revise_raw.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, list):
                revise[idx] = [str(x) for x in v]
            elif isinstance(v, str):
                # 舊 schema：value 是單字串 → wrap 成 list
                revise[idx] = [v]
            # 其它型別 → skip
        cs = d.get("citation_style")
        # Literal 容錯：非 enum 值（舊 row 髒資料 / LLM hallucinate）→ None
        if cs not in ("author_year", "numeric", "footnote", "none", None):
            cs = None
        # target_word_count 容錯：非正整數 → None（舊 row backward compat）
        twc_raw = d.get("target_word_count")
        twc: Optional[int]
        try:
            twc = int(twc_raw) if twc_raw is not None else None
            if twc is not None and twc < 1:
                twc = None
        except (TypeError, ValueError):
            twc = None
        return cls(
            citation_style=cs,
            target_word_count=twc,
            stage2_feedback=list(d.get("stage2_feedback") or []),
            revise_instructions=revise,
        )


@dataclass
class LiveResearchStageState:
    """
    跨 request 持久化的 Live Research 狀態。

    所有欄位都是 JSON-serializable（str, int, list, dict）。
    ContextMap 等 Pydantic model 以 JSON string 存放，
    使用者端負責 parse。
    """

    # === Stage 追蹤 ===
    current_stage: int = 0           # 0=未開始, 1-6=對應 Stage
    stage_status: str = "pending"    # pending / in_progress / checkpoint / completed
    checkpoint_prompt: str = ""      # 當前 checkpoint 的提案文字
    failed_intent_parse_count: int = 0  # Stage 1 intent parser 連續失敗計數（dialog loop fallback 觸發點）

    # === ContextMap（核心） ===
    context_map_json: str = ""       # ContextMap.model_dump_json()
    initial_context_map_json: str = ""  # Version 0 snapshot

    # === Stage-specific state ===
    completed_sections: List[str] = field(default_factory=list)
    style_features_json: str = ""
    # Plan 2 Phase 1: 升級為 Dict[str, Any] 以容納 chapters: List[Dict[str, str]] override。
    # Backward compat: 既有 {"user_specified": str} / {"default": str} 寫法仍 work。
    # 新欄位 chapters = [{"name": ..., "outline": ...}, ...] (writer format_specs.chapters override)
    format_specs: Dict[str, Any] = field(default_factory=dict)
    pending_format_confirmation: bool = False  # Stage 4 mixed path：等 user confirm 已記下的 format_specs
    # UX-9: pending reframe waiting for user confirm
    # JSON-serialized ContextMapRevisionOperation (op_type=reframe_structure)
    # Set when LLM parses reframe_structure intent；下一輪 user confirm 才 apply。
    # 空字串 = 沒有 pending reframe。
    pending_reframe_json: str = ""
    # Bug 2 (2026-05-18) root-fix：reframe proposal markdown 獨立 field，跟
    # `checkpoint_prompt`（原 stage 的 checkpoint）解耦。
    # `_emit_reframe_proposal` 寫入此 field（不再 mutate `checkpoint_prompt`），
    # Stage 4 entry confirm path re-emit `checkpoint_prompt` 拿到的就是原 format prompt
    # 不會被 reframe proposal 污染。
    # 空字串 = 沒有 pending reframe proposal。
    pending_reframe_proposal_markdown: str = ""
    # Plan 4 Phase 1: BookOutline.model_dump_json() — Stage 5 開頭 outline planner LLM 產出。
    # 空字串 = 尚未規劃；Stage 5 進場 idempotent guard 依此判斷是否要呼叫 planner。
    # 舊 session restore 時無此欄位 → from_dict fallback "" (backward compat)。
    book_outline_json: str = ""
    written_sections: List[Dict] = field(default_factory=list)

    # === Grounding (Track A — sprint 2026-05-28) ===
    # key = evidence_id (int), value = List[GroundedClaim.model_dump()]
    # 由 loop_engine._run_mini_reasoning 寫入; Stage 5 chapter writer 讀取。
    # JSON 化時 int key 轉 str (沿 user_voice 既有 pattern)。
    # Gemini Critical 拍板 (2026-05-28): REJECT claim 也入庫並標 critic_status="REJECT"
    # 保留 forensic trail; render 層 (Task 3) 過濾不入 writer prompt。
    evidence_usage: Dict[int, List[Dict]] = field(default_factory=dict)

    # Gemini C-1 拍板 (2026-05-28): REJECT batch metadata trace (雙路追蹤之 2)。
    # 每筆 REJECT batch append 一筆 dict:
    #   {"topic_id": str, "iteration": int, "claim_count": int,
    #    "evidence_ids": List[int], "reason": str}
    # 主表 evidence_usage 含 critic_status="REJECT" claim 主體; 本 log 為 audit trail
    # 方便 oncall 直接撈某次 REJECT batch。
    rejected_claims_log: List[Dict] = field(default_factory=list)

    # addendum C-3 (Track A 2026-05-28): schema version — v1 = sprint 前舊 session
    # (from_dict 偵測無欄位 → default 1), v2 = Track A 後新 session (default 2);
    # backend revise / continue API gate v1 拒絕 (return 409 legacy_schema_session)。
    schema_version: int = 2

    # === Loop state ===
    executed_searches: List[str] = field(default_factory=list)

    # === Evidence Pool（references master list 來源）===
    # JSON-encoded Dict[str(evidence_id), EvidencePoolEntry.model_dump()]
    # 空字串 = 尚未持久化（兼容舊 DB row）
    evidence_pool_json: str = ""

    # === Hallucination Guard（Task 9 — DR-style per-section subset check）===
    # True = 寫作過程中某 section 觸發 Hallucination Guard 自動修正，
    # Stage 6 narration 提示使用者特別檢視 confidence=Low 段落
    hallucination_corrected: bool = False

    # === VP-7: Stage 5 Writer State ===
    # stage_5_writer_running: writer loop 進行中時 True；
    #   loop 結束/CancelledError 都會 reset False（finally clause）。
    # last_completed_section_index: -1 表示尚未開始；resume 邏輯依此 skip 已寫段落。
    stage_5_writer_running: bool = False
    last_completed_section_index: int = -1
    # VP-7: writer per-section checkpoint flow reversal.
    # True 表示 writer 已寫完某段、paused 等 user reply（continue / revise / export）。
    # 寫某段中 / Stage 5 尚未進場 / 全部段已完成均為 False。
    # 主要用途：debug log + 跨 request 持久化。
    stage5_waiting_for_user: bool = False

    # === User Voice 容器（Plan: lr-user-voice-container-and-4-fixes）===
    # 跨 stage user 訴求統一 typed 通道。詳見 docs/specs/live-research-spec.md §4.12。
    user_voice: "UserVoice" = field(default_factory=lambda: UserVoice())

    # === Track E (sprint 2026-05-28) — Temporal BINDING ===
    # user 對研究範圍的時間訴求。由 Stage 1 intent parser 寫入；loop_engine
    # retrieval / writer prompt 讀。None = user 沒給時間訴求 → pipeline
    # pass-through（不過濾、不注入 BINDING）。N-6 紀律：state.time_constraint
    # 是 single source of truth，禁止讀 intent.time_range_extracted 或
    # handler.temporal_range（後者是 DR Stage 0 抽的，跟 LR Stage 1 dialog
    # 可能不同步）。
    time_constraint: Optional["TimeRange"] = None

    # === Track D (sprint 2026-05-28) — Knowledge Graph ===
    # 由 loop_engine._run_mini_reasoning 跨 iteration / 跨 topic merge 累積。
    # None = user 沒啟用 KG / Analyst 沒輸出 KG → pipeline pass-through
    # (Stage 6 export 不附 KG, 前端 KG container hidden)。
    # Merge 紀律: D-AMB-2 — name-based entity dedup + (src,pred,tgt) triple
    # relationship dedup, evidence_ids set union。
    # 沿 Track E pattern: state field forward reference 字串型別 + from_dict local import。
    knowledge_graph: Optional["KnowledgeGraph"] = None

    # === Track F (sprint 2026-05-28) — Critic 擴充 / Consistency Monitor / CoV-lite ===
    # F1 per-section Critic publish gate 結果。
    # key = section_index (int), value = CriticSectionReview.model_dump()
    # 由 orchestrator._run_publish_gate F1 critic call 寫入；Stage 6 export
    # 偵測有 verdict="REJECT" 章節 → SSE 提醒 user。
    # 舊 v1 / 早期 v2 session 無此欄位 → from_dict fallback {} → pipeline pass-through。
    # JSON 化時 int key 轉 str (沿 user_voice / evidence_usage pattern)。
    critic_section_reviews: Dict[int, Dict] = field(default_factory=dict)

    # F2 Consistency Monitor drift log（spec §9.2 自標未實現的補完）。
    # 每輪 BAB iteration 後（_run_consistency_check 跑完）append 一筆
    # ConsistencyDriftEntry.model_dump()。F-AMB-3 LOCKED: 每輪都 append
    # （drift_level=none 也 append；audit trail 完整）。
    # I-3: entry 含 stage 欄位區分 Stage 1 (global) / Stage 2 (per-topic) invoke。
    # 跨 session resume / oncall debug / Track F 後續分析都讀此欄位。
    consistency_drift_log: List[Dict] = field(default_factory=list)

    # === Metadata ===
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict for DB storage."""
        return {
            "current_stage": self.current_stage,
            "stage_status": self.stage_status,
            "checkpoint_prompt": self.checkpoint_prompt,
            "failed_intent_parse_count": self.failed_intent_parse_count,
            "context_map_json": self.context_map_json,
            "initial_context_map_json": self.initial_context_map_json,
            "completed_sections": self.completed_sections,
            "style_features_json": self.style_features_json,
            "format_specs": self.format_specs,
            "pending_format_confirmation": self.pending_format_confirmation,
            "pending_reframe_json": self.pending_reframe_json,
            "pending_reframe_proposal_markdown": self.pending_reframe_proposal_markdown,
            "book_outline_json": self.book_outline_json,
            "written_sections": self.written_sections,
            "executed_searches": self.executed_searches,
            "evidence_pool_json": self.evidence_pool_json,
            "hallucination_corrected": self.hallucination_corrected,
            "stage_5_writer_running": self.stage_5_writer_running,
            "last_completed_section_index": self.last_completed_section_index,
            "stage5_waiting_for_user": self.stage5_waiting_for_user,
            "user_voice": self.user_voice.to_dict(),
            "created_at": self.created_at,
            "last_updated_at": self.last_updated_at,
            # Track A (sprint 2026-05-28): grounding additions —
            # int key → str (JSON 規範)
            "evidence_usage": {
                str(k): list(v) for k, v in self.evidence_usage.items()
            },
            # Gemini C-1: REJECT batch metadata trace
            "rejected_claims_log": list(self.rejected_claims_log),
            # addendum C-3: schema_version 持久化
            "schema_version": self.schema_version,
            # Track E (sprint 2026-05-28): time_constraint Pydantic → dict
            # None → null（既有 v1/v2 早期 session restore 後 None；新 session
            # 若 user 沒給時間訴求也 None）
            "time_constraint": (
                self.time_constraint.model_dump() if self.time_constraint else None
            ),
            # Track D (sprint 2026-05-28): knowledge_graph Pydantic → dict
            # None → null（既有 v1/v2 早期 session restore 後 None；新 session
            # 若 user 沒啟用 KG 或 Analyst 沒輸出也 None）
            "knowledge_graph": (
                self.knowledge_graph.model_dump() if self.knowledge_graph else None
            ),
            # Track F (sprint 2026-05-28): critic_section_reviews —
            # int key → str (JSON 規範，沿 evidence_usage pattern)
            "critic_section_reviews": {
                str(k): dict(v) for k, v in self.critic_section_reviews.items()
            },
            # Track F (sprint 2026-05-28): consistency_drift_log — append-only list
            "consistency_drift_log": list(self.consistency_drift_log),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LiveResearchStageState":
        """Deserialize from dict. Missing fields use defaults."""
        # Track A (sprint 2026-05-28): evidence_usage — JSON dict key 必為 str，
        # restore 時轉回 int (容錯非 int / 非 list value)
        raw_usage = d.get("evidence_usage") or {}
        evidence_usage: Dict[int, List[Dict]] = {}
        for k, v in raw_usage.items():
            try:
                eid = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, list):
                evidence_usage[eid] = list(v)

        # Gemini C-1: rejected_claims_log (v1 session 無此欄位 → 空 list)
        rejected_claims_log = list(d.get("rejected_claims_log") or [])

        # addendum C-3: schema_version 偵測 — 缺欄位 = 舊 session = 1
        # R1 reviewer I-3 fix (sprint 2026-05-28): XOR 雙重檢查 — 異常 case
        # (schema_version missing **AND** evidence_usage 非空) 視為 v2 + log ERROR。
        # 原因: future test author 可能構造 LiveResearchStageState() default schema_version=2
        # 但放 v1 shape (省略 schema_version) + 帶 v2 specific data (evidence_usage) →
        # 落 v1 silently 會誤導 legacy gate test。fail-loud log + 修補成 v2。
        raw_schema_version = d.get("schema_version")
        raw_evidence_usage_for_xor = d.get("evidence_usage") or {}
        if raw_schema_version is None and raw_evidence_usage_for_xor:
            logger.error(
                "[STATE] schema_version missing but evidence_usage non-empty — "
                "treating as v2 (likely client/payload anomaly; v1 sessions cannot "
                "have evidence_usage). raw_keys=%s",
                list(d.keys()),
            )
            schema_version = 2
        else:
            try:
                schema_version = int(raw_schema_version or 1)
            except (TypeError, ValueError):
                schema_version = 1

        # Track E (sprint 2026-05-28): time_constraint deserialize — None 容錯
        # + invalid payload（非 dict / Pydantic 驗證失敗）log warning + fallback None
        raw_tc = d.get("time_constraint")
        time_constraint: Optional["TimeRange"] = None
        if raw_tc:
            try:
                from reasoning.schemas_live import TimeRange
                time_constraint = TimeRange.model_validate(raw_tc)
            except Exception as e:
                logger.warning(
                    "[STATE] time_constraint deserialize failed: %s; "
                    "defaulting to None (legacy / corrupted payload)",
                    e,
                )
                time_constraint = None

        # Track D (sprint 2026-05-28): knowledge_graph deserialize — None 容錯
        # + invalid payload（非 dict / Pydantic 驗證失敗）log warning + fallback None
        # 沿 Track E time_constraint pattern。舊 session (v1 / v2 早期) 無此欄位
        # → load 後 None → pipeline pass-through (不影響任何路徑)。
        raw_kg = d.get("knowledge_graph")
        knowledge_graph: Optional["KnowledgeGraph"] = None
        if raw_kg:
            try:
                from reasoning.schemas_enhanced import KnowledgeGraph
                knowledge_graph = KnowledgeGraph.model_validate(raw_kg)
            except Exception as e:
                logger.warning(
                    "[STATE] knowledge_graph deserialize failed: %s; "
                    "defaulting to None (legacy / corrupted payload)",
                    e,
                )
                knowledge_graph = None

        # Track F (sprint 2026-05-28): critic_section_reviews — JSON str key 轉回 int
        # （容錯非 int / 非 dict value）。舊 v1 / 早期 v2 session 無此欄位 → 空 dict。
        raw_reviews = d.get("critic_section_reviews") or {}
        critic_section_reviews: Dict[int, Dict] = {}
        for k, v in raw_reviews.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict):
                critic_section_reviews[idx] = v

        # Track F (sprint 2026-05-28): consistency_drift_log fallback 空 list
        consistency_drift_log = list(d.get("consistency_drift_log") or [])

        return cls(
            current_stage=d.get("current_stage", 0),
            stage_status=d.get("stage_status", "pending"),
            checkpoint_prompt=d.get("checkpoint_prompt", ""),
            failed_intent_parse_count=d.get("failed_intent_parse_count", 0),
            context_map_json=d.get("context_map_json", ""),
            initial_context_map_json=d.get("initial_context_map_json", ""),
            completed_sections=d.get("completed_sections", []),
            style_features_json=d.get("style_features_json", ""),
            format_specs=d.get("format_specs", {}),
            pending_format_confirmation=d.get("pending_format_confirmation", False),
            pending_reframe_json=d.get("pending_reframe_json", ""),
            pending_reframe_proposal_markdown=d.get("pending_reframe_proposal_markdown", ""),
            book_outline_json=d.get("book_outline_json", ""),
            written_sections=d.get("written_sections", []),
            executed_searches=d.get("executed_searches", []),
            evidence_pool_json=d.get("evidence_pool_json", ""),
            hallucination_corrected=d.get("hallucination_corrected", False),
            stage_5_writer_running=d.get("stage_5_writer_running", False),
            last_completed_section_index=d.get("last_completed_section_index", -1),
            stage5_waiting_for_user=d.get("stage5_waiting_for_user", False),
            user_voice=UserVoice.from_dict(d.get("user_voice")),
            created_at=d.get("created_at", ""),
            last_updated_at=d.get("last_updated_at", ""),
            # Track A grounding additions
            evidence_usage=evidence_usage,
            rejected_claims_log=rejected_claims_log,
            schema_version=schema_version,
            # Track E (sprint 2026-05-28)
            time_constraint=time_constraint,
            # Track D (sprint 2026-05-28)
            knowledge_graph=knowledge_graph,
            # Track F (sprint 2026-05-28)
            critic_section_reviews=critic_section_reviews,
            consistency_drift_log=consistency_drift_log,
        )

    def advance_to_stage(self, stage: int) -> None:
        """前進到下一個 stage。"""
        self.current_stage = stage
        self.stage_status = "in_progress"
        self.checkpoint_prompt = ""
        self.last_updated_at = datetime.now().isoformat()

    def set_checkpoint(self, prompt: str) -> None:
        """設置 checkpoint，等待使用者回覆。"""
        self.stage_status = "checkpoint"
        self.checkpoint_prompt = prompt
        self.last_updated_at = datetime.now().isoformat()

    def complete_stage(self) -> None:
        """標記當前 stage 完成。"""
        self.stage_status = "completed"
        self.last_updated_at = datetime.now().isoformat()
