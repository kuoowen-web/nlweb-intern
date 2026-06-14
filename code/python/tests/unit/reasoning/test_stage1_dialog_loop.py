"""Stage 1 dialog loop 補完相關測試（schema / mutation engine / intent parser / handler）。"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from reasoning.schemas_live import (
    ContextMap,
    ContextMapTopic,
    ContextMapRelation,
    ContextMapRevisionOperation,
    Stage1ParsedIntent,
    Stage4Intent,
    Stage4Action,
)
from reasoning.live_research.stage_state import LiveResearchStageState


def _real_llm_key_available() -> bool:
    """是否有可用真 LLM key。conftest 預設把 key 中和為空 → 回 False → @mark.llm
    真打 LLM 測試自動 SKIP（與 contract suite skip_if_no_api 同型，非弱化）。
    僅 NLWEB_ALLOW_REAL_LLM=1 opt-in（conftest 不中和）+ 環境有真 key 時才 run。"""
    return any(
        bool(os.environ.get(k, "").strip())
        for k in ("OPENAI_API_KEY", "NLWEB_ANTHROPIC_API_KEY",
                  "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AZURE_OPENAI_API_KEY")
    )


_skip_if_no_real_llm = pytest.mark.skipif(
    not _real_llm_key_available(),
    reason="real LLM 整合測試（@mark.llm）：無真 key（conftest 已中和）→ SKIP，"
           "需 NLWEB_ALLOW_REAL_LLM=1 opt-in + 真 key 才跑",
)


def _make_cm(version=0):
    """共用 ContextMap fixture：3 topics + 1 relation。"""
    return ContextMap(
        research_question="台灣綠能",
        topics=[
            ContextMapTopic(topic_id="t1", name="土地", domain="政策", relevance="core"),
            ContextMapTopic(topic_id="t2", name="社區", domain="治理", relevance="core"),
            ContextMapTopic(topic_id="t3", name="電網", domain="基建", relevance="supporting"),
        ],
        relations=[
            ContextMapRelation(
                relation_id="r1", source_topic_id="t1", target_topic_id="t2",
                relation_type="causes",
            ),
        ],
        version=version,
    )


class TestStage1DialogLoopSchemas:
    def test_revision_op_merge_topics(self):
        op = ContextMapRevisionOperation(
            op_type="merge_topics",
            source_topic_ids=["t1", "t3"],
            merged_name="能源轉型",
        )
        assert op.op_type == "merge_topics"
        assert op.source_topic_ids == ["t1", "t3"]

    def test_revision_op_split_topic(self):
        op = ContextMapRevisionOperation(
            op_type="split_topic",
            split_from_topic_id="t2",
            split_into=[{"name": "A", "description": "x", "evidence_ids": [1]}],
        )
        assert op.split_from_topic_id == "t2"
        assert len(op.split_into) == 1

    def test_stage1_intent_confirm(self):
        intent = Stage1ParsedIntent(action="confirm", summary="OK")
        assert intent.action == "confirm"
        assert intent.operations == []

    def test_stage1_intent_adjust_with_operations(self):
        intent = Stage1ParsedIntent(
            action="adjust",
            operations=[
                ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t3"),
            ],
            summary="移除電網整合議題",
        )
        assert len(intent.operations) == 1

    def test_stage4_intent_enum(self):
        intent = Stage4Intent(intent="structure_change")
        assert intent.intent == "structure_change"

    def test_stage4_action_enum_values(self):
        """改動 6：Stage4Action 完整 enum value 檢查。"""
        assert Stage4Action.auto_continue.value == "auto_continue"
        assert Stage4Action.format_spec.value == "format_spec"
        assert Stage4Action.structure_change.value == "structure_change"
        assert Stage4Action.mixed.value == "mixed"

    def test_stage4_intent_mixed_with_format_spec_extracted(self):
        intent = Stage4Intent(
            intent="mixed",
            format_spec_extracted="每段 500 字",
            raw_message="改成 5 章，每段 500 字",
        )
        assert intent.intent == "mixed"
        assert intent.format_spec_extracted == "每段 500 字"
        assert intent.raw_message == "改成 5 章，每段 500 字"

    def test_stage_state_failed_count_default(self):
        s = LiveResearchStageState()
        assert s.failed_intent_parse_count == 0

    def test_stage_state_roundtrip_preserves_failed_count(self):
        s = LiveResearchStageState(failed_intent_parse_count=2)
        s2 = LiveResearchStageState.from_dict(s.to_dict())
        assert s2.failed_intent_parse_count == 2


# ============================================================================
# Task 2: Mutation engine (_apply_context_map_revisions) — pure function tests
# ============================================================================


class TestStage1ParsedIntentClarifyingQuestion:
    """clarifying_question 欄位（empty-ops clarification dialog plan）。"""

    def test_clarifying_question_default_empty(self):
        from reasoning.schemas_live import Stage1ParsedIntent
        intent = Stage1ParsedIntent(action="adjust", operations=[])
        assert intent.clarifying_question == ""

    def test_clarifying_question_can_be_set(self):
        from reasoning.schemas_live import Stage1ParsedIntent
        intent = Stage1ParsedIntent(
            action="adjust",
            operations=[],
            clarifying_question="你說「太細」是希望整段刪掉、還是降為輔助議題?",
        )
        assert intent.clarifying_question == "你說「太細」是希望整段刪掉、還是降為輔助議題?"

    def test_clarifying_question_independent_of_summary(self):
        """clarifying_question 跟 summary 是不同 semantic（一個給 user,一個進 revision_history)。"""
        from reasoning.schemas_live import Stage1ParsedIntent
        intent = Stage1ParsedIntent(
            action="adjust",
            operations=[],
            summary="user 訴求模糊,需澄清",
            clarifying_question="想保留電力議題的哪個面向?",
        )
        assert intent.summary == "user 訴求模糊,需澄清"
        assert intent.clarifying_question == "想保留電力議題的哪個面向?"


class TestApplyContextMapRevisions:
    def test_merge_topics(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(
            op_type="merge_topics",
            source_topic_ids=["t1", "t3"],
            merged_name="能源轉型衝突",
        )
        cm2, delta, warnings = _apply_context_map_revisions(cm, [op], "merge")
        assert cm2 is not None
        names = [t.name for t in cm2.topics]
        assert "能源轉型衝突" in names
        assert "土地" not in names
        assert "電網" not in names
        assert cm2.version == 1
        # revision_history 最新一筆的 reason 應該包含 parse summary 或預設說明
        assert "merge" in cm2.revision_history[-1].reason or "用戶" in cm2.revision_history[-1].reason or "使用者" in cm2.revision_history[-1].reason

    def test_remove_topic(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t3")
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        assert len(cm2.topics) == 2
        assert "t3" in delta.removed_topics

    def test_add_topic(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(
            op_type="add_topic",
            new_topic_name="國際案例",
            new_topic_description="德國 Energiewende",
            new_topic_relevance="core",
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        assert any(t.name == "國際案例" for t in cm2.topics)
        assert len(delta.added_topics) == 1

    def test_rename_topic(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(
            op_type="rename_topic", target_topic_id="t1", new_name="土地使用衝突",
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        names = [t.name for t in cm2.topics]
        assert "土地使用衝突" in names

    def test_change_relevance(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(
            op_type="change_relevance", target_topic_id="t3", new_relevance="core",
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        t3 = next(t for t in cm2.topics if t.topic_id == "t3")
        assert t3.relevance == "core"

    def test_split_topic(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(
            op_type="split_topic",
            split_from_topic_id="t2",
            split_into=[
                {"name": "居民溝通", "description": "", "evidence_ids": []},
                {"name": "社區共有", "description": "", "evidence_ids": []},
            ],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        names = [t.name for t in cm2.topics]
        assert "居民溝通" in names
        assert "社區共有" in names
        assert "社區" not in names  # 原 t2 被移除

    def test_invalid_topic_id_skipped_with_warning(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        op = ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="nonexistent")
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        assert len(cm2.topics) == 3  # 未變
        assert any("nonexistent" in msg for msg in w)

    def test_empty_result_rejected(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        ops = [
            ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t1"),
            ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t2"),
            ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t3"),
        ]
        cm2, delta, w = _apply_context_map_revisions(cm, ops, "delete all")
        assert cm2 is None
        assert delta is None
        assert any("至少要保留" in msg or "empty" in msg.lower() for msg in w)

    def test_multi_op_sequential(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm()
        ops = [
            ContextMapRevisionOperation(
                op_type="merge_topics",
                source_topic_ids=["t1", "t3"],
                merged_name="能源轉型",
            ),
            ContextMapRevisionOperation(
                op_type="add_topic",
                new_topic_name="國際案例",
                new_topic_relevance="core",
            ),
        ]
        cm2, delta, w = _apply_context_map_revisions(cm, ops, "")
        assert cm2 is not None
        names = [t.name for t in cm2.topics]
        assert "能源轉型" in names
        assert "國際案例" in names
        assert "土地" not in names
        assert cm2.version == 1  # 一次 batch 只 bump 一次

    def test_version_bump_and_revision_history(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm(version=2)
        op = ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t3")
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "user wants remove")
        assert cm2.version == 3
        assert delta.from_version == 2
        assert delta.to_version == 3
        assert len(cm2.revision_history) == 1

    def test_op_handler_exception_aborts_mutation(self):
        """CEO 補強：中途某 op 拋 exception → 整體 abort + 原 cm 不變（transactional safety）。"""
        from reasoning.live_research import orchestrator as orch_module
        from reasoning.live_research.orchestrator import _apply_context_map_revisions

        cm = _make_cm()
        pre_json = cm.model_dump_json()

        # 用 monkeypatch 強制讓 rename_topic handler 拋 exception
        # 第一個 op 正常執行（remove_topic），第二個 op 觸發 handler 拋 exception
        original_handler = orch_module._REVISION_HANDLERS["rename_topic"]

        def _raising_handler(cm_arg, op_arg, delta_arg, warnings_arg):
            raise RuntimeError("simulated handler crash")

        orch_module._REVISION_HANDLERS["rename_topic"] = _raising_handler
        try:
            operations = [
                ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t3"),
                ContextMapRevisionOperation(op_type="rename_topic", target_topic_id="t1", new_name="should_fail"),
            ]
            result_cm, delta, warnings = _apply_context_map_revisions(cm, operations, "test")
        finally:
            orch_module._REVISION_HANDLERS["rename_topic"] = original_handler

        # abort：mutated cm 與 delta 都應為 None
        assert result_cm is None
        assert delta is None
        # warnings 應含 exception 提示
        assert any(
            ("exception" in w.lower()) or ("失敗" in w) or ("crash" in w.lower())
            for w in warnings
        )
        # 原 cm 物件未被 mutate（deep copy 紀律：working copy 上做事，外部 cm 不變）
        assert cm.model_dump_json() == pre_json


# ============================================================================
# Task 3: _parse_stage_1_intent — LLM intent parser tests
# ============================================================================


class TestParseStage1Intent:
    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=True)

    @pytest.mark.asyncio
    async def test_dry_run_returns_confirm(self, orchestrator):
        cm = _make_cm()
        intent = await orchestrator._parse_stage_1_intent("你決定吧", cm)
        assert intent.action == "confirm"

    @pytest.mark.asyncio
    async def test_parse_stage_1_intent_confirm_via_llm(self, orchestrator):
        from unittest.mock import AsyncMock, patch
        cm = _make_cm()
        orchestrator.dry_run = False
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value={
                "action": "confirm",
                "operations": [],
                "summary": "OK",
            }),
            create=True,
        ):
            intent = await orchestrator._parse_stage_1_intent("看起來不錯", cm)
            assert intent.action == "confirm"

    @pytest.mark.asyncio
    async def test_parse_stage_1_intent_adjust_with_merge(self, orchestrator):
        from unittest.mock import AsyncMock, patch
        cm = _make_cm()
        orchestrator.dry_run = False
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value={
                "action": "adjust",
                "operations": [
                    {
                        "op_type": "merge_topics",
                        "source_topic_ids": ["t1", "t3"],
                        "merged_name": "能源轉型",
                    }
                ],
                "summary": "合併 1+3",
            }),
            create=True,
        ):
            intent = await orchestrator._parse_stage_1_intent("把第 1 和第 3 合併", cm)
            assert intent.action == "adjust"
            assert len(intent.operations) == 1
            assert intent.operations[0].op_type == "merge_topics"

    @pytest.mark.asyncio
    async def test_parse_stage_1_intent_llm_returns_none_returns_none(self, orchestrator):
        from unittest.mock import AsyncMock, patch
        cm = _make_cm()
        orchestrator.dry_run = False
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value=None),
            create=True,
        ):
            # LLM 失敗 → return None，讓 caller 走 fallback narration
            intent = await orchestrator._parse_stage_1_intent("xxx", cm)
            assert intent is None


# ============================================================================
# Task 4: _handle_stage_1_response — full dialog loop tests
# ============================================================================


class TestParseStage1IntentClarifyingQuestion:
    """_parse_stage_1_intent 解析 clarifying_question 欄位
    (empty-ops clarification dialog plan)。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=True)

    @pytest.mark.asyncio
    async def test_parse_empty_ops_with_clarifying_question(self, orchestrator):
        from unittest.mock import AsyncMock, patch
        cm = _make_cm()
        orchestrator.dry_run = False
        mock_response = {
            "action": "adjust",
            "operations": [],
            "summary": "user 訴求模糊:電力供需太細",
            "clarifying_question": "你說「太細」是希望整段刪掉,還是降為輔助議題?",
        }
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value=mock_response),
            create=True,
        ):
            intent = await orchestrator._parse_stage_1_intent(
                "電力供需太細,先放放", cm
            )
        assert intent is not None
        assert intent.action == "adjust"
        assert intent.operations == []
        assert intent.clarifying_question == "你說「太細」是希望整段刪掉,還是降為輔助議題?"

    @pytest.mark.asyncio
    async def test_parse_old_response_without_clarifying_question_still_works(
        self, orchestrator
    ):
        """既有 LLM response(沒 clarifying_question 欄位)回 default '',不破舊行為。"""
        from unittest.mock import AsyncMock, patch
        cm = _make_cm()
        orchestrator.dry_run = False
        mock_response = {"action": "confirm", "operations": [], "summary": "OK"}
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value=mock_response),
            create=True,
        ):
            intent = await orchestrator._parse_stage_1_intent("OK", cm)
        assert intent is not None
        assert intent.action == "confirm"
        assert intent.clarifying_question == ""


