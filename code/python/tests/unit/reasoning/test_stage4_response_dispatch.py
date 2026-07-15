"""Target 2 — Stage4Response typed action enum + dispatcher tests.

Plan: lr-typeagent-refactor (2026-05-19)
CEO 拍板 OQ-1：**合併** — 刪舊 `_parse_stage_4_intent`，新
`_classify_stage_4_response` 取代，全 caller migrate。
"""
import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock, patch  # plan: durable boundary persist needs AsyncMock in scope


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2.1: Schema basics
# ──────────────────────────────────────────────────────────────────────────────

def test_response_action_enum_values():
    """10-action enum 完整覆蓋（per plan §3.2）。"""
    from reasoning.schemas_live import Stage4ResponseAction
    expected = {
        "confirm_reframe", "confirm_format", "confirm_both",
        "cancel_reframe", "adjust_chapters", "adjust_format",
        "add_special_element", "new_structure_request",
        "auto_continue", "unclear",
    }
    actual = {a.value for a in Stage4ResponseAction}
    assert actual == expected


def test_confirm_target_enum_values():
    from reasoning.schemas_live import Stage4ConfirmTarget
    expected = {"reframe", "format", "both"}
    actual = {a.value for a in Stage4ConfirmTarget}
    assert actual == expected


def test_response_confirm_reframe_requires_target():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="confirm_reframe")  # confirm_target=None


def test_response_confirm_format_requires_target():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="confirm_format")


def test_response_confirm_both_requires_target():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="confirm_both")


def test_response_adjust_chapters_requires_structural():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="adjust_chapters")


def test_response_new_structure_request_requires_structural():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="new_structure_request")


def test_response_adjust_format_requires_format():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="adjust_format")


def test_response_unclear_requires_question():
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response(action="unclear", clarifying_question="")


def test_response_confirm_format_with_target_ok():
    from reasoning.schemas_live import Stage4Response, Stage4ConfirmTarget
    r = Stage4Response(action="confirm_format", confirm_target="format")
    assert r.confirm_target == Stage4ConfirmTarget.format


def test_response_unclear_with_question_ok():
    from reasoning.schemas_live import Stage4Response
    r = Stage4Response(action="unclear", clarifying_question="想請你具體說明？")
    assert r.clarifying_question


# ──────────────────────────────────────────────────────────────────────────────
# Blocker C (2026-05-19): clarifying_question=None coerce 為 ""
# v13 backend log 觀察：LLM 為非 'unclear' action 仍 output null clarifying_question
# → Pydantic str field validation fail → 整個 Stage4Response reject → _save_state
# 沒跑完。Root fix：欄位語意 None == "" == 無需澄清，schema-side 收斂。
# Stage4Response unclear action 仍由 @model_validator 強制非空（不放寬契約）。
# ──────────────────────────────────────────────────────────────────────────────


def test_response_adjust_format_with_null_clarifying_question_coerces_to_empty():
    """Blocker C：LLM output {action:'adjust_format', clarifying_question:null}
    （v13 16:43:59 / 16:45:28 觀察 fail pattern） → coerce None 為 ""，不 reject。"""
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
    )
    r = Stage4Response.model_validate({
        "action": "adjust_format",
        "format_content": {
            "format_spec_extracted": "五千字左右",
            "citation_style_extracted": "author_year",
            "target_word_count": 5000,
        },
        "clarifying_question": None,
    })
    assert r.action == Stage4ResponseAction.adjust_format
    assert r.clarifying_question == ""
    assert r.format_content.target_word_count == 5000


def test_response_confirm_format_with_null_clarifying_question_coerces():
    """Blocker C：confirm_format + null clarifying_question 也應該 coerce。"""
    from reasoning.schemas_live import Stage4Response, Stage4ConfirmTarget
    r = Stage4Response.model_validate({
        "action": "confirm_format",
        "confirm_target": "format",
        "clarifying_question": None,
    })
    assert r.confirm_target == Stage4ConfirmTarget.format
    assert r.clarifying_question == ""


def test_response_unclear_with_null_clarifying_question_still_rejects():
    """Blocker C：unclear action 的 clarifying_question 必填契約不放寬 —
    null → coerce "" → @model_validator reject (空字串 fail)。"""
    from reasoning.schemas_live import Stage4Response
    with pytest.raises(ValidationError):
        Stage4Response.model_validate({
            "action": "unclear",
            "clarifying_question": None,
        })