class TestHandleStage1Response:
    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        return orch

    @pytest.fixture
    def stage1_state(self):
        cm = _make_cm()
        state = LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
        )
        return state

    @pytest.mark.asyncio
    async def test_auto_continue_advances(self, orchestrator, stage1_state):
        result = await orchestrator._handle_stage_1_response(
            stage1_state, user_message="", auto_continue=True
        )
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_confirm_advances(self, orchestrator, stage1_state):
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="confirm", operations=[], summary="OK"
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="看起來不錯", auto_continue=False
            )
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_adjust_merge_keeps_checkpoint_and_bumps_version(
        self, orchestrator, stage1_state
    ):
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="adjust",
                operations=[ContextMapRevisionOperation(
                    op_type="merge_topics",
                    source_topic_ids=["t1", "t3"],
                    merged_name="能源轉型",
                )],
                summary="合併 1+3",
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="把第 1 和第 3 合併", auto_continue=False
            )
        assert result.stage_status == "checkpoint"  # 保持 checkpoint
        cm2 = ContextMap.model_validate_json(result.context_map_json)
        assert cm2.version == 1  # bump 過
        names = [t.name for t in cm2.topics]
        assert "能源轉型" in names

    @pytest.mark.asyncio
    async def test_adjust_empty_operations_treated_as_confirm(
        self, orchestrator, stage1_state
    ):
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="adjust", operations=[], summary="模糊"
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="呃", auto_continue=False
            )
        # 沒實質訴求 → narration + advance
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_llm_api_fail_none_keeps_checkpoint_no_count_bump(
        self, orchestrator, stage1_state
    ):
        """#20 改善：intent is None = LLM API 失敗（系統端）→ 系統暫時無法處理文案、
        不累積 failed_intent_parse_count、不 force advance（API fail 不是 user 講不清）。"""
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=None),  # LLM API fail
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="????", auto_continue=False
            )
        assert result.stage_status == "checkpoint"
        # API fail 不累積計數（不該餵 force-advance 安全閥）
        assert result.failed_intent_parse_count == 0
        sent = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narrations = [
            m for m in sent if m.get("message_type") == "live_research_narration"
        ]
        # 系統端文案，不是怪 user 的「我沒看懂」
        assert any("系統暫時無法處理" in m.get("text", "") for m in narrations), \
            f"expect system-unavailable narration, got {narrations}"
        assert not any("沒看懂" in m.get("text", "") for m in narrations), \
            f"API fail 不該說「我沒看懂」（怪 user），got {narrations}"

    @pytest.mark.asyncio
    async def test_llm_api_fail_repeated_does_not_force_advance(
        self, orchestrator, stage1_state
    ):
        """API fail 反覆出現也不該 force advance（即使 count 已高）——
        系統掛了，user 重講沒用，silent 推進到下一 stage 是錯的。"""
        from unittest.mock import AsyncMock, patch
        stage1_state.failed_intent_parse_count = 2  # 即使先前有計數
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=None),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="?", auto_continue=False
            )
        # 不 force advance，仍在 Stage 1 checkpoint
        assert result.stage_status == "checkpoint"
        assert result.current_stage == 1

    @pytest.mark.asyncio
    async def test_empty_context_map_after_mutation_rejected(
        self, orchestrator, stage1_state
    ):
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="adjust",
                operations=[
                    ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t1"),
                    ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t2"),
                    ContextMapRevisionOperation(op_type="remove_topic", target_topic_id="t3"),
                ],
                summary="全刪",
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="全刪", auto_continue=False
            )
        # ContextMap 應保持原狀（不空）
        cm = ContextMap.model_validate_json(result.context_map_json)
        assert len(cm.topics) == 3
        assert result.stage_status == "checkpoint"

    @pytest.mark.asyncio
    async def test_routing_does_not_advance_when_checkpoint_kept(
        self, orchestrator, stage1_state
    ):
        """continue_from_checkpoint 看到 stage_status=checkpoint 時不該呼叫 _run_stage_2。"""
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_run_stage_2", new=AsyncMock()
        ) as mock_run_stage_2, patch.object(
            # 直接 shadow _handle_stage_1_response 回 checkpoint
            orchestrator, "_handle_stage_1_response",
            new=AsyncMock(return_value=LiveResearchStageState(
                current_stage=1, stage_status="checkpoint",
                context_map_json=stage1_state.context_map_json,
            )),
        ):
            result = await orchestrator.continue_from_checkpoint(
                stage1_state, user_message="調整", auto_continue=False
            )
        assert result.stage_status == "checkpoint"
        mock_run_stage_2.assert_not_called()


# ============================================================================
# Track E (LR DR-parity sprint 2026-05-28) — Stage 1 time_range_extracted wiring
# ============================================================================


class TestTrackEStage1TimeConstraintWiring:
    """Track E E2：_handle_stage_1_response 把 intent.time_range_extracted 寫進
    state.time_constraint。

    紀律（N-6）：state.time_constraint 是 single source of truth；
    intent.time_range_extracted 只是 LLM 抽取暫存。
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        return orch

    @pytest.fixture
    def stage1_state(self):
        cm = _make_cm()
        return LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
        )

    @pytest.mark.asyncio
    async def test_handle_stage_1_response_writes_time_constraint_to_state(
        self, orchestrator, stage1_state
    ):
        """user reply 含時間訴求 → state.time_constraint 被寫入。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import Stage1ParsedIntent, TimeRange

        mock_intent = Stage1ParsedIntent(
            action="confirm",
            operations=[],
            summary="確認結構，限定 2024 後",
            time_range_extracted=TimeRange(
                start_date="2024-01-01",
                end_date=None,
                raw_phrase="2024 之後",
                user_selected=True,
            ),
        )
        with patch.object(
            orchestrator,
            "_parse_stage_1_intent",
            new=AsyncMock(return_value=mock_intent),
        ):
            state = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="OK，但只看 2024 之後的", auto_continue=False
            )
        assert state.time_constraint is not None
        assert state.time_constraint.start_date == "2024-01-01"
        assert state.time_constraint.end_date is None
        assert state.time_constraint.user_selected is True
        assert state.time_constraint.raw_phrase == "2024 之後"

    @pytest.mark.asyncio
    async def test_handle_stage_1_response_no_time_keeps_state_time_constraint_none(
        self, orchestrator, stage1_state
    ):
        """user reply 沒提時間 → state.time_constraint 仍 None（既有 confirm 路徑不受影響）。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import Stage1ParsedIntent

        mock_intent = Stage1ParsedIntent(action="confirm", time_range_extracted=None)
        with patch.object(
            orchestrator,
            "_parse_stage_1_intent",
            new=AsyncMock(return_value=mock_intent),
        ):
            state = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="OK", auto_continue=False
            )
        assert state.time_constraint is None


# ============================================================================
# Task 5: Stage 4 redirect — _parse_stage_4_intent + _handle_stage_4_response
# ============================================================================


class TestHandleStage1EmptyOpsClarification:
    """Stage 1 empty-ops + clarifying_question dispatch
    (empty-ops clarification dialog plan)。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage1_state(self):
        cm = _make_cm()
        return LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
            checkpoint_prompt="原本的研究結構提案...",
        )

    @pytest.mark.asyncio
    async def test_empty_ops_with_clarifying_question_emits_checkpoint(
        self, orchestrator, stage1_state
    ):
        """clarifying_question 非空 -> emit narration 帶問句 + 重 emit checkpoint, stage 不前進。"""
        from unittest.mock import AsyncMock, patch

        clarifying = "你說「太細」是希望整段刪掉,還是降為輔助議題?"
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="adjust",
                operations=[],
                summary="user 訴求模糊:電力供需太細",
                clarifying_question=clarifying,
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="電力供需太細,先放放", auto_continue=False
            )

        # Stage 不前進
        assert result.current_stage == 1
        assert result.stage_status == "checkpoint"

        sent = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narrations = [m for m in sent if m.get("message_type") == "live_research_narration"]
        checkpoints = [m for m in sent if m.get("message_type") == "live_research_checkpoint"]
        assert len(narrations) >= 1, f"expect narration with clarifying question, got {sent}"
        assert clarifying in narrations[-1].get("text", ""), \
            f"narration text should contain clarifying_question, got {narrations[-1]}"
        assert len(checkpoints) >= 1, f"expect re-emit checkpoint, got {sent}"
        assert checkpoints[-1].get("stage") == 1

    @pytest.mark.asyncio
    async def test_empty_ops_without_clarifying_question_still_advances(
        self, orchestrator, stage1_state
    ):
        """既有 path 保持: clarifying_question='' + empty ops -> advance + narration '直接用'。"""
        from unittest.mock import AsyncMock, patch

        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="adjust",
                operations=[],
                summary="user 模糊但無實質訴求",
                clarifying_question="",
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="嗯", auto_continue=False
            )

        # 既有行為:advance (complete_stage)
        assert result.stage_status == "completed"

        sent = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narrations = [m for m in sent if m.get("message_type") == "live_research_narration"]
        # 既有 narration「沒問題,目前的結構直接用。」
        assert any("直接用" in m.get("text", "") for m in narrations), \
            f"expect '沒問題,目前的結構直接用' narration, got {narrations}"

    @pytest.mark.asyncio
    async def test_intent_is_none_goes_to_system_unavailable_narration(
        self, orchestrator, stage1_state
    ):
        """#20 改善：LLM API 失敗(return None) -> 系統暫時無法處理文案 + retry checkpoint,
        stage 不前進、不累積計數（與「真模糊」clarification 分支區分）。"""
        from unittest.mock import AsyncMock, patch

        stage1_state.failed_intent_parse_count = 0
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=None),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="亂打一通", auto_continue=False
            )

        # API fail path：系統端文案 + retry checkpoint, stage 不前進
        assert result.current_stage == 1
        sent = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narrations = [m for m in sent if m.get("message_type") == "live_research_narration"]
        assert any("系統暫時無法處理" in m.get("text", "") for m in narrations), \
            f"expect system-unavailable narration, got {narrations}"
        # 不累積計數（不混淆「真模糊」path）
        assert result.failed_intent_parse_count == 0


class TestStage4Redirect:
    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage4_state(self):
        # UX-9 Task 2.4：Stage 4 entry reframe 需要 context_map_json
        # 才能用 _parse_stage_1_intent 解 reframe，舊 fixture 補 cm。
        cm = _make_cm()
        return LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            checkpoint_prompt="請告訴我格式偏好",
            context_map_json=cm.model_dump_json(),
        )

    @pytest.mark.asyncio
    async def test_auto_continue_uses_default(self, orchestrator, stage4_state):
        result = await orchestrator._handle_stage_4_response(
            stage4_state, user_message="", auto_continue=True
        )
        assert result.format_specs == {"default": "markdown_apa"}
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_pure_format_spec_advances(self, orchestrator, stage4_state):
        """TypeAgent: adjust_format action → 寫 format_specs + complete_stage。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )
        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="用 APA 引用、每段 500 字",
                ),
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state, user_message="用 APA 引用、每段 500 字", auto_continue=False
            )
        assert result.stage_status == "completed"
        assert "user_specified" in result.format_specs

    @pytest.mark.asyncio
    async def test_structure_change_triggers_reframe_entry_not_redirect(
        self, orchestrator, stage4_state
    ):
        """TypeAgent: new_structure_request action → typed reframe entry，
        不再 emit「請點上方 Stage 1」 redirect narration。
        """
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, ChapterSpec,
        )

        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[ChapterSpec(name="前言"), ChapterSpec(name="結論")],
                    summary="2 章",
                ),
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state, user_message="我要改成 2 章", auto_continue=False,
            )
        # 保持 stage 4（state.current_stage 不退）
        assert result.current_stage == 4
        assert result.stage_status == "checkpoint"
        # pending_reframe_json 有值
        assert result.pending_reframe_json != ""
        # 不 emit「請點上方 Stage 1」 redirect
        sent_messages = [
            c.args[0] for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert not any("請點上方 Stage 1" in t for t in narration_texts)

    @pytest.mark.asyncio
    async def test_mixed_records_format_part_and_triggers_reframe(
        self, orchestrator, stage4_state
    ):
        """TypeAgent: new_structure_request + format_content → 同時記 format + reframe。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, Stage4FormatPayload, ChapterSpec,
        )

        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="A"), ChapterSpec(name="B"), ChapterSpec(name="C"),
                        ChapterSpec(name="D"), ChapterSpec(name="E"),
                    ],
                    summary="5 章",
                ),
                format_content=Stage4FormatPayload(
                    format_spec_extracted="每段 500 字、APA 引用",
                    citation_style_extracted="author_year",
                ),
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state,
                user_message="改成 5 章，每段 500 字、APA 引用",
                auto_continue=False,
            )
        assert result.stage_status == "checkpoint"
        assert result.format_specs.get("user_specified") == "每段 500 字、APA 引用"
        # 兩個 pending 都 set
        assert result.pending_format_confirmation is True
        assert result.pending_reframe_json != ""

    @pytest.mark.asyncio
    async def test_parse_fail_falls_back_to_unclear_narration(
        self, orchestrator, stage4_state
    ):
        """TypeAgent: classifier 失敗 → fallback unclear narration（不 silent fail）。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.unclear,
                clarifying_question="（系統暫時無法解析，請改用一句話描述訴求）",
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state, user_message="some preference", auto_continue=False
            )
        # unclear → 保持 checkpoint，narration emit
        assert result.stage_status == "checkpoint"


# ============================================================================
# Task 6: Stage 5 structure_change action + parse-fail behavior
# ============================================================================


class TestStage5StructureChange:
    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage5_state(self):
        cm = _make_cm()
        return LiveResearchStageState(
            current_stage=5, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            # D-2026-06-11 done completeness gate：2 core topics 全寫完（index=1 ↔
            # written_sections 2 筆自洽），test_done_still_advances 語意 = 全寫完才 advance
            last_completed_section_index=1,
            written_sections=[
                {"section_index": 0, "title": "土地", "content": "...",
                 "sources_used": [], "confidence_level": "Medium"},
                {"section_index": 1, "title": "社區", "content": "...",
                 "sources_used": [], "confidence_level": "Medium"},
            ],
        )

    @pytest.mark.asyncio
    async def test_structure_change_redirects(self, orchestrator, stage5_state):
        from unittest.mock import AsyncMock, patch
        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch.object(
            orchestrator, "_parse_revision_intent",
            new=AsyncMock(return_value={
                "action": "structure_change",
                "reason": "user wants merge chapters",
            }),
        ), patch(
            "reasoning.live_research.orchestrator._classify_meta_intent",
            new=AsyncMock(return_value="substantive"),
        ):
            result = await orchestrator._handle_stage_5_response(
                stage5_state, user_message="把第 1 和第 2 段合併", auto_continue=False
            )
        assert result.stage_status == "checkpoint"
        sent_messages = [
            c.args[0] for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert any("第一階段" in t for t in narration_texts)

    @pytest.mark.asyncio
    async def test_done_still_advances(self, orchestrator, stage5_state):
        from unittest.mock import AsyncMock, patch
        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch.object(
            orchestrator, "_parse_revision_intent",
            new=AsyncMock(return_value={"action": "done", "reason": "OK"}),
        ), patch(
            "reasoning.live_research.orchestrator._classify_meta_intent",
            new=AsyncMock(return_value="substantive"),
        ):
            result = await orchestrator._handle_stage_5_response(
                stage5_state, user_message="可以匯出", auto_continue=False
            )
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_stage5_parse_fail_stays_at_checkpoint(self, orchestrator, stage5_state):
        """#20 改善：parse 回 None = LLM API 失敗（系統端）→ 系統暫時無法處理文案 +
        保持 checkpoint，不 silent advance、不怪 user「沒看懂」。"""
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_parse_revision_intent",
            new=AsyncMock(return_value=None),  # LLM API fail
        ):
            result = await orchestrator._handle_stage_5_response(
                stage5_state, user_message="????", auto_continue=False
            )
        assert result.stage_status == "checkpoint"
        sent_messages = [
            c.args[0] for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert any("系統暫時無法處理" in t for t in narration_texts)


# ============================================================================
# UX-9: reframe_structure mutation tests
# ============================================================================


def _make_cm_with_evidence():
    """共用 fixture：3 topics + 各帶 evidence_ids。"""
    return ContextMap(
        research_question="台灣綠能",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="土地", domain="政策", relevance="core",
                evidence_ids=[1, 2, 3],
            ),
            ContextMapTopic(
                topic_id="t2", name="社區", domain="治理", relevance="core",
                evidence_ids=[4, 5],
            ),
            ContextMapTopic(
                topic_id="t3", name="電網", domain="基建", relevance="supporting",
                evidence_ids=[6, 7, 8],
            ),
        ],
        relations=[
            ContextMapRelation(
                relation_id="r1", source_topic_id="t1", target_topic_id="t2",
                relation_type="causes",
            ),
        ],
        version=0,
    )


class TestReframeStructure:
    """UX-9 Phase 3 Task 3.1：reframe_structure op 8 個 unit tests。"""

    def test_reframe_replaces_all_topics(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"},
                {"name": "國內案例"},
                {"name": "國際比較"},
                {"name": "政策建議"},
                {"name": "結論"},
            ],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "整體重組為 5 章")
        assert cm2 is not None
        assert len(cm2.topics) == 5
        names = [t.name for t in cm2.topics]
        # 既有 topics 全砍
        assert "土地" not in names
        assert "社區" not in names
        assert "電網" not in names
        # 新 chapters 全在
        assert names == ["前言", "國內案例", "國際比較", "政策建議", "結論"]
        assert cm2.version == 1

    def test_reframe_with_new_research_question(self):
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "緒論"}, {"name": "案例"}, {"name": "結論"}],
            new_research_question="台灣綠能社區共有模式的可行性",
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        assert cm2.research_question == "台灣綠能社區共有模式的可行性"

    def test_reframe_preserves_evidence_pool_via_state_level(self):
        """D-2：evidence_pool 在 state level，不受 ContextMap mutation 影響。

        驗證 _apply_context_map_revisions 不接受/不回傳 evidence_pool，
        且 reframe 後 ContextMap 內所有 evidence_ids 仍能 trace back to pool。
        """
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        # Pre：所有 evidence_ids
        pre_all_ids = sorted(set(eid for t in cm.topics for eid in t.evidence_ids))
        assert pre_all_ids == [1, 2, 3, 4, 5, 6, 7, 8]

        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "前言"}, {"name": "案例"}, {"name": "結論"}],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        # Post：所有 evidence_ids 仍然在（重新分配給新 chapters）
        post_all_ids = sorted(set(eid for t in cm2.topics for eid in t.evidence_ids))
        assert post_all_ids == pre_all_ids  # 無遺失

    def test_reframe_evidence_ids_to_first_chapter(self):
        """D-2：所有 leftover evidence_ids 都塞到第一個 new chapter。"""
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"},
                {"name": "案例"},
                {"name": "結論"},
            ],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        # 第一個 chapter 接全部
        assert cm2.topics[0].name == "前言"
        assert sorted(cm2.topics[0].evidence_ids) == [1, 2, 3, 4, 5, 6, 7, 8]
        # 其他 chapters evidence_ids 空
        assert cm2.topics[1].evidence_ids == []
        assert cm2.topics[2].evidence_ids == []

    def test_reframe_default_relevance_core(self):
        """D-3：default relevance core（Stage 2 BAB 只跑 core，全 core 確保都會被寫到）。"""
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "國內案例"},      # 無 keyword，default core
                {"name": "國際比較"},      # 無 keyword，default core
                {"name": "政策建議"},      # 無 keyword，default core
            ],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        for t in cm2.topics:
            assert t.relevance == "core", \
                f"預期 {t.name} 為 core，實際 {t.relevance}"

    def test_reframe_with_explicit_supporting_relevance(self):
        """D-3：keyword + explicit 共同決定 relevance。"""
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"},                        # core default
                {"name": "背景文獻"},                    # keyword → supporting
                {"name": "案例", "relevance": "supporting"},  # explicit supporting
                {"name": "結論"},                        # core default
            ],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        relevance_map = {t.name: t.relevance for t in cm2.topics}
        assert relevance_map["前言"] == "core"
        assert relevance_map["背景文獻"] == "supporting"
        assert relevance_map["案例"] == "supporting"
        assert relevance_map["結論"] == "core"

    def test_reframe_empty_chapters_rejected(self):
        """new_chapters 為空 → warnings + cm 不變（empty guard 之後變空 → reject）。"""
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        # handler 早 return，empty guard 之後砍光的話會 reject
        # 但本案 handler 沒進入 clear → cm 保持原狀，warnings 有提示
        assert any("new_chapters 為空" in msg for msg in w)
        # 因為 handler 早 return，topics 不變
        assert cm2 is not None
        assert len(cm2.topics) == 3  # 原 cm 不變

    def test_reframe_clears_relations(self):
        """relations 全清空（reframe 後既有 relations 都失效）。"""
        from reasoning.live_research.orchestrator import _apply_context_map_revisions
        cm = _make_cm_with_evidence()
        assert len(cm.relations) == 1  # 原本有 1 條 relation
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "前言"}, {"name": "結論"}],
        )
        cm2, delta, w = _apply_context_map_revisions(cm, [op], "")
        assert cm2 is not None
        assert cm2.relations == []  # 全清空


# ============================================================================
# UX-9: integration test — _handle_stage_1_response reframe confirm round
# ============================================================================


class TestHandleStage1ReframeConfirmRound:
    """UX-9 Phase 3 Task 3.2：Cayenne use case integration test。

    完整 round trip：
    1. user 給結構訴求 → LLM 解出 reframe_structure op
    2. handler emit detail-rich checkpoint + set pending_reframe_json
    3. user 回「OK」→ handler apply reframe + advance
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage1_state(self):
        cm = _make_cm_with_evidence()
        return LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
            checkpoint_prompt="原始 outline",
        )

    @pytest.mark.asyncio
    async def test_reframe_round_1_emits_checkpoint_and_stores_pending(
        self, orchestrator, stage1_state
    ):
        """Round 1：reframe intent → emit detail-rich checkpoint + set pending."""
        from unittest.mock import AsyncMock, patch

        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"}, {"name": "國內案例"}, {"name": "國際比較"},
                {"name": "政策建議"}, {"name": "結論"},
            ],
            proposal_markdown=(
                "## 我準備重組為 5 章：\n\n"
                "### 第 1 章：前言\n"
                "### 第 2 章：國內案例\n"
                "### 第 3 章：國際比較\n"
                "### 第 4 章：政策建議\n"
                "### 第 5 章：結論\n"
            ),
        )
        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="adjust",
                operations=[reframe_op],
                summary="整體重組為 5 章",
            )),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state,
                user_message="電力供需太細，先放放；想寫案例比較類型，分成五章",
                auto_continue=False,
            )

        # 不立即 apply — cm 仍是原 3 topics
        cm_unchanged = ContextMap.model_validate_json(result.context_map_json)
        assert len(cm_unchanged.topics) == 3
        # 保持 checkpoint
        assert result.stage_status == "checkpoint"
        # pending_reframe_json 有值
        assert result.pending_reframe_json != ""
        # Bug 2 (2026-05-18) root-fix：reframe proposal 寫到獨立 field，
        # 不再污染 `checkpoint_prompt`（後者保留原 stage prompt）。
        assert "我準備重組為 5 章" in result.pending_reframe_proposal_markdown
        assert "我準備重組為" not in result.checkpoint_prompt
        assert result.checkpoint_prompt == "原始 outline", (
            "Stage 1 entry reframe proposal 不應污染 checkpoint_prompt"
        )

    @pytest.mark.asyncio
    async def test_reframe_round_2_confirm_applies_and_advances(
        self, orchestrator, stage1_state
    ):
        """Round 2：user 回「OK」→ apply reframe + advance to Stage 2。"""
        # 預先 set pending_reframe_json
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"}, {"name": "案例"}, {"name": "結論"},
            ],
        )
        stage1_state.pending_reframe_json = reframe_op.model_dump_json()

        result = await orchestrator._handle_stage_1_response(
            stage1_state, user_message="OK", auto_continue=False,
        )
        # advance
        assert result.stage_status == "completed"
        # pending cleared
        assert result.pending_reframe_json == ""
        # cm 已 mutate
        cm2 = ContextMap.model_validate_json(result.context_map_json)
        names = [t.name for t in cm2.topics]
        assert names == ["前言", "案例", "結論"]

    @pytest.mark.asyncio
    async def test_auto_continue_with_pending_reframe_treats_as_confirm(
        self, orchestrator, stage1_state
    ):
        """#8 regression: pending_reframe_json + auto_continue=True must confirm,
        not fall into adjust path and loop forever.
        """
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"}, {"name": "案例"}, {"name": "結論"},
            ],
        )
        stage1_state.pending_reframe_json = reframe_op.model_dump_json()

        result = await orchestrator._handle_stage_1_response(
            stage1_state, user_message="", auto_continue=True,
        )
        # Must advance — not stay at checkpoint
        assert result.stage_status == "completed", (
            "auto_continue with pending reframe should confirm and advance, "
            "not loop at checkpoint"
        )
        # pending must be cleared
        assert result.pending_reframe_json == ""
        # ContextMap must be updated to the reframe structure
        cm = ContextMap.model_validate_json(result.context_map_json)
        names = [t.name for t in cm.topics]
        assert names == ["前言", "案例", "結論"]

    @pytest.mark.asyncio
    async def test_no_pending_auto_continue_still_advances_without_applying_reframe(
        self, orchestrator, stage1_state
    ):
        """§4.3.6 regression: no pending + auto_continue must advance directly
        (no silent apply of LLM-generated ops). This is the existing behaviour
        — verify it is not broken by the #8 fix.
        """
        # No pending set
        assert stage1_state.pending_reframe_json == ""

        result = await orchestrator._handle_stage_1_response(
            stage1_state, user_message="", auto_continue=True,
        )
        assert result.stage_status == "completed"
        # ContextMap unchanged (no reframe applied)
        cm = ContextMap.model_validate_json(result.context_map_json)
        assert len(cm.topics) == 3  # original fixture has 3 topics

    @pytest.mark.asyncio
    async def test_reframe_apply_syncs_format_specs_chapters(
        self, orchestrator, stage1_state
    ):
        """P0-3 fix (2026-05-19, spec §4.7.6 reframe → writer 接線):

        reframe apply 後 state.format_specs["chapters"] 必須同步成 user 指定的
        N 章。沒這個，_resolve_chapter_source 走 core_topics fallback → writer 寫
        舊 ContextMap N 章而非 user reframe 的 N 章（v15 Cayenne real persona E2E P0-3）。
        """
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言", "description": "研究背景"},
                {"name": "國內案例", "description": "台灣"},
                {"name": "國外案例", "description": "德日韓"},
                {"name": "結果與討論", "description": "比較"},
                {"name": "結論", "description": "建議"},
            ],
        )
        stage1_state.pending_reframe_json = reframe_op.model_dump_json()

        result = await orchestrator._handle_stage_1_response(
            stage1_state, user_message="OK", auto_continue=False,
        )

        # P0-3 acceptance：format_specs["chapters"] 同步 5 章
        assert result.format_specs is not None
        chapters = result.format_specs.get("chapters", [])
        assert len(chapters) == 5, \
            f"reframe 5 章但 format_specs.chapters={len(chapters)} (P0-3 regression)"

        names = [c.get("name") for c in chapters]
        assert names == ["前言", "國內案例", "國外案例", "結果與討論", "結論"], \
            f"chapter names 不對齊 reframe op: {names}"

        # cm.topics 也應該同步（既有行為）
        cm = ContextMap.model_validate_json(result.context_map_json)
        cm_names = [t.name for t in cm.topics]
        assert cm_names == names, \
            f"cm.topics 與 format_specs.chapters 不一致: cm={cm_names} fmt={names}"

    @pytest.mark.asyncio
    async def test_reframe_round_2_cancel_clears_pending(
        self, orchestrator, stage1_state
    ):
        """Round 2：user 回「取消」→ clear pending + 保持 checkpoint。"""
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "A"}, {"name": "B"}],
        )
        stage1_state.pending_reframe_json = reframe_op.model_dump_json()

        # cancel 偵測走 _classify_confirmation_intent（真 LLM）；斷 key 後 pin cancel 不打真 LLM。
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="cancel"),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state, user_message="取消", auto_continue=False,
            )
        assert result.pending_reframe_json == ""
        # cm 不變
        cm = ContextMap.model_validate_json(result.context_map_json)
        assert len(cm.topics) == 3

    @pytest.mark.asyncio
    async def test_reframe_round_2_adjust_keeps_pending_and_re_emits_checkpoint(
        self, orchestrator, stage1_state
    ):
        """Round 2：user 回新訴求（不是 confirm/cancel）→ 保留 pending + re-emit checkpoint。

        P0-2 fix (2026-05-19, spec §4.3.6 adjust path 不可 silent advance):
        - 不可 silent recursive call（舊行為），會 silent advance Stage 2
        - 不可 narration 引用 LLM-generated 數字當 user 訴求
        - 應該保留 pending，emit narration 引導 user 明示 OK / 取消 / 重給結構，
          re-emit pending reframe checkpoint
        """
        from unittest.mock import AsyncMock, patch

        reframe_op_old = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "A"}, {"name": "B"}],
            proposal_markdown="## 我準備重組為 2 章：A / B",
        )
        stage1_state.pending_reframe_json = reframe_op_old.model_dump_json()
        stage1_state.pending_reframe_proposal_markdown = reframe_op_old.proposal_markdown
        stage1_state.current_stage = 1

        # Spy _parse_stage_1_intent — 新行為不應該 call 它（不 recursive）
        parse_spy = AsyncMock()
        with patch.object(orchestrator, "_parse_stage_1_intent", new=parse_spy):
            result = await orchestrator._handle_pending_reframe(
                stage1_state, user_message="德日韓", target_stage=1,
            )

        # P0-2 fix 紀律 1：pending 保留（不 clear）
        assert result.pending_reframe_json == reframe_op_old.model_dump_json(), \
            "pending_reframe_json 不應被 adjust path 清除"
        new_pending_op = ContextMapRevisionOperation.model_validate_json(
            result.pending_reframe_json
        )
        assert len(new_pending_op.new_chapters) == 2, \
            "pending 內容應保持原 2 章不變"

        # P0-2 fix 紀律 2：沒 recursive call _parse_stage_1_intent
        parse_spy.assert_not_called()

        # P0-2 fix 紀律 3：emit narration 含選項引導（不引用 LLM-generated 章節數字）
        sent_messages = [
            call.args[0]
            for call in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert any("OK" in t or "確認" in t for t in narration_texts), \
            "narration 應引導 user 回覆 OK / 確認"
        assert any("取消" in t for t in narration_texts), \
            "narration 應提供取消選項"
        # 紀律：不該引用 LLM-generated 數字（「2 章」「3 章」之類字眼當作 user 訴求）
        for t in narration_texts:
            assert "2 章重組訴求" not in t, \
                "narration 不可引用 LLM-generated chapter_count（v15 P0-2 sub-bug a）"

        # P0-2 fix 紀律 4：re-emit reframe checkpoint
        checkpoint_messages = [
            m for m in sent_messages
            if m.get("message_type") == "live_research_checkpoint"
        ]
        assert checkpoint_messages, "應 re-emit reframe checkpoint"


class TestStage1ReframeConfirmAcknowledgeNarration:
    """LR Stage 1 reframe confirm path acknowledge narration（2026-05-16 bug fix）。

    Bug：CEO E2E 看到 Stage 1 reframe confirm 後 silent jump to Stage 2，user
    回覆「我想最後架構就是前言、國內案例...看起來行嗎？」之後，系統 silently
    advance，中間沒任何 narration acknowledge user。對比 Stage 4 entry confirm
    有 emit「結構已重組（...）」narration，Stage 1 vs Stage 4 設計不對稱。

    Fix：Stage 1 entry confirm path 在 `state.complete_stage()` 前加
    `_emit_narration` 含章節 list + 「接下來進入下一階段」cue。
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage1_state_with_pending_reframe(self):
        cm = _make_cm_with_evidence()
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "引言", "description": "案例比較研究背景"},
                {"name": "國內案例", "description": "台灣案例"},
                {"name": "國外案例", "description": "國際對照"},
                {"name": "結果與討論", "description": "比較分析"},
                {"name": "結論", "description": "總結建議"},
            ],
            new_research_question="台灣能源政策案例比較",
            rationale="user 提案 5 章學術結構",
        )
        return LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
            checkpoint_prompt="原始 outline",
            pending_reframe_json=reframe_op.model_dump_json(),
        )

    def _narration_texts(self, orchestrator):
        """Capture all narration text emitted via message_sender."""
        sent_messages = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        return [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]

    @pytest.mark.asyncio
    async def test_reframe_confirm_stage1_entry_emits_acknowledge_narration(
        self, orchestrator, stage1_state_with_pending_reframe,
    ):
        """Stage 1 entry reframe confirm 必須 emit narration（含章節 list + 章節數）+ advance。"""
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="confirm"),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state_with_pending_reframe,
                user_message="OK", auto_continue=False,
            )

        # 既有 behavior：state advance + pending cleared
        assert result.pending_reframe_json == ""
        assert result.stage_status == "completed"

        # 新 behavior：必須 emit acknowledge narration
        texts = self._narration_texts(orchestrator)
        assert len(texts) >= 1, "Stage 1 entry confirm 必須至少 emit 1 條 narration"
        # 取最後一條 narration（acknowledge 應為 emit 在 complete_stage 之前的最後一條）
        last = texts[-1]
        # 章節數提示（5 章 / 五章 任一即可）
        assert "5 章" in last or "五章" in last, \
            f"narration 必須提到 5 章 / 五章，實際內容：{last!r}"
        # 至少提到 >=3 章節名（user 提的 5 章）
        chapter_mentions = sum(
            1 for c in ["引言", "國內案例", "國外案例", "結果與討論", "結論"]
            if c in last
        )
        assert chapter_mentions >= 3, \
            f"narration 必須提到 >=3 章節名，實際提到 {chapter_mentions} 個。內容：{last!r}"

    @pytest.mark.asyncio
    async def test_ceo_case_repeat_chapter_list_with_question_advances_with_narration(
        self, orchestrator, stage1_state_with_pending_reframe,
    ):
        """CEO E2E 真實 case：user reply 重複 5 章 list +「看起來行嗎？」→
        classifier 判 confirm → 必須 emit narration acknowledge + advance Stage 2。
        """
        from unittest.mock import AsyncMock, patch
        ceo_reply = (
            "我想最後架構就是前言、國內案例、國外案例、結果與討論、結論這五章。"
            "看起來行嗎？"
        )
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="confirm"),
        ):
            result = await orchestrator._handle_stage_1_response(
                stage1_state_with_pending_reframe,
                user_message=ceo_reply, auto_continue=False,
            )

        # advance to Stage 2
        assert result.stage_status == "completed", \
            f"CEO 真實 case 應 advance，但 stage_status={result.stage_status}"
        assert result.pending_reframe_json == ""

        # 必須 emit acknowledge narration（不可 silent jump）
        texts = self._narration_texts(orchestrator)
        assert len(texts) >= 1, \
            "CEO case：silent jump 是 bug — 必須 emit acknowledge narration"
        last = texts[-1]
        # 章節數提示
        assert "5 章" in last or "五章" in last, \
            f"narration 必須提到章節數，實際：{last!r}"

    @pytest.mark.asyncio
    async def test_reframe_confirm_stage4_entry_keeps_existing_narration(self):
        """Regression：target_stage=4 confirm path 既有 narration「結構已重組」不能被誤刪。"""
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator

        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orchestrator = LiveResearchOrchestrator(handler=handler, dry_run=False)

        cm = _make_cm_with_evidence()
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "前言"}, {"name": "結論"}],
        )
        stage4_state = LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
            pending_reframe_json=reframe_op.model_dump_json(),
        )

        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="confirm"),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state, user_message="OK", auto_continue=False,
            )

        # Stage 4 entry confirm：保持 Stage 4
        assert result.current_stage == 4
        assert result.pending_reframe_json == ""

        # 既有 Stage 4 narration「結構已重組」必須仍存在
        sent_messages = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert any("結構已重組" in t for t in narration_texts), \
            "Stage 4 entry confirm 既有「結構已重組」narration 不可被誤刪"


class TestStage4ReframeEntry:
    """UX-9 Phase 3 Task 3.2：Stage 4 reframe entry (D-7) 測試。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage4_state(self):
        cm = _make_cm_with_evidence()
        return LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
        )

    @pytest.mark.asyncio
    async def test_stage_4_structure_change_triggers_reframe_entry(
        self, orchestrator, stage4_state
    ):
        """TypeAgent: new_structure_request → typed reframe entry, pending_reframe_json set."""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, ChapterSpec,
        )

        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[ChapterSpec(name="前言"), ChapterSpec(name="結論")],
                    summary="兩章",
                ),
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state, user_message="改成 2 章：前言、結論", auto_continue=False,
            )

        # state.current_stage 保持 4
        assert result.current_stage == 4
        # pending_reframe_json 有值
        assert result.pending_reframe_json != ""
        # 不 emit「請點 Stage 1」（既有 UX-7 redirect 已取代）
        sent_messages = [
            c.args[0] for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert not any("請點上方 Stage 1" in t for t in narration_texts), \
            "Stage 4 reframe entry 不應再 emit「請點 Stage 1」redirect"

    @pytest.mark.asyncio
    async def test_stage_4_pending_reframe_confirm_keeps_stage_4(
        self, orchestrator, stage4_state
    ):
        """Stage 4 reframe pending + user confirm → apply reframe，state.current_stage 仍為 4。"""
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "前言"}, {"name": "結論"}],
        )
        stage4_state.pending_reframe_json = reframe_op.model_dump_json()
        stage4_state.checkpoint_prompt = "請告訴我格式偏好"

        result = await orchestrator._handle_stage_4_response(
            stage4_state, user_message="OK", auto_continue=False,
        )
        # Stage 4 entry confirm → reframe 套完但保持 Stage 4
        assert result.current_stage == 4
        assert result.pending_reframe_json == ""
        # cm 已 mutate
        cm = ContextMap.model_validate_json(result.context_map_json)
        names = [t.name for t in cm.topics]
        assert names == ["前言", "結論"]

    @pytest.mark.asyncio
    async def test_stage_4_mixed_records_format_and_triggers_reframe(
        self, orchestrator, stage4_state
    ):
        """TypeAgent: new_structure_request + format_content → reframe + 記 format。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, Stage4FormatPayload, ChapterSpec,
        )

        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="A"), ChapterSpec(name="B"), ChapterSpec(name="C"),
                    ],
                    summary="3 章",
                ),
                format_content=Stage4FormatPayload(
                    format_spec_extracted="每段 500 字",
                ),
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state,
                user_message="改成 3 章 A / B / C，每段 500 字",
                auto_continue=False,
            )

        # format_specs 已記下
        assert result.format_specs.get("user_specified") == "每段 500 字"
        # pending_format_confirmation set
        assert result.pending_format_confirmation is True
        # pending_reframe_json 也 set（兩個 pending 獨立並存）
        assert result.pending_reframe_json != ""
        # state.current_stage 仍為 4
        assert result.current_stage == 4

    @pytest.mark.asyncio
    async def test_stage_4_unclear_action_stays_at_checkpoint(
        self, orchestrator, stage4_state
    ):
        """TypeAgent: classifier 回 unclear → narration + 保持 Stage 4 checkpoint。

        取代舊「LLM 沒解出 reframe → Stage 1 fallback narration」legacy test —
        TypeAgent strict mode 沒 fallback path（OQ-2 紀律），LLM 模糊就回 unclear。
        """
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.unclear,
                clarifying_question="想請你具體說明 — 要幾章、每章標題是什麼？",
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state, user_message="想刪一些章節", auto_continue=False,
            )
        # 保持 Stage 4 checkpoint，no pending reframe
        assert result.current_stage == 4
        assert result.pending_reframe_json == ""
        # cm 不變
        cm = ContextMap.model_validate_json(result.context_map_json)
        assert len(cm.topics) == 3

    @pytest.mark.asyncio
    async def test_stage_4_legacy_fallback_intent_none_emits_system_unavailable(
        self, orchestrator, stage4_state
    ):
        """#20 改善：Stage 4 reframe entry legacy fallback（_parse_stage_1_intent 回 None
        = LLM API 失敗）→ narration 該說「系統暫時無法處理」，不該怪 user「我沒看懂你的結構訴求」。
        保持 Stage 4 checkpoint。"""
        from unittest.mock import AsyncMock, patch

        with patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=None),  # LLM API fail
        ):
            # legacy caller：stage4_intent=None → 走 Stage 1 parser fallback
            result = await orchestrator._try_stage_4_reframe_entry(
                stage4_state,
                user_message="重新組織章節結構",
                format_spec_extracted="",
                stage4_intent=None,
            )

        assert result.current_stage == 4
        sent_messages = [
            c.args[0] for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert any("系統暫時無法處理" in t for t in narration_texts), \
            f"expect system-unavailable narration, got {narration_texts}"
        assert not any("沒看懂你的結構訴求" in t for t in narration_texts), \
            f"API fail 不該怪 user 結構訴求, got {narration_texts}"


# ============================================================================
# UX-9 Stage1 outline-cue fix: prompt builder regression tests
# ============================================================================


class TestStage1RevisionPromptOutlineCue:
    """Phase 3.1：prompt builder unit tests（不 call LLM）。

    確認 prompt 改動含：
    - D-5 訊號 4「outline 列舉句型」明寫
    - Cayenne R1 原文作為 few-shot input
    - reframe op_type 範例段含完整 input → output 對照
    - line 154-155 衝突指令已收斂為三分支
    """

    @pytest.fixture
    def prompt(self):
        from reasoning.prompts.stage1_revision import Stage1RevisionPromptBuilder
        cm = _make_cm_with_evidence()
        builder = Stage1RevisionPromptBuilder()
        return builder.build_intent_parse_prompt(
            user_message="（測試 placeholder）", context_map=cm,
        )

    def test_prompt_includes_outline_cue_signal_4_heuristic(self, prompt):
        """D-5 heuristic 段含「訊號 4」「outline 列舉句型」字樣，
        且列出 4a/4b/4c 三 sub-pattern。"""
        # 訊號 4 標題
        assert "訊號 4" in prompt or "4. **outline 列舉句型**" in prompt, \
            "D-5 段應該明寫第 4 條訊號"
        assert "outline 列舉句型" in prompt
        # 三 sub-pattern
        assert "連接詞列舉" in prompt or "前面/前言 X，然後/接著 Y" in prompt, \
            "應該含 sub-pattern 4a（連接詞列舉）"
        assert "頓號" in prompt and "列舉" in prompt, \
            "應該含 sub-pattern 4b（頓號 / 逗號列舉）"
        assert "文體宣告" in prompt, \
            "應該含 sub-pattern 4c（文體宣告 + 章節列舉）"

    def test_prompt_includes_cayenne_r1_few_shot_example(self, prompt):
        """reframe_structure op_type 範例段含 Cayenne R1 原文 substring。"""
        # R1 中段最具識別性的片段（避免標點符號差異）
        assert "想寫成案例比較類型的" in prompt, \
            "few-shot Input 應含 Cayenne R1 文體宣告原文"
        assert "前面引言，然後國內外案例比較" in prompt, \
            "few-shot Input 應含 Cayenne R1 outline 列舉原文"
        assert "電力供需" in prompt, \
            "few-shot Input 應含 Cayenne R1 局部排除原文"

    def test_prompt_does_not_double_define_empty_ops_behavior(self, prompt):
        """確認 line 154-155 衝突指令已收斂 — 不再同時出現
        「模糊訴求 → 生成具體 operations」與「看不懂 → operations=[]」並列。

        改寫後應該是三分支結構（路徑 A / 路徑 B / 路徑 C），互斥。
        """
        # 不應再出現舊衝突措辭
        assert not (
            "如果 user 訴求模糊" in prompt and "如果完全看不懂" in prompt
            and "operations=[]" in prompt
            and prompt.count("operations=[]") == 1
            and "路徑" not in prompt
        ), "舊衝突指令（兩條並列）不該還在"
        # 應該出現三分支
        assert "路徑 A" in prompt
        assert "路徑 B" in prompt
        assert "路徑 C" in prompt
        # 三分支說明
        assert "互斥" in prompt, "三分支應明寫互斥性"

    def test_prompt_reframe_op_section_includes_input_output_pair(self, prompt):
        """reframe_structure op_type 範例段含完整 few-shot input + output JSON 對照。"""
        # Few-shot 標記
        assert "Few-shot" in prompt or "Input user_message" in prompt, \
            "應該有 input → output 對照段"
        # Output JSON 範例至少含 5 章 chapter name
        for chapter in ["引言", "國內案例", "國外案例", "討論", "結論"]:
            assert chapter in prompt, \
                f"few-shot Output 應該含「{chapter}」章節"
        # 標明 reframe vs incremental 的 reasoning
        assert "Why reframe_structure 而非 remove+add" in prompt \
            or "整體重組" in prompt, \
            "應該解釋為何選 reframe_structure 而非 incremental ops"
        # proposal_markdown 範例含 D-6 detail-rich 結構
        assert "我準備重組為 5 章" in prompt
        assert "預期內容" in prompt
        assert "包含資料" in prompt


# ============================================================================
# UX-9 Stage1 outline-cue fix: real LLM integration tests
# ============================================================================


class TestStage1RevisionPromptClarification:
    """Prompt 三分支紀律 + clarifying_question 規格(empty-ops clarification dialog plan)。"""

    @pytest.fixture
    def prompt(self):
        from reasoning.prompts.stage1_revision import Stage1RevisionPromptBuilder
        cm = _make_cm_with_evidence()
        builder = Stage1RevisionPromptBuilder()
        return builder.build_intent_parse_prompt(
            user_message="(測試 placeholder)", context_map=cm,
        )

    def test_prompt_includes_three_branch_discipline(self, prompt):
        """Prompt 明確列出三分支 A/B/C 紀律,並涵蓋 clarifying_question 字串。"""
        # 三分支標題
        assert "分支 A" in prompt or "路徑 A" in prompt
        assert "分支 B" in prompt or "路徑 B" in prompt
        assert "分支 C" in prompt or "路徑 C" in prompt
        # clarifying_question 字串必須出現(以前沒有)
        assert "clarifying_question" in prompt, \
            "prompt 必須出現 clarifying_question 欄位名稱"

    def test_prompt_describes_clarifying_question_as_zh_question(self, prompt):
        """提示 LLM clarifying_question 必須是繁體中文問句。"""
        # 在 clarifying_question 規格附近要含「繁體中文」+「問句」
        assert "繁體中文" in prompt
        assert "問句" in prompt

    def test_prompt_forbids_fixed_example_fallback(self, prompt):
        """LLM 不應該回固定例句(『把第 1 章合併』之類),而是針對 user reply 具體追問。"""
        # prompt 要明確要求「針對 user 剛剛說的內容」或「具體追問」
        assert "針對" in prompt and ("具體追問" in prompt or "具體" in prompt), \
            "prompt 應該要求問句針對 user reply 具體追問"
        # prompt 要禁止「複製固定例句」or 類似禁令
        assert "複製" in prompt or "禁止" in prompt or "不要複製" in prompt or "不要照抄" in prompt, \
            "prompt 應該明文禁止複製固定例句"

    def test_prompt_clarifying_question_required_only_in_branch_c(self, prompt):
        """分支 A/B clarifying_question 留空,分支 C 必填。"""
        # 整段 prompt 應該明寫「分支 C 必填」或「分支 A/B 留空」
        assert "分支 C 必填" in prompt or "C 必填" in prompt or "分支 C 時填" in prompt
        assert "留空" in prompt or "空字串" in prompt


class TestStage1OutlineCueLLMIntegration:
    """Phase 3.2：Cayenne R1 / R3 真實 LLM parse 行為驗證。

    這兩個測試需要真實 LLM key（OpenAI / Anthropic / Gemini）。
    沒有 key 時 `ask_llm` 會 raise / return None，
    `_parse_stage_1_intent` 回 None，assert 會 fail。
    平時 pytest 跑 `-m llm` 才會觸發，CI 沒 LLM key 時用 `-m "not llm"` skip。
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def initial_cm(self):
        """模擬 Cayenne fixture 初始 17 topics 縮版（取 5 條代表）。"""
        return ContextMap(
            research_question="台灣半導體業電力供需與綠電轉型挑戰",
            topics=[
                ContextMapTopic(
                    topic_id="t1", name="電力供需缺口", domain="能源",
                    relevance="core",
                    description="台灣電力供需現況與半導體用電壓力",
                ),
                ContextMapTopic(
                    topic_id="t2", name="太陽能成本",
                    domain="能源", relevance="core",
                    description="太陽能 LCOE 與成本下降趨勢",
                ),
                ContextMapTopic(
                    topic_id="t3", name="綠電憑證",
                    domain="制度", relevance="supporting",
                    description="T-REC 制度設計與買賣機制",
                ),
                ContextMapTopic(
                    topic_id="t4", name="半導體業 RE100",
                    domain="產業", relevance="core",
                    description="台積電 / 聯電 RE100 承諾與進度",
                ),
                ContextMapTopic(
                    topic_id="t5", name="儲能技術",
                    domain="技術", relevance="supporting",
                    description="鋰電池 / 抽蓄儲能技術現況",
                ),
            ],
            relations=[],
            version=0,
        )

    @_skip_if_no_real_llm
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_cayenne_r1_vague_outline_triggers_reframe(
        self, orchestrator, initial_cm,
    ):
        """Cayenne R1 vague outline reply → LLM 應選 reframe_structure。

        Trigger 訊號 4a（前面 X，然後 Y，結尾 Z）+ 4c（文體宣告）。
        """
        user_message = (
            "我其實想寫成案例比較類型的，前面引言，然後國內外案例比較，"
            "結尾討論結論這樣。電力供需跟半導體那塊太細，我們先放放。"
        )
        intent = await orchestrator._parse_stage_1_intent(
            user_message=user_message,
            context_map=initial_cm,
        )
        assert intent is not None, "LLM 應 parse 成功（非 None fallback）"
        assert intent.action == "adjust", \
            f"R1 outline reply 應 → adjust，實際 {intent.action}"
        assert len(intent.operations) >= 1, "至少要有一個 operation"
        # 第一個 op 應為 reframe_structure
        first_op = intent.operations[0]
        assert first_op.op_type == "reframe_structure", \
            f"R1 應選 reframe_structure，實際 {first_op.op_type}"
        # 新章節 ≥ 3 章（user 至少列了 引言 + 案例比較 + 討論 + 結論）
        assert len(first_op.new_chapters) >= 3, \
            f"R1 至少 3 章，實際 {len(first_op.new_chapters)}"

    @_skip_if_no_real_llm
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_cayenne_r3_explicit_outline_triggers_reframe(
        self, orchestrator, initial_cm,
    ):
        """Cayenne R3 explicit outline → 必選 reframe_structure，且 5 章。

        Trigger 訊號 4b（頓號列舉 5 章名 + 「這五章」收斂語）。
        """
        user_message = "前言、國內案例、國外案例、結果討論、結論這五章"
        intent = await orchestrator._parse_stage_1_intent(
            user_message=user_message,
            context_map=initial_cm,
        )
        assert intent is not None, "LLM 應 parse 成功（非 None fallback）"
        assert intent.action == "adjust", \
            f"R3 explicit outline 應 → adjust，實際 {intent.action}"
        assert len(intent.operations) >= 1
        first_op = intent.operations[0]
        assert first_op.op_type == "reframe_structure", \
            f"R3 應選 reframe_structure，實際 {first_op.op_type}"
        assert len(first_op.new_chapters) == 5, \
            f"R3 explicit 5 章，實際 {len(first_op.new_chapters)}"