def test_response_apa_short_reply_typical_llm_output_succeeds():
    """Blocker C：模擬 v13 16:45:23 user reply「用 APA」LLM 典型 output —
    adjust_format + null clarifying_question 真實場景應通過 validation。"""
    from reasoning.schemas_live import Stage4Response, Stage4ResponseAction
    # 對應 v13 backend log line 「Stage4Response validation fail
    # clarifying_question Input should be a valid string, input_value=None」
    llm_output = {
        "action": "adjust_format",
        "confirm_target": None,
        "structural_content": None,
        "format_content": {
            "format_spec_extracted": "",
            "citation_style_extracted": "author_year",
            "target_word_count": None,
            "special_elements": [],
        },
        "clarifying_question": None,
    }
    r = Stage4Response.model_validate(llm_output)
    assert r.action == Stage4ResponseAction.adjust_format
    assert r.format_content.citation_style_extracted == "author_year"
    assert r.clarifying_question == ""


def test_response_adjust_chapters_with_payload_ok():
    from reasoning.schemas_live import (
        Stage4Response, Stage4StructuralPayload, ChapterSpec,
    )
    r = Stage4Response(
        action="adjust_chapters",
        structural_content=Stage4StructuralPayload(
            new_chapters=[ChapterSpec(name="前言"), ChapterSpec(name="結論")],
        ),
    )
    assert len(r.structural_content.new_chapters) == 2


def test_response_auto_continue_no_payload_ok():
    from reasoning.schemas_live import Stage4Response
    r = Stage4Response(action="auto_continue")
    assert r.action.value == "auto_continue"
    assert r.confirm_target is None


def test_response_cancel_reframe_no_payload_ok():
    from reasoning.schemas_live import Stage4Response
    r = Stage4Response(action="cancel_reframe")
    assert r.action.value == "cancel_reframe"


def test_response_add_special_element_with_format_payload():
    from reasoning.schemas_live import (
        Stage4Response, Stage4FormatPayload, SpecialElementSpec,
    )
    r = Stage4Response(
        action="add_special_element",
        format_content=Stage4FormatPayload(
            special_elements=[SpecialElementSpec(type="table", target_chapter="結果與討論")],
        ),
    )
    assert r.format_content.special_elements[0].type == "table"


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2.2: _classify_stage_4_response orchestrator helper + prompt builder
# ──────────────────────────────────────────────────────────────────────────────


def test_response_classifier_prompt_builder_exists():
    """builder.build_response_classifier_prompt 存在且回傳含 10 action enum。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_response_classifier_prompt(
        user_message="OK",
        pending_reframe=True,
        pending_format_confirmation=False,
    )
    for action_val in (
        "confirm_reframe", "confirm_format", "confirm_both",
        "cancel_reframe", "adjust_chapters", "adjust_format",
        "add_special_element", "new_structure_request",
        "auto_continue", "unclear",
    ):
        assert action_val in prompt, f"prompt 缺 action enum '{action_val}'"


def test_response_classifier_prompt_includes_state_snapshot():
    """prompt 必須含 pending state — dispatcher 上下文。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_response_classifier_prompt(
        user_message="比較表加到結果與討論章節裡",
        pending_reframe=False,
        pending_format_confirmation=True,
    )
    assert "pending_format_confirmation" in prompt
    assert "True" in prompt


@pytest.mark.asyncio
async def test_classify_stage_4_response_dry_run_unclear():
    """dry_run 不呼 LLM → safe default 'unclear'，clarifying_question 非空。"""
    from unittest.mock import MagicMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = True
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    r = await orch._classify_stage_4_response(state, "test")
    assert r.action.value == "unclear"
    assert r.clarifying_question  # non-empty


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2.3: dispatcher 切換到 typed action 路由
# CEO 拍板 OQ-1：完全取代舊 _parse_stage_4_intent
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_confirm_format_advances_without_reparse():
    """v8 Bug 2 partial root fix — confirm_format typed action 不 re-emit checkpoint。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4ConfirmTarget,
    )

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.pending_format_confirmation = True
    state.format_specs = {"default": "markdown_apa", "chapters": [{"name": "前言"}]}

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.confirm_format,
            confirm_target=Stage4ConfirmTarget.format,
        )
    )
    orch._emit_checkpoint = AsyncMock()
    orch._emit_narration = AsyncMock()

    result = await orch._handle_stage_4_response(state, "好就這樣", auto_continue=False)
    # complete_stage marked completed
    assert result.stage_status == "completed"
    # 不 re-emit checkpoint（既有 bug 2 partial：confirm 後又 emit format checkpoint）
    orch._emit_checkpoint.assert_not_called()
    # chapters 保留
    assert result.format_specs.get("chapters") == [{"name": "前言"}]


@pytest.mark.asyncio
async def test_dispatch_add_special_element_no_loop():
    """add_special_element 停在 checkpoint（不 advance）— 必須重 emit checkpoint 讓前端 reply UI 恢復。

    Root fix: 原本 assert call_count==0 來自 v8 Bug 2「confirm_format 不應 loop advance」
    的脈絡，對「add_special_element 停在 checkpoint」不適用。re-emit 同一 format
    checkpoint 不 advance（不呼叫 complete_stage），不 regress v8 Bug 2。
    """
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4FormatPayload, SpecialElementSpec,
    )

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.pending_format_confirmation = True
    state.set_checkpoint("格式詢問 prompt（Stage 4）")
    # R2（2026-07）：dispatch 現在會用 _resolve_chapter_source 取暫定章名清單判 target
    # 第一層 code 短路。給有效 context_map_json（否則 dispatch try 讀 context_map 會 except
    # → 章名空）+ format_specs.chapters override（含「結果與討論」）→ target exact 命中
    # → 直接寫入 special_elements（不落 clarify pending）。
    from reasoning.schemas_live import ContextMap
    state.context_map_json = ContextMap(research_question="q").model_dump_json()
    state.format_specs = {"chapters": [{"name": "結果與討論", "outline": ""}]}

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.add_special_element,
            format_content=Stage4FormatPayload(
                special_elements=[SpecialElementSpec(
                    type="table", target_chapter="結果與討論", description="比較表"
                )],
            ),
        )
    )
    orch._emit_checkpoint = AsyncMock()
    orch._emit_narration = AsyncMock()

    result = await orch._handle_stage_4_response(
        state, "比較表加到結果與討論章節裡", auto_continue=False,
    )
    # add_special_element 不 advance（user 還沒 confirm format） — stage_status 保持 checkpoint
    assert result.stage_status == "checkpoint"
    assert result.current_stage == 4
    # root fix: add_special_element 停在 checkpoint（不 advance），必須重 emit
    # checkpoint 讓前端 reply UI 恢復；re-emit 同一 format checkpoint 不 advance，
    # 不會 regress v8 Bug 2（confirm_format advance loop）。
    assert orch._emit_checkpoint.call_count == 1
    _, kwargs = orch._emit_checkpoint.call_args
    assert kwargs.get("stage") == 4
    # R2 新契約：exact 命中章名 → 走 serializer 直接寫入（三欄，無 transient）；不落 pending。
    assert result.format_specs["special_elements"][0]["type"] == "table"
    assert result.format_specs["special_elements"][0]["target_chapter"] == "結果與討論"
    assert not result.pending_special_element_json  # exact 命中不進 pending


@pytest.mark.asyncio
async def test_dispatch_add_special_element_empty_payload_reemits_checkpoint():
    """add_special_element 但 payload 空 → fallback narration + 重 emit checkpoint。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        Stage4Response,
        Stage4ResponseAction,
        Stage4FormatPayload,
    )

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.set_checkpoint("格式詢問 prompt（Stage 4）")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.add_special_element,
            format_content=Stage4FormatPayload(special_elements=[]),
        ),
    )
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    result = await orch._handle_stage_4_response(state, "加個東西", auto_continue=False)
    assert result.stage_status == "checkpoint"
    assert result.current_stage == 4
    orch._emit_narration.assert_called_once()
    orch._emit_checkpoint.assert_called_once()
    _, kwargs = orch._emit_checkpoint.call_args
    assert kwargs.get("stage") == 4
    assert kwargs.get("proposal") == "格式詢問 prompt（Stage 4）"