# ============================================================================
# LR Stage 4 confirm path fix R1 — LLM-based confirmation intent classifier
# ============================================================================


class TestClassifyConfirmationIntentR1:
    """R1：LLM-based confirmation intent classifier。

    用 LLM 取代 keyword exact-match — `_looks_like_confirmation("OK 就這樣")`
    從嚴 strip == keyword 過嚴，不認多種人類自然 confirm 句型。

    helper 簽名：
        await orch._classify_confirmation_intent(user_message) → "confirm" | "cancel" | "adjust"
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phrase", [
        "OK 就這樣",
        "就這樣",
        "OK 確認",
        "沒問題就這樣",
        "好的，這樣可以",
        "嗯，可以這樣",
        "OK",
        "好",
        "確認",
        "Sure，這版好",
    ])
    async def test_confirm_phrases_classified_as_confirm(
        self, orchestrator, phrase,
    ):
        """多種 confirm 句型應 → "confirm"。"""
        from unittest.mock import AsyncMock, patch
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value={"intent": "confirm"}),
        ):
            result = await orchestrator._classify_confirmation_intent(phrase)
        assert result == "confirm", f"phrase {phrase!r} 應 → confirm, 實際 {result!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phrase", [
        "取消",
        "算了",
        "不要重組",
        "再想想",
        "先不要",
    ])
    async def test_cancel_phrases_classified_as_cancel(
        self, orchestrator, phrase,
    ):
        """cancel 句型應 → "cancel"。"""
        from unittest.mock import AsyncMock, patch
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value={"intent": "cancel"}),
        ):
            result = await orchestrator._classify_confirmation_intent(phrase)
        assert result == "cancel", f"phrase {phrase!r} 應 → cancel, 實際 {result!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phrase", [
        "改成 3 章 X / Y / Z",
        "把第 1 章拿掉",
        "再多一章談國際",
        "每章 800 字、APA 引用",
    ])
    async def test_adjust_phrases_classified_as_adjust(
        self, orchestrator, phrase,
    ):
        """新訴求句型應 → "adjust"。"""
        from unittest.mock import AsyncMock, patch
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(return_value={"intent": "adjust"}),
        ):
            result = await orchestrator._classify_confirmation_intent(phrase)
        assert result == "adjust", f"phrase {phrase!r} 應 → adjust, 實際 {result!r}"

    @pytest.mark.asyncio
    async def test_dry_run_short_confirm_fallback(self, orchestrator):
        """dry_run 模式下，short OK 直接 confirm 不打 LLM（加速 unit test）。"""
        orchestrator.dry_run = True
        from unittest.mock import AsyncMock, patch
        ask_llm_mock = AsyncMock(return_value={"intent": "confirm"})
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=ask_llm_mock,
        ):
            result = await orchestrator._classify_confirmation_intent("OK")
        assert result == "confirm"
        assert ask_llm_mock.call_count == 0, "dry_run 短訊息不應打 LLM"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_adjust_safe_default(self, orchestrator):
        """LLM 失敗 / empty → adjust（safe default，避免誤套用）。"""
        from unittest.mock import AsyncMock, patch
        with patch(
            "reasoning.live_research.orchestrator.ask_llm",
            new=AsyncMock(side_effect=Exception("LLM dead")),
        ):
            result = await orchestrator._classify_confirmation_intent("OK 就這樣")
        assert result == "adjust"


class TestStage4ConfirmRoundR1Integration:
    """R1 整合測試：_handle_stage_4_response 對 pending_reframe + 多種 confirm 句型應 PASS。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage4_state_with_pending_reframe(self):
        cm = _make_cm()
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"}, {"name": "案例"}, {"name": "結論"},
            ],
        )
        return LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
            pending_reframe_json=reframe_op.model_dump_json(),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_reply", [
        "OK 就這樣",
        "就這樣",
        "OK 確認",
        "沒問題就這樣",
    ])
    async def test_complex_confirm_phrases_apply_reframe(
        self, orchestrator, stage4_state_with_pending_reframe, user_reply,
    ):
        """複合 confirm 句型應 → apply reframe，cm.topics 變 3 章。"""
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="confirm"),
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state_with_pending_reframe,
                user_message=user_reply, auto_continue=False,
            )
        cm2 = ContextMap.model_validate_json(result.context_map_json)
        names = [t.name for t in cm2.topics]
        assert names == ["前言", "案例", "結論"], \
            f"reply {user_reply!r} 應 apply reframe，但 names={names}"
        assert result.pending_reframe_json == ""