@pytest.mark.asyncio
async def test_dispatch_auto_continue_advances():
    """auto_continue action → merge default + complete_stage。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(action=Stage4ResponseAction.auto_continue),
    )
    result = await orch._handle_stage_4_response(state, "你決定", auto_continue=False)
    assert result.stage_status == "completed"
    assert result.format_specs.get("default") == "markdown_apa"


@pytest.mark.asyncio
async def test_dispatch_unclear_stays_at_checkpoint():
    """unclear action → emit clarifying_question narration + 重 emit checkpoint、不 advance。

    Root fix: narration-only 會讓前端 reply UI 卡住（continueLiveResearch 已把
    _lrAwaitingCheckpointReply 設 false，narration 防呆不觸發）。必須重 emit
    live_research_checkpoint 讓 showLRCheckpoint 恢復 reply UI。
    """
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.set_checkpoint("格式詢問 prompt（Stage 4）")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.unclear,
            clarifying_question="想請你具體說明？",
        ),
    )
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    result = await orch._handle_stage_4_response(state, "嗯？", auto_continue=False)
    assert result.stage_status == "checkpoint"
    assert result.current_stage == 4
    orch._emit_narration.assert_called_once()
    # root fix: 必須重 emit checkpoint，否則前端 reply UI 卡住
    orch._emit_checkpoint.assert_called_once()
    _, kwargs = orch._emit_checkpoint.call_args
    assert kwargs.get("stage") == 4
    assert kwargs.get("proposal") == "格式詢問 prompt（Stage 4）"


@pytest.mark.asyncio
async def test_dispatch_adjust_format_advances_and_records():
    """adjust_format action → 寫 format_specs + advance。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
    )

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.adjust_format,
            format_content=Stage4FormatPayload(
                format_spec_extracted="每段 500 字",
                citation_style_extracted="author_year",
            ),
        ),
    )
    result = await orch._handle_stage_4_response(state, "每段 500 字 APA", auto_continue=False)
    assert result.stage_status == "completed"  # advance
    assert result.user_voice.citation_style == "author_year"


@pytest.mark.asyncio
async def test_dispatch_adjust_format_writes_target_word_count():
    """Blocker A root fix (2026-05-19)：「APA 引用格式，五千字左右」典型 mixed
    format payload → user_voice.target_word_count == 5000 + format_specs mirror。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
    )

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.adjust_format,
            format_content=Stage4FormatPayload(
                format_spec_extracted="五千字左右",
                citation_style_extracted="author_year",
                target_word_count=5000,
            ),
        ),
    )
    result = await orch._handle_stage_4_response(
        state, "APA 引用格式，五千字左右", auto_continue=False,
    )
    assert result.stage_status == "completed"
    assert result.user_voice.citation_style == "author_year"
    # typed channel + format_specs mirror (供 outline planner 讀取)
    assert result.user_voice.target_word_count == 5000
    assert result.format_specs.get("target_word_count") == 5000


@pytest.mark.asyncio
async def test_dispatch_adjust_format_no_word_count_leaves_user_voice_default():
    """adjust_format 但 user 沒提字數 → target_word_count 留 None（不污染 user_voice）。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
    )

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.adjust_format,
            format_content=Stage4FormatPayload(
                format_spec_extracted="每段 500 字",
                citation_style_extracted="author_year",
                target_word_count=None,
            ),
        ),
    )
    result = await orch._handle_stage_4_response(
        state, "每段 500 字、APA", auto_continue=False,
    )
    assert result.user_voice.target_word_count is None
    assert "target_word_count" not in result.format_specs


@pytest.mark.asyncio
async def test_classify_stage_4_response_llm_exception_uses_system_unavailable():
    """ask_llm 例外（API 失敗）→ fallback 用系統端常數（lr_copy 單一事實源），不怪 user。"""
    from unittest.mock import AsyncMock, MagicMock, patch
    from reasoning.live_research import lr_copy
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    with patch(
        "reasoning.live_research.orchestrator.ask_llm",
        new=AsyncMock(side_effect=RuntimeError("simulated API failure")),
    ):
        r = await orch._classify_stage_4_response(state, "幫我調整章節")

    assert r.action.value == "unclear"
    assert r.clarifying_question == lr_copy.LLM_UNAVAILABLE_NARRATION


@pytest.mark.asyncio
async def test_classify_stage_4_response_empty_uses_system_unavailable():
    """ask_llm 回空（None/{}）→ `if not response:` 分支也用系統端常數（封死第三處漏改的假綠燈）。"""
    from unittest.mock import AsyncMock, MagicMock, patch
    from reasoning.live_research import lr_copy
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    with patch(
        "reasoning.live_research.orchestrator.ask_llm",
        new=AsyncMock(return_value=None),
    ):
        r = await orch._classify_stage_4_response(state, "幫我調整章節")

    assert r.action.value == "unclear"
    assert r.clarifying_question == lr_copy.LLM_UNAVAILABLE_NARRATION


@pytest.mark.asyncio
async def test_classify_stage_4_response_validation_fail_uses_system_unavailable():
    """LLM 回了但 schema validation 失敗 → 系統端常數（user 重述也修不了 malformed schema）。"""
    from unittest.mock import AsyncMock, MagicMock, patch
    from reasoning.live_research import lr_copy
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    # 回一個非 None、非空、但缺 required 'action' 又無 'properties' 包裝的 dict
    # → 不觸發 unwrap、直奔 model_validate 失敗的 except 分支
    with patch(
        "reasoning.live_research.orchestrator.ask_llm",
        new=AsyncMock(return_value={"garbage_field": "no action key"}),
    ):
        r = await orch._classify_stage_4_response(state, "幫我調整章節")

    assert r.action.value == "unclear"
    assert r.clarifying_question == lr_copy.LLM_UNAVAILABLE_NARRATION


def test_response_classifier_prompt_includes_target_word_count_few_shots():
    """Few-shot prompt 含「APA + 五千字」典型範例 + 中文數字解析規則。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_response_classifier_prompt(
        user_message="APA 引用格式，五千字左右",
        pending_reframe=False,
        pending_format_confirmation=True,
    )
    # 範例 6b 必須存在
    assert "APA 引用格式，五千字左右" in prompt
    assert '"target_word_count":5000' in prompt
    # 抽取規則明示
    assert "target_word_count" in prompt
    assert "中文數字" in prompt


@pytest.mark.asyncio
async def test_dispatch_auto_continue_branch_via_arg():
    """auto_continue=True arg → 直接 advance（不打 LLM classifier）。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")

    orch._classify_stage_4_response = AsyncMock()  # 不該被叫
    result = await orch._handle_stage_4_response(state, "", auto_continue=True)
    assert result.stage_status == "completed"
    orch._classify_stage_4_response.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_confirm_reframe_without_pending_reemits_checkpoint():
    """confirm_reframe 但無 pending → fallback narration + 重 emit checkpoint。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.pending_reframe_json = ""  # 確保沒有 pending，不走 short-circuit
    state.set_checkpoint("格式詢問 prompt（Stage 4）")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.confirm_reframe,
            confirm_target="reframe",
        ),
    )
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    result = await orch._handle_stage_4_response(state, "OK", auto_continue=False)
    assert result.stage_status == "checkpoint"
    assert result.current_stage == 4
    orch._emit_narration.assert_called_once()
    orch._emit_checkpoint.assert_called_once()
    _, kwargs = orch._emit_checkpoint.call_args
    assert kwargs.get("stage") == 4
    assert kwargs.get("proposal") == "格式詢問 prompt（Stage 4）"


@pytest.mark.asyncio
async def test_dispatch_cancel_reframe_without_pending_reemits_checkpoint():
    """cancel_reframe 但無 pending → fallback narration + 重 emit checkpoint。"""
    from unittest.mock import MagicMock, AsyncMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import Stage4Response, Stage4ResponseAction

    handler = MagicMock()
    handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.pending_reframe_json = ""
    state.set_checkpoint("格式詢問 prompt（Stage 4）")

    orch._classify_stage_4_response = AsyncMock(
        return_value=Stage4Response(
            action=Stage4ResponseAction.cancel_reframe,
        ),
    )
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    result = await orch._handle_stage_4_response(state, "算了不要", auto_continue=False)
    assert result.stage_status == "checkpoint"
    assert result.current_stage == 4
    orch._emit_narration.assert_called_once()
    orch._emit_checkpoint.assert_called_once()
    _, kwargs = orch._emit_checkpoint.call_args
    assert kwargs.get("stage") == 4
    assert kwargs.get("proposal") == "格式詢問 prompt（Stage 4）"