# ============================================================================
# LR Stage 4 confirm path fix R2 — adjust path narration（不可 silent drop）
# ============================================================================


class TestReframeAdjustPathR2:
    """R2：adjust path silent drop reframe — clear pending 前必須 emit narration。

    違反 CLAUDE.md「不可 silent fail」紀律 — user reframe 訴求被丟掉時必須
    有可觀察訊息流。
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage4_state_with_pending(self):
        cm = _make_cm()
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "A"}, {"name": "B"}],
        )
        return LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
            pending_reframe_json=reframe_op.model_dump_json(),
        )

    @pytest.mark.asyncio
    async def test_adjust_emits_narration_before_clearing_pending(
        self, orchestrator, stage4_state_with_pending,
    ):
        """user 給新訴求（不是 confirm/cancel）→ 必 emit narration 明示「先放下你剛才的 reframe 訴求」。"""
        from unittest.mock import AsyncMock, patch
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="adjust"),
        ):
            await orchestrator._handle_stage_4_response(
                stage4_state_with_pending,
                user_message="再改一下章節",
                auto_continue=False,
            )
        sent_messages = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        # 必須 emit 至少一則 narration 提到 reframe / 重組 / 結構 + 取消 / 放下
        assert any(
            ("重組" in t or "reframe" in t.lower() or "結構" in t)
            and ("取消" in t or "放下" in t or "先不" in t or "已" in t)
            for t in narration_texts
        ), (
            f"adjust path 必須 emit narration 告知 user reframe 訴求被放下，"
            f"實際 narration={narration_texts}"
        )

    @pytest.mark.asyncio
    async def test_stage1_adjust_emits_narration_before_clearing_pending(
        self, orchestrator,
    ):
        """同 Stage 1 entry：adjust path 也必 emit narration。"""
        from unittest.mock import AsyncMock, patch
        cm = _make_cm()
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "A"}, {"name": "B"}],
        )
        state = LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
            checkpoint_prompt="原 outline",
            pending_reframe_json=reframe_op.model_dump_json(),
        )
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="adjust"),
        ), patch.object(
            orchestrator, "_parse_stage_1_intent",
            new=AsyncMock(return_value=Stage1ParsedIntent(
                action="confirm", summary="新訴求"
            )),
        ):
            await orchestrator._handle_stage_1_response(
                state, user_message="改一下吧", auto_continue=False,
            )
        sent_messages = [
            c.args[0]
            for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        narration_texts = [
            m.get("text", "") for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert any(
            ("重組" in t or "reframe" in t.lower() or "結構" in t)
            and ("取消" in t or "放下" in t or "先不" in t or "已" in t)
            for t in narration_texts
        ), f"Stage 1 adjust path 也必 emit narration, 實際 {narration_texts}"


# ============================================================================
# LR Stage 4 confirm path fix R3 — format_specs merge 不 overwrite
# ============================================================================


class TestFormatSpecsMergeR3:
    """R3：format_specs 不可無條件 overwrite — 既有 chapters / user_specified 要保留。

    Root cause of v3 Fail 2：「OK 就這樣」被當 auto_continue 蓋寫整個
    format_specs，wipe Plan 2 Phase 4 fallback 寫入的 chapters override，
    導致 writer 跑 fallback core_topics（9 章）而非 user 指定的 5 章。
    """

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    def _state_with_chapters(self, chapters):
        cm = _make_cm()
        return LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
            format_specs={"chapters": chapters},
        )

    @pytest.mark.asyncio
    async def test_auto_continue_preserves_existing_chapters(
        self, orchestrator,
    ):
        """auto_continue path（OK 就這樣 → auto_continue intent）不可 wipe 既有 chapters。"""
        chapters = [
            {"name": "前言", "outline": ""},
            {"name": "國內案例", "outline": ""},
            {"name": "國外案例", "outline": ""},
            {"name": "結果與討論", "outline": ""},
            {"name": "結論", "outline": ""},
        ]
        state = self._state_with_chapters(chapters)
        result = await orchestrator._handle_stage_4_response(
            state, user_message="", auto_continue=True,
        )
        # default 應該被 set，chapters 應保留
        assert result.format_specs.get("default") == "markdown_apa"
        assert result.format_specs.get("chapters") == chapters, \
            f"既有 chapters 必須保留, 實際 {result.format_specs}"

    @pytest.mark.asyncio
    async def test_auto_continue_intent_preserves_existing_chapters(
        self, orchestrator,
    ):
        """TypeAgent: auto_continue action 必須保留 chapters。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

        chapters = [{"name": f"章{i}", "outline": ""} for i in range(5)]
        state = self._state_with_chapters(chapters)
        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.auto_continue,
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                state, user_message="OK 就這樣", auto_continue=False,
            )
        assert result.format_specs.get("chapters") == chapters
        assert result.format_specs.get("default") == "markdown_apa"

    @pytest.mark.asyncio
    async def test_format_spec_intent_preserves_existing_chapters(
        self, orchestrator,
    ):
        """TypeAgent: adjust_format 必須保留 chapters。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )

        chapters = [{"name": "A", "outline": ""}, {"name": "B", "outline": ""}]
        state = self._state_with_chapters(chapters)
        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="APA 引用、每段 500 字",
                ),
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                state, user_message="APA 引用、每段 500 字", auto_continue=False,
            )
        assert result.format_specs.get("chapters") == chapters
        assert "user_specified" in result.format_specs

    @pytest.mark.asyncio
    async def test_unclear_action_preserves_existing_chapters(
        self, orchestrator,
    ):
        """TypeAgent: unclear action 不 mutate format_specs（chapters 保留）。"""
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

        chapters = [{"name": "X", "outline": ""}]
        state = self._state_with_chapters(chapters)
        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.unclear,
                clarifying_question="可以再具體說明嗎？",
            )),
        ):
            result = await orchestrator._handle_stage_4_response(
                state, user_message="random", auto_continue=False,
            )
        assert result.format_specs.get("chapters") == chapters

    @pytest.mark.asyncio
    async def test_empty_format_specs_still_gets_default(
        self, orchestrator,
    ):
        """既有 format_specs 完全空時，auto_continue 仍要 set default（不破壞 happy path）。"""
        cm = _make_cm()
        state = LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
            format_specs={},
        )
        result = await orchestrator._handle_stage_4_response(
            state, user_message="", auto_continue=True,
        )
        assert result.format_specs.get("default") == "markdown_apa"

    def test_merge_default_helper(self):
        """_merge_format_specs_default：set default 但保留所有既有 keys。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        existing = {"chapters": [{"name": "X"}], "user_specified": "APA"}
        merged = LiveResearchOrchestrator._merge_format_specs_default(existing)
        assert merged["default"] == "markdown_apa"
        assert merged["chapters"] == [{"name": "X"}]
        assert merged["user_specified"] == "APA"

    def test_merge_default_does_not_overwrite_existing_default(self):
        """既有 default 不被 overwrite（setdefault semantics）。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        existing = {"default": "custom_value", "chapters": []}
        merged = LiveResearchOrchestrator._merge_format_specs_default(existing)
        assert merged["default"] == "custom_value"

    def test_merge_user_helper(self):
        """_merge_format_specs_user：set user_specified 但保留 chapters / default。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        existing = {"chapters": [{"name": "X"}], "default": "markdown_apa"}
        merged = LiveResearchOrchestrator._merge_format_specs_user(existing, "APA + 5 章")
        assert merged["chapters"] == [{"name": "X"}]
        assert merged["default"] == "markdown_apa"
        assert merged["user_specified"] == "APA + 5 章"

    def test_merge_helpers_handle_none(self):
        """existing=None 不會崩。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        assert LiveResearchOrchestrator._merge_format_specs_default(None) == \
            {"default": "markdown_apa"}
        assert LiveResearchOrchestrator._merge_format_specs_user(None, "x") == \
            {"user_specified": "x"}


# ============================================================================
# Bug 2 (2026-05-18) root-fix regression：
# reframe proposal 必須與 checkpoint_prompt 解耦，避免 Stage 4 entry confirm path
# re-emit「污染後的 checkpoint_prompt」造成 looping。
# 根解：新增獨立 state field `pending_reframe_proposal_markdown`；
# `_emit_reframe_proposal` 寫獨立 field、不再 mutate `checkpoint_prompt`。
# ============================================================================


class TestReframeProposalDecoupledFromCheckpointPrompt:
    """Bug 2 root-fix：reframe proposal markdown 不應寫進 `checkpoint_prompt`。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.mark.asyncio
    async def test_emit_reframe_proposal_does_not_overwrite_checkpoint_prompt(
        self, orchestrator
    ):
        """`_emit_reframe_proposal` 之後 `state.checkpoint_prompt` 必須保持不變。

        防 regression：以前 `_emit_reframe_proposal` 透過 `set_checkpoint(proposal)`
        把 reframe markdown 寫進 `checkpoint_prompt`，污染原 stage checkpoint。
        """
        cm = _make_cm_with_evidence()
        original_checkpoint = "原始 Stage 4 格式偏好詢問 prompt"
        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            checkpoint_prompt=original_checkpoint,
            context_map_json=cm.model_dump_json(),
        )
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "前言"}, {"name": "案例"}, {"name": "結論"},
            ],
            proposal_markdown="## 我準備重組為 3 章：\n\n### 第 1 章：前言",
        )
        await orchestrator._emit_reframe_proposal(
            state, reframe_op, cm, summary="重組 3 章", target_stage=4,
        )
        # 核心 assertion：原 checkpoint_prompt 必須完整保留
        assert state.checkpoint_prompt == original_checkpoint, (
            "reframe proposal 不可污染 checkpoint_prompt — 必須走獨立 field"
        )
        # pending_reframe_json 有寫入
        assert state.pending_reframe_json != ""
        # 新獨立 field 存了 proposal markdown
        assert "我準備重組為 3 章" in state.pending_reframe_proposal_markdown

    @pytest.mark.asyncio
    async def test_stage_4_entry_confirm_emits_original_format_checkpoint(
        self, orchestrator
    ):
        """Stage 4 entry reframe confirm 之後，re-emit 的 checkpoint proposal
        必須是**原** Stage 4 格式詢問 prompt（而不是 reframe proposal）。"""
        cm = _make_cm_with_evidence()
        original_format_prompt = "請告訴我格式偏好（表格／列表／APA 等）"
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": "前言"}, {"name": "結論"}],
        )
        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            checkpoint_prompt=original_format_prompt,
            context_map_json=cm.model_dump_json(),
            pending_reframe_json=reframe_op.model_dump_json(),
            pending_reframe_proposal_markdown="## 我準備重組為 2 章 ...（污染來源測試）",
        )
        result = await orchestrator._handle_stage_4_response(
            state, user_message="OK", auto_continue=False,
        )
        # Stage 4 confirm path emit 的 checkpoint proposal 應為原 format prompt
        sent_messages = [
            c.args[0] for c in orchestrator.handler.message_sender.send_message.call_args_list
        ]
        checkpoint_msgs = [
            m for m in sent_messages
            if m.get("message_type") == "live_research_checkpoint"
        ]
        # 取最後一個 checkpoint event（confirm path re-emit 的那個）
        assert len(checkpoint_msgs) >= 1
        last_checkpoint_proposal = checkpoint_msgs[-1].get("proposal", "")
        assert original_format_prompt in last_checkpoint_proposal, (
            f"Stage 4 entry confirm 後 re-emit 的 checkpoint 應為原 format prompt，"
            f"實際 emit={last_checkpoint_proposal!r}"
        )
        assert "我準備重組為" not in last_checkpoint_proposal, (
            "Stage 4 entry confirm 後 emit 的 checkpoint 不應為 reframe proposal"
        )
        # confirm 後兩個 pending field 都清掉
        assert result.pending_reframe_json == ""
        assert result.pending_reframe_proposal_markdown == ""

    def test_state_field_roundtrip(self):
        """新 state field `pending_reframe_proposal_markdown` 必須 to/from_dict 互通。"""
        state = LiveResearchStageState(
            current_stage=4,
            pending_reframe_proposal_markdown="## 我準備重組為 5 章 ...",
        )
        d = state.to_dict()
        assert d["pending_reframe_proposal_markdown"] == "## 我準備重組為 5 章 ..."
        restored = LiveResearchStageState.from_dict(d)
        assert restored.pending_reframe_proposal_markdown == "## 我準備重組為 5 章 ..."
        # 舊 session 沒這欄 → fallback ""
        legacy_dict = {k: v for k, v in d.items() if k != "pending_reframe_proposal_markdown"}
        restored_legacy = LiveResearchStageState.from_dict(legacy_dict)
        assert restored_legacy.pending_reframe_proposal_markdown == ""


# ============================================================================
# Bug 4a (2026-05-18) root-fix regression：
# `ContextMapDelta.added_topics` 必須維持插入順序（不能是 set 差集 → list 化）。
# Chapter order 是 semantic info — reframe 後 narration 必須按 user 拍板的章節
# 順序輸出，而不是 set hash-based 隨機順序。
# ============================================================================


class TestReframeAddedTopicsOrdered:
    """Bug 4a root-fix：reframe 後 `delta.added_topics` 順序 == reframe_op.new_chapters 順序。"""

    def test_apply_reframe_added_topics_preserves_chapter_order(self):
        """reframe 5 章 → `delta.added_topics` 按 reframe_op.new_chapters 順序排列。"""
        from reasoning.live_research.orchestrator import _apply_context_map_revisions

        cm = _make_cm_with_evidence()
        # user 明示順序：前言 → 國內案例 → 國外案例 → 結果與討論 → 結論
        chapter_names_ordered = [
            "前言", "國內案例", "國外案例", "結果與討論", "結論",
        ]
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": n} for n in chapter_names_ordered],
        )
        mutated_cm, delta, warnings = _apply_context_map_revisions(
            cm, [op], "整體重組為 5 章",
        )
        assert mutated_cm is not None
        # added_topics 必須按 cm_working.topics（即 new_chapters）的順序
        # → 用 mutated_cm 反查每個 added_topic 的 name
        post_name_map = {t.topic_id: t.name for t in mutated_cm.topics}
        added_names_in_order = [
            post_name_map.get(tid, "?") for tid in delta.added_topics
        ]
        assert added_names_in_order == chapter_names_ordered, (
            f"delta.added_topics 順序應為 user 拍板章節順序，"
            f"實際={added_names_in_order}，預期={chapter_names_ordered}"
        )

    def test_format_delta_summary_reframe_chapters_in_user_order(self):
        """`_format_delta_summary` reframe 分支輸出順序 == user 拍板章節順序。"""
        from reasoning.live_research.orchestrator import (
            _apply_context_map_revisions, LiveResearchOrchestrator,
        )
        from unittest.mock import MagicMock, patch, AsyncMock

        cm = _make_cm_with_evidence()
        chapter_names_ordered = [
            "前言", "國內案例", "國外案例", "結果與討論", "結論",
        ]
        op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[{"name": n} for n in chapter_names_ordered],
        )
        pre_snapshot = cm
        mutated_cm, delta, _ = _apply_context_map_revisions(
            cm, [op], "整體重組為 5 章",
        )
        post_name_map = {t.topic_id: t.name for t in mutated_cm.topics}
        removed_name_map = {
            t.topic_id: t.name for t in pre_snapshot.topics
            if t.topic_id in delta.removed_topics
        }

        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        summary = orch._format_delta_summary(delta, post_name_map, removed_name_map)
        # 預期 "整體重組為 5 章：前言 / 國內案例 / 國外案例 / 結果與討論 / 結論"
        expected_suffix = " / ".join(chapter_names_ordered)
        assert summary.endswith(expected_suffix), (
            f"_format_delta_summary 應按 user 章節順序輸出，"
            f"實際={summary!r}，預期 suffix={expected_suffix!r}"
        )


# ============================================================================
# Bug 4b (2026-05-18) root-fix regression：
# Stage 4 reframe entry 不應 round-trip 給 Stage 1 prompt 重解。
# Stage 1 prompt 對 special_elements 零知識，會把「最後加比較表」當第 6 章。
# 根解：Stage4Intent 抽出 `new_chapters`，`_try_stage_4_reframe_entry`
# 直接用它構造 reframe op，跳過 Stage 1 parser。
# ============================================================================


class TestStage4ReframeEntryUsesStage4NewChapters:
    """Bug 4b root-fix：Stage 4 reframe entry 用 Stage 4 抽出的 new_chapters，
    不再呼叫 Stage 1 prompt round-trip（避免 special_elements 被誤判為新章）。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=handler, dry_run=False)

    @pytest.fixture
    def stage4_state(self):
        cm = _make_cm_with_evidence()
        return LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            checkpoint_prompt="請告訴我格式偏好",
        )

    @pytest.mark.asyncio
    async def test_stage_4_mixed_with_table_does_not_promote_to_chapter(
        self, orchestrator, stage4_state
    ):
        """E2E case：「五章 + 各章字數 + 最後加比較表 + APA」→ reframe op 只有 5 章，
        「比較表」**不會** 變第 6 章。"""
        from unittest.mock import AsyncMock, patch

        # TypeAgent typed action — structural_content + format_content channels 互斥
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, Stage4FormatPayload,
            SpecialElementSpec, ChapterSpec,
        )
        stage1_spy = AsyncMock(return_value=None)
        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="前言"),
                        ChapterSpec(name="國內案例"),
                        ChapterSpec(name="國外案例"),
                        ChapterSpec(name="結果與討論"),
                        ChapterSpec(name="結論"),
                    ],
                ),
                format_content=Stage4FormatPayload(
                    format_spec_extracted="每章字數 1000 / APA",
                    citation_style_extracted="author_year",
                    special_elements=[
                        SpecialElementSpec(
                            type="table", target_chapter="",
                            description="5 國能源比較",
                        ),
                    ],
                ),
            )),
        ), patch.object(
            orchestrator, "_parse_stage_1_intent", new=stage1_spy,
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state,
                user_message=(
                    "五章：前言、國內案例、國外案例、結果與討論、結論，"
                    "各章 1000 字，最後加 5 國能源比較表，引用用 APA"
                ),
                auto_continue=False,
            )
        # pending_reframe_json 套用 5 章（不是 6 章）
        assert result.pending_reframe_json != ""
        pending_op = ContextMapRevisionOperation.model_validate_json(
            result.pending_reframe_json
        )
        assert len(pending_op.new_chapters) == 5, (
            f"Stage 4 reframe entry 應採 Stage 4 抽出的 5 章（不該因 special_elements 升為 6 章），"
            f"實際 new_chapters={[c.get('name') for c in pending_op.new_chapters]}"
        )
        chapter_names = [c.get("name") for c in pending_op.new_chapters]
        assert "比較表" not in chapter_names, (
            "「比較表」是 special_element，不應升為章節"
        )
        # special_elements 仍進 format_specs
        assert result.format_specs.get("special_elements"), (
            "special_elements 應寫入 format_specs"
        )
        # Stage 1 parser **不再為了重解結構**被呼叫
        # （Stage 4 抽到 new_chapters 就應跳過 round-trip）
        assert stage1_spy.call_count == 0, (
            f"Stage 1 parser 不應被 round-trip 呼叫"
            f"（Stage 4 已抽出 new_chapters），實際呼叫 {stage1_spy.call_count} 次"
        )

    @pytest.mark.asyncio
    async def test_stage_4_structure_change_uses_stage4_chapters(
        self, orchestrator, stage4_state
    ):
        """structure_change 路徑：Stage 4 抽出 new_chapters → 直接 reframe，不過 Stage 1."""
        from unittest.mock import AsyncMock, patch

        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, ChapterSpec,
        )
        stage1_spy = AsyncMock(return_value=None)
        with patch.object(
            orchestrator, "_classify_stage_4_response",
            new=AsyncMock(return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="A"), ChapterSpec(name="B"), ChapterSpec(name="C"),
                    ],
                ),
            )),
        ), patch.object(
            orchestrator, "_parse_stage_1_intent", new=stage1_spy,
        ):
            result = await orchestrator._handle_stage_4_response(
                stage4_state,
                user_message="改成 3 章：A / B / C",
                auto_continue=False,
            )
        assert result.pending_reframe_json != ""
        pending_op = ContextMapRevisionOperation.model_validate_json(
            result.pending_reframe_json
        )
        assert [c.get("name") for c in pending_op.new_chapters] == ["A", "B", "C"]
        assert stage1_spy.call_count == 0


# ============================================================================
# Bug 1 (2026-05-18) root-fix regression：
# Writer prompt + Outline planner prompt 不應 dump `analyst_citations` /
# `evidence_ids` raw list literal — 此 channel 教 LLM 在段末抄「來源：[1] [2] ...」。
# Fix D 只堵了 ContextMap render；本 fix 把 writer / planner 自身的 list literal
# 改成 narrative count form + 加段末禁令紀律。
# ============================================================================


class TestWriterEvidenceDumpForbidden:
    """Bug 1 root-fix：writer prompt 不應含 raw analyst_citations list literal，
    並必須含「禁段末 dump」紀律字串。"""

    def test_section_compose_no_raw_citation_list_literal(self):
        """`build_section_compose_prompt(analyst_citations=[1,2,3,4,5])` 輸出
        不含 `"[1, 2, 3, 4, 5]"` substring（raw list literal channel 已堵）。"""
        import re
        from reasoning.prompts.writer import WriterPromptBuilder
        builder = WriterPromptBuilder()
        prompt = builder.build_section_compose_prompt(
            section_title="第一章",
            section_outline="背景",
            relevant_findings="[1] 文獻 A",
            analyst_citations=[1, 2, 3, 4, 5],
        )
        # 連續整 list literal 不可出現（schema 範例「[1, 2]」之類短示例可保留，
        # 但完整白名單 `[1, 2, 3, 4, 5]` 不可整段印）
        assert "[1, 2, 3, 4, 5]" not in prompt, (
            "writer prompt 不應 dump 完整 analyst_citations list literal — "
            "改為 narrative count + max id form"
        )
        # 應含 narrative 形式（用「N 個」或「最大 ID」等字眼）
        assert ("5 個" in prompt or "個白名單" in prompt or "最大 ID" in prompt), (
            "writer prompt 應有 narrative count / max id 取代 raw list dump"
        )

    def test_section_compose_has_no_tail_dump_discipline(self):
        """`build_section_compose_prompt` 輸出含「禁段末 dump」紀律字串。"""
        from reasoning.prompts.writer import WriterPromptBuilder
        builder = WriterPromptBuilder()
        prompt = builder.build_section_compose_prompt(
            section_title="第一章",
            section_outline="背景",
            relevant_findings="[1] 文獻",
            analyst_citations=[1, 2, 3],
            citation_format="numeric",
        )
        # 必須有禁段末 dump 紀律
        assert ("段末" in prompt and "禁止" in prompt) or "禁止在段末" in prompt, (
            "writer prompt 應加明示「禁止段末 dump 來源清單」紀律"
        )

    def test_compose_prompt_with_plan_no_raw_citation_list(self):
        """DR mode `build_compose_prompt_with_plan` 同病同治。"""
        from reasoning.prompts.writer import WriterPromptBuilder
        from unittest.mock import MagicMock
        builder = WriterPromptBuilder()
        plan = MagicMock()
        plan.outline = "outline"
        plan.estimated_length = 1000
        plan.key_arguments = ["A", "B"]
        prompt = builder.build_compose_prompt_with_plan(
            analyst_draft="draft",
            analyst_citations=[1, 2, 3, 4, 5],
            plan=plan,
        )
        assert "[1, 2, 3, 4, 5]" not in prompt, (
            "build_compose_prompt_with_plan 不應 dump raw analyst_citations list"
        )


class TestOutlinePlannerEvidenceDumpForbidden:
    """Bug 1 root-fix：outline planner prompt 不應 dump evidence_ids raw list literal."""

    def test_planner_prompt_no_raw_evidence_ids_list(self):
        """`build_outline_planner_prompt` 輸出不含 `[1, 2, 3, 4, 5, 6, 7, 8]` list literal。"""
        from reasoning.prompts.outline_planner import build_outline_planner_prompt
        cm = _make_cm_with_evidence()  # evidence_ids 聯集 = {1..8}
        prompt = build_outline_planner_prompt(
            chapter_source=[
                {"name": "前言", "outline": "x"},
                {"name": "結論", "outline": "y"},
            ],
            context_map=cm,
            format_specs={},
            style_features=None,
        )
        assert "[1, 2, 3, 4, 5, 6, 7, 8]" not in prompt, (
            "outline planner prompt 不應 dump evidence_ids list literal — "
            "改為 narrative count + max id form"
        )


# ============================================================================
# FIX-4 (Cayenne #4/#5/#6): reframe per-chapter edit + constraint preservation
# ============================================================================


class TestReframePerChapterEdit:
    """_handle_pending_reframe 第 4 分支：單章微調 (FIX-4 / Cayenne #4)。"""

    @pytest.fixture
    def orchestrator(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        handler = MagicMock()
        handler.query_params = {}
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        return orch

    def _pending_state(self):
        from reasoning.schemas_live import ContextMapRevisionOperation
        cm = _make_cm()
        reframe_op = ContextMapRevisionOperation(
            op_type="reframe_structure",
            new_chapters=[
                {"name": "\u5F15\u8A00", "description": "\u80CC\u666F", "relevance": "core"},
                {"name": "\u570B\u5916\u6848\u4F8B", "description": "\u570B\u969B\u5C0D\u7167\u6848\u4F8B\u5206\u6790", "relevance": "core"},
                {"name": "\u7D50\u8AD6", "description": "\u7E3D\u7D50", "relevance": "core"},
            ],
            proposal_markdown="## \u820A\u63D0\u6848",
        )
        state = LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            initial_context_map_json=cm.model_dump_json(),
            pending_reframe_json=reframe_op.model_dump_json(),
            pending_reframe_proposal_markdown="## \u820A\u63D0\u6848",
        )
        return state

    @pytest.mark.asyncio
    async def test_per_chapter_edit_mutates_only_target(self, orchestrator):
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import ContextMapRevisionOperation
        state = self._pending_state()
        new_desc = "\u570B\u969B\u5C0D\u7167\u6848\u4F8B\uFF08\u4E0D\u7D0D\u5165\u667A\u5229\u6848\u4F8B\uFF09"
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="adjust"),
        ), patch.object(
            orchestrator, "_parse_per_chapter_reframe_edit",
            new=AsyncMock(return_value={"chapter_index": 1, "new_description": new_desc}),
        ):
            result = await orchestrator._handle_pending_reframe(
                state, user_message="\u570B\u5916\u6848\u4F8B\u90A3\u7AE0\u628A\u667A\u5229\u62FF\u6389", target_stage=1,
            )
        # \u4ECD pending\uFF08\u672A advance\uFF09
        assert result.stage_status == "checkpoint"
        assert result.pending_reframe_json
        op = ContextMapRevisionOperation.model_validate_json(result.pending_reframe_json)
        # \u53EA\u6709 index=1 \u88AB\u6539
        assert op.new_chapters[1]["description"] == new_desc
        assert op.new_chapters[0]["description"] == "\u80CC\u666F"
        assert op.new_chapters[2]["description"] == "\u7E3D\u7D50"

    @pytest.mark.asyncio
    async def test_per_chapter_edit_none_falls_back_to_adjust(self, orchestrator):
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import ContextMapRevisionOperation
        state = self._pending_state()
        before = state.pending_reframe_json
        with patch.object(
            orchestrator, "_classify_confirmation_intent",
            new=AsyncMock(return_value="adjust"),
        ), patch.object(
            orchestrator, "_parse_per_chapter_reframe_edit",
            new=AsyncMock(return_value=None),
        ):
            result = await orchestrator._handle_pending_reframe(
                state, user_message="\u55EF", target_stage=1,
            )
        # fallback adjust\uFF1Apending \u4E0D\u8B8A\u3001\u672A advance
        assert result.stage_status == "checkpoint"
        op = ContextMapRevisionOperation.model_validate_json(result.pending_reframe_json)
        op_before = ContextMapRevisionOperation.model_validate_json(before)
        assert op.new_chapters == op_before.new_chapters

    def test_prompt_has_constraint_preservation_rule(self):
        from reasoning.prompts.stage1_revision import Stage1RevisionPromptBuilder
        cm = _make_cm()
        prompt = Stage1RevisionPromptBuilder().build_intent_parse_prompt(
            "\u570B\u5916\u6848\u4F8B\u62FF\u6389\u667A\u5229", cm,
        )
        assert "\u9010\u5B57\u4FDD\u7559" in prompt
        assert "\u62FF\u6389\u667A\u5229" in prompt
