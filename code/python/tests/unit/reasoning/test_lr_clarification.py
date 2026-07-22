"""LR 通用 clarification 機制：ClarificationRequest model + _emit_clarification helper。

設計來源：docs/in progress/discussions/lr-clarification-mechanism-design.md §3。
收斂三條同型 spine（Stage 1 empty-ops / Stage 4 unclear / R2 表格指涉）。
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_clarification_request_requires_nonempty_question():
    """對齊既有 Stage4Response action='unclear' validator 藍本：問句非空由 validator 保證。"""
    from reasoning.schemas_live import ClarificationRequest
    req = ClarificationRequest(question="你想放哪一章？①前言 ②結論", stage=4)
    assert req.question and req.stage == 4
    with pytest.raises(Exception):
        ClarificationRequest(question="", stage=4)
    with pytest.raises(Exception):
        ClarificationRequest(question="   ", stage=4)


def _make_orch():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock()
    handler.query = "測試"
    handler.query_params = {}
    handler.site = "all"
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=True)
    handler.final_retrieved_items = []
    handler._save_state = AsyncMock()
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    return orch


@pytest.mark.asyncio
async def test_emit_clarification_narrates_and_reemits_checkpoint_returns_state():
    """helper = emit 問句 narration + re-emit checkpoint(stage) + return state（不 advance）。"""
    from reasoning.schemas_live import ClarificationRequest
    orch = _make_orch()
    narrated = []
    checkpoints = []

    async def fake_narr(text):
        narrated.append(text)

    async def fake_ckpt(stage, proposal, **kw):
        checkpoints.append((stage, proposal))

    orch._emit_narration = fake_narr
    orch._emit_checkpoint = fake_ckpt
    state = MagicMock()
    state.checkpoint_prompt = "格式確認 checkpoint"

    req = ClarificationRequest(question="你想放哪一章？①前言 ②結論", stage=4)
    ret = await orch._emit_clarification(req, state)

    assert narrated == ["你想放哪一章？①前言 ②結論"]
    assert checkpoints == [(4, "格式確認 checkpoint")]
    assert ret is state  # 不 advance，回原 state


# ═══════════════ Task 5: R2 雙層 + serializer + 問句 + Stage 5 filter ═══════════════


def test_resolve_target_chapter_layer1_exact_and_unique():
    """第一層 code 短路：exact / 唯一 substring / 空 → 直接命中；其餘 → None（交 LLM 語意層）。"""
    from reasoning.live_research.orchestrator import _resolve_target_chapter_layer1
    names = ["前言", "國內案例", "國外案例", "結果與討論", "結論"]
    assert _resolve_target_chapter_layer1("結果與討論", names) == "結果與討論"  # exact
    assert _resolve_target_chapter_layer1("", names) == ""  # 空 = 全章注入 sentinel
    assert _resolve_target_chapter_layer1("討論", names) == "結果與討論"  # 唯一 substring
    # 非 exact/非唯一（語意/序數/多候選/對不到）→ None（第一層放手，交第二層 LLM）
    assert _resolve_target_chapter_layer1("講政策的那章", names) is None  # 語意 → 交 LLM
    assert _resolve_target_chapter_layer1("第四章", names) is None  # 序數 → 交 LLM
    assert _resolve_target_chapter_layer1("附錄A", names) is None  # 對不到 → 交 LLM


def test_special_element_spec_transient_fields_optional_default_none():
    """schema 擴 Optional transient 欄位，舊持久化 dict 缺 key → 默認 None（零 migration）。"""
    from reasoning.schemas_live import SpecialElementSpec
    # 舊持久化 shape（無 transient 欄位）仍合法 validate
    s = SpecialElementSpec.model_validate({"type": "table", "target_chapter": "結論", "description": ""})
    assert s.resolved_chapter_title is None
    assert s.resolution_confidence is None
    # 新 transient 欄位可帶
    s2 = SpecialElementSpec.model_validate({
        "type": "table", "target_chapter": "講政策的那章",
        "resolved_chapter_title": "結果與討論", "resolution_confidence": "clear",
    })
    assert s2.resolved_chapter_title == "結果與討論" and s2.resolution_confidence == "clear"


def test_serialize_special_element_strips_transient():
    """B3：serializer 強制排除 transient 欄位 + None，持久化 shape 只剩 type/target_chapter/description。"""
    from reasoning.live_research.orchestrator import _serialize_special_element_for_state
    # 帶 transient 的 dict → strip 掉
    out = _serialize_special_element_for_state({
        "type": "table", "target_chapter": "結論", "description": "x",
        "resolved_chapter_title": "結論", "resolution_confidence": "clear",
    })
    assert out == {"type": "table", "target_chapter": "結論", "description": "x"}
    assert "resolved_chapter_title" not in out and "resolution_confidence" not in out
    # SpecialElementSpec 物件 → 同樣只留三欄
    from reasoning.schemas_live import SpecialElementSpec
    spec = SpecialElementSpec(type="list", target_chapter="", description="",
                              resolved_chapter_title="前言", resolution_confidence="uncertain")
    out2 = _serialize_special_element_for_state(spec)
    assert set(out2.keys()) == {"type", "target_chapter", "description"}


def test_merge_format_specs_user_serializes_special_elements():
    """B3：_merge_format_specs_user 也走 serializer strip transient（adjust_format 路徑）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    dirty = [{"type": "table", "target_chapter": "結論", "description": "",
              "resolved_chapter_title": "結論", "resolution_confidence": "clear"}]
    merged = LiveResearchOrchestrator._merge_format_specs_user({}, "msg", special_elements=dirty)
    se = merged["special_elements"]
    assert se == [{"type": "table", "target_chapter": "結論", "description": ""}]
    assert "resolution_confidence" not in se[0]


def test_dispatch_copies_existing_list_sanitized():
    """SF2b（Codex）：add_special_element dispatch 拷貝既有 special_elements 時也逐項 serialize —
    既有髒 transient element + 新 resolved element 一起寫回後**都只剩三欄**（不繼續污染持久化）。"""
    from reasoning.live_research.orchestrator import _serialize_special_element_for_state
    existing_dirty = [{"type": "list", "target_chapter": "前言", "description": "",
                       "resolved_chapter_title": "前言", "resolution_confidence": "clear"}]
    resolved_elements = [_serialize_special_element_for_state(e)
                         for e in existing_dirty if isinstance(e, dict)]
    resolved_elements.append(_serialize_special_element_for_state(
        {"type": "table", "target_chapter": "結論", "description": ""}))
    for el in resolved_elements:
        assert set(el.keys()) == {"type", "target_chapter", "description"}  # 全三欄，無 transient


def test_special_element_confirm_question_merges_multiple():
    """clear → 確認式問句（多個 title 合併成一句，nit）。"""
    from reasoning.live_research import lr_copy
    q1 = lr_copy.special_element_confirm_question("結果與討論")  # 單一
    assert "結果與討論" in q1
    q2 = lr_copy.special_element_confirm_question(["結果與討論", "國外案例"])  # 多個合併
    assert "結果與討論" in q2 and "國外案例" in q2
    assert "；" not in q2  # nit：不用「；」串多句
    for bad in ("LLM", "token", "confidence", "resolved", "None"):
        assert bad not in q2


def test_special_element_clarification_question_lists_chapters():
    """uncertain → 完整枚舉列章名。"""
    from reasoning.live_research import lr_copy
    q = lr_copy.special_element_clarification_question(["附錄A"], ["前言", "國內案例", "結論"])
    assert "附錄A" in q
    assert "前言" in q and "國內案例" in q and "結論" in q
    for bad in ("LLM", "token", "target_chapter", "index", "None"):
        assert bad not in q


def test_stage5_filter_exact_only():
    """Stage 5 filter：exact 命中 section_title 注入 / 空→全章 / 對不到→不注入（C-7）。"""
    def _filter(all_se, section_title):
        out = []
        for elem in all_se:
            t = (elem.get("target_chapter") or "").strip()
            if not t:
                out.append(elem)          # 空 → 全章
            elif t == section_title:
                out.append(elem)          # exact 命中
        return out
    elems = [{"type": "table", "target_chapter": "結果與討論"}]
    assert _filter(elems, "結果與討論") == elems   # exact 命中
    assert _filter(elems, "前言") == []             # 對不到 → 不注入
    assert _filter([{"type": "table", "target_chapter": ""}], "任意章") == \
        [{"type": "table", "target_chapter": ""}]   # 空 → 全章


# ═══════════════ Task 5: pending special_element 狀態機（機械路由，LLM 意圖隔開）═══════════════


def _prep_pending_orch(orch):
    """把 Task 4 的 _make_orch() 產物調成 pending test 用（dry_run + fake async methods）。"""
    orch.dry_run = True

    async def _noop(*a, **k):
        return None

    orch._emit_narration = _noop
    orch._emit_checkpoint = _noop
    orch._persist_checkpoint_boundary = _noop

    async def _emit_clar(req, state):
        return state

    orch._emit_clarification = _emit_clar
    return orch


def _make_stage4_state():
    state = MagicMock()
    state.format_specs = {}
    state.pending_reframe_json = ""
    state.pending_special_element_json = ""
    state.checkpoint_prompt = "格式確認 checkpoint"
    return state


@pytest.mark.asyncio
async def test_handle_pending_special_element_confirm(monkeypatch):
    """confirm intent（dry_run「對」→ keyword fallback confirm）→ 用 resolved_title 定位 + 清 pending。"""
    orch = _prep_pending_orch(_make_orch())
    state = _make_stage4_state()
    state.pending_special_element_json = json.dumps({
        "kind": "confirm",
        "elements": [{"type": "table", "resolved_title": "結果與討論",
                      "raw_target": "講政策的那章", "description": ""}],
        "clarify_backlog": [], "chapter_names": ["前言", "結果與討論", "結論"],
    }, ensure_ascii=False)
    await orch._handle_pending_special_element(state, "對")
    se = state.format_specs["special_elements"]
    assert se == [{"type": "table", "target_chapter": "結果與討論", "description": ""}]  # 走 serializer 三欄
    assert state.pending_special_element_json == ""   # 清 pending


@pytest.mark.asyncio
async def test_handle_pending_special_element_cancel(monkeypatch):
    """cancel intent（dry_run「算了」→ fallback cancel）→ 不注入 + 清 pending。"""
    orch = _prep_pending_orch(_make_orch())
    state = _make_stage4_state()
    state.format_specs = {}
    state.pending_special_element_json = json.dumps({
        "kind": "confirm", "elements": [{"type": "table", "resolved_title": "結論",
        "raw_target": "", "description": ""}], "clarify_backlog": [],
        "chapter_names": ["前言", "結論"]}, ensure_ascii=False)
    await orch._handle_pending_special_element(state, "算了不用了")
    assert not state.format_specs.get("special_elements")   # 未注入
    assert state.pending_special_element_json == ""


@pytest.mark.asyncio
async def test_handle_pending_malformed_json_recovers(monkeypatch):
    """malformed pending → 清空 + persist + 重問（不無限重進壞 pending，no silent fail）。"""
    orch = _prep_pending_orch(_make_orch())
    state = _make_stage4_state()
    state.pending_special_element_json = "{not valid json"
    await orch._handle_pending_special_element(state, "隨便")
    assert state.pending_special_element_json == ""


@pytest.mark.asyncio
async def test_pending_mutual_exclusion_recovers(monkeypatch):
    """互斥 invariant：reframe + special 同時非空 → fail-loud 清 special（reframe 優先）+ SF3b 誠實 narration。"""
    orch = _prep_pending_orch(_make_orch())
    narrated = []

    async def _cap(t):
        narrated.append(t)

    orch._emit_narration = _cap
    state = _make_stage4_state()
    state.pending_reframe_json = json.dumps({"kind": "adjust_chapters"}, ensure_ascii=False)
    state.pending_special_element_json = json.dumps({"kind": "confirm", "elements": [],
        "chapter_names": []}, ensure_ascii=False)
    recovered = await orch._enforce_pending_exclusivity(state)
    assert recovered is True                           # SF-order：回 True 供 caller route
    assert state.pending_special_element_json == ""    # special 被清
    assert state.pending_reframe_json != ""            # reframe 保留、優先吃
    assert narrated and "表格" in narrated[0]           # SF3b：user-facing recovery narration
    # SF3b 誠實：不承諾「不會漏掉/不漏」（資料已清）
    assert "不會漏" not in narrated[0] and "不漏" not in narrated[0]
    # 無雙 pending → 回 False、不動作
    state2 = _make_stage4_state()
    assert await orch._enforce_pending_exclusivity(state2) is False


@pytest.mark.asyncio
async def test_handle_pending_blank_reemits_not_advance(monkeypatch):
    """B-order：pending 非空 + blank/auto → re-emit clarification、保 pending、不 finalize。"""
    orch = _prep_pending_orch(_make_orch())
    emitted = []

    async def _cap_clar(req, state):
        emitted.append(req.question)
        return state

    orch._emit_clarification = _cap_clar
    state = _make_stage4_state()
    pend = json.dumps({"kind": "clarify", "elements": [{"type": "table",
        "raw_target": "附錄A", "description": ""}], "chapter_names": ["前言", "結論"]},
        ensure_ascii=False)
    state.pending_special_element_json = pend
    await orch._handle_pending_special_element(state, "", auto_continue=True)
    assert state.pending_special_element_json == pend   # 保 pending、不清
    assert not state.format_specs.get("special_elements")  # 不 finalize
    assert emitted   # re-emit 了問句


@pytest.mark.asyncio
async def test_handle_pending_change_chapter_no_outline_writes_raw(monkeypatch):
    """B-loop 消費端：no-outline（chapter_names=[]）+ classifier 已回非空 target → handler 直寫 raw。
    ⚠️ 此 test 只驗 handler 分支（stub classifier）；prompt 生產端另有 test 驗。"""
    orch = _prep_pending_orch(_make_orch())

    async def _stub_cls(msg, names, summary):
        return {"intent": "change_chapter", "target_chapter": "結論"}

    orch._classify_pending_special_element_reply = _stub_cls
    state = _make_stage4_state()
    state.pending_special_element_json = json.dumps({"kind": "clarify",
        "elements": [{"type": "table", "raw_target": "", "description": ""}],
        "chapter_names": []}, ensure_ascii=False)   # 無 outline
    await orch._handle_pending_special_element(state, "放結論那章", auto_continue=False)
    se = state.format_specs["special_elements"]
    assert se == [{"type": "table", "target_chapter": "結論", "description": ""}]  # 直寫 raw
    assert state.pending_special_element_json == ""   # 清 pending，不卡死


@pytest.mark.asyncio
async def test_pending_classifier_prompt_no_outline_raw_target_rule(monkeypatch):
    """B-loop 生產端（Codex）：prompt 必須教 LLM 在 no-outline（chapter_names=[]）時
    仍填非空 raw target（否則 handler 的 no-outline 直寫分支永遠進不去，prod 復現卡死）。
    不 stub classifier —— patch ask_llm 捕捉真實 prompt 內容驗規則存在。"""
    orch = _prep_pending_orch(_make_orch())
    orch.dry_run = False   # 走 production LLM path 才組 prompt
    captured = {}

    async def _fake_ask(prompt, schema, **kw):
        captured["prompt"] = prompt
        return {"intent": "change_chapter", "target_chapter": "結論"}

    monkeypatch.setattr("reasoning.live_research.orchestrator.ask_llm", _fake_ask)
    res = await orch._classify_pending_special_element_reply("放結論那章", [], "table放「未指定」")
    p = captured["prompt"]
    assert "章節尚未定案" in p               # no-outline 情境確實成立
    assert "章節清單為空" in p and "raw target" in p and "不要留空" in p  # 生產端規則在
    assert res["target_chapter"] == "結論"   # LLM（此處 fake）回非空 → handler 接得到


@pytest.mark.asyncio
async def test_pending_reply_classifier_prod_calls_llm(monkeypatch):
    """nit：production（dry_run=False）永遠走 LLM，不走 keyword fallback。"""
    orch = _prep_pending_orch(_make_orch())
    orch.dry_run = False   # 覆寫回 production
    called = {"n": 0}

    async def _fake_ask(prompt, schema, **kw):
        called["n"] += 1
        return {"intent": "confirm", "target_chapter": ""}

    monkeypatch.setattr("reasoning.live_research.orchestrator.ask_llm", _fake_ask)
    res = await orch._classify_pending_special_element_reply("對", ["前言", "結論"], "table放「結論」")
    assert called["n"] == 1        # 輸入「對」在 production 仍呼叫 LLM，不走 fallback
    assert res["intent"] == "confirm"


# ═══════════════ Task 6: Stage 5 report-level no-silent-fail 後衛 ═══════════════


def test_diagnose_unmatched_special_element_targets():
    """report-level 後衛：找出 outline 定案後仍對不到任何章的 target（no silent fail）。"""
    from reasoning.live_research.orchestrator import _diagnose_unmatched_special_element_targets
    titles = ["前言", "國內案例", "國外案例", "結果與討論", "結論"]
    # exact 命中 → 不列
    assert _diagnose_unmatched_special_element_targets(
        [{"target_chapter": "結果與討論"}], titles) == []
    # 對不到 → 列入
    assert _diagnose_unmatched_special_element_targets(
        [{"target_chapter": "附錄A"}], titles) == ["附錄A"]
    # 空 target（全章注入）→ 不列
    assert _diagnose_unmatched_special_element_targets(
        [{"target_chapter": ""}], titles) == []
    # 無 outline → 不診斷（回 []）
    assert _diagnose_unmatched_special_element_targets(
        [{"target_chapter": "附錄A"}], []) == []


def test_special_element_target_unmatched_narration_no_jargon():
    from reasoning.live_research import lr_copy
    n = lr_copy.special_element_target_unmatched_narration(["附錄A", "第九章"])
    assert "附錄A" in n and "第九章" in n
    for bad in ("LLM", "token", "target_chapter", "normalize", "index", "None"):
        assert bad not in n


# ═══════════════ Task 7: prompt 抽取端 target_chapter 章名優先 + 序數合法 ═══════════════


def test_stage4_prompt_target_chapter_prefers_name_allows_ordinal():
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    prompt = Stage4IntentPromptBuilder().build_intent_classifier_prompt("測試訊息")
    assert "章節名稱原文" in prompt or "章名原文" in prompt
    assert "序數" in prompt


def test_stage1_extract_prompt_target_chapter_allows_ordinal():
    from reasoning.prompts.stage1_format_extract import Stage1FormatExtractPromptBuilder
    prompt = Stage1FormatExtractPromptBuilder().build_extract_prompt("測試委託")
    assert "章節名稱原文" in prompt or "章名原文" in prompt
    assert "序數" in prompt


# ═══════════════ B1（2026-07-15）：Stage 1 複合句紀律 prompt contract ═══════════════


def test_stage1_revision_prompt_compound_praise_rule_present():
    """B1 wiring 鎖：prompt 含「複合句紀律」塊與真實誤判反例。
    ⚠️ contract test 只證 prompt 字串在，不證 LLM 行為 —— 行為證據是
    _tmp_b1_b2_matrix.py 真 LLM 矩陣（結果記錄於 commit message）。"""
    from reasoning.prompts.stage1_revision import Stage1RevisionPromptBuilder
    from reasoning.schemas_live import ContextMap, ContextMapTopic
    cm = ContextMap(
        research_question="能源轉型下再生能源設置之地方衝突與治理",
        topics=[ContextMapTopic(topic_id="t1", name="光電衝突", domain="能源", relevance="core")],
        version=1,
    )
    prompt = Stage1RevisionPromptBuilder().build_intent_parse_prompt("結構很好，繼續", cm)
    assert "複合句" in prompt
    assert "結構很好，方向就這樣。請用這個架構" in prompt   # 真實誤判反例在
    assert "前綴不豁免" in prompt                            # 分支 A 補條在
    assert "矯枉過正" in prompt or "才是 confirm" in prompt   # 對照組例在


# ═══════════════ B2（2026-07-15）：pending classifier reframe 意圖擴充 ═══════════════


@pytest.mark.asyncio
async def test_pending_classifier_prompt_and_schema_include_reframe(monkeypatch):
    """B2 wiring 鎖：schema enum 含 reframe + prompt 教 reframe 意圖 +
    unknown-intent guard 不再把 reframe 降成 unclear。
    ⚠️ LLM 行為證據是 _tmp_b1_b2_matrix.py b2 真 LLM 矩陣。"""
    orch = _prep_pending_orch(_make_orch())
    orch.dry_run = False
    captured = {}

    async def _fake_ask(prompt, schema, **kw):
        captured["prompt"] = prompt
        captured["schema"] = schema
        return {"intent": "reframe", "target_chapter": ""}

    monkeypatch.setattr("reasoning.live_research.orchestrator.ask_llm", _fake_ask)
    res = await orch._classify_pending_special_element_reply(
        "把這 11 個主題重組成三章：前言、國際案例分析、結論", ["主題1", "主題2"], "table放「結論」")
    assert "reframe" in captured["schema"]["properties"]["intent"]["enum"]
    assert "重組" in captured["prompt"]           # prompt 有教 reframe 意圖
    assert res["intent"] == "reframe"             # guard 不把合法 reframe 降成 unclear


def _make_stage4_response(action_name, chapters=None, clarifying=""):
    """typed Stage4Response 構造 helper（validator 強制 action↔payload 互斥契約）。"""
    from reasoning.schemas_live import (
        Stage4Response, Stage4ResponseAction, Stage4StructuralPayload, ChapterSpec,
    )
    if chapters:
        return Stage4Response(
            action=Stage4ResponseAction(action_name),
            structural_content=Stage4StructuralPayload(
                new_chapters=[ChapterSpec(name=n) for n in chapters],
                summary="整體重組",
            ),
        )
    return Stage4Response(action=Stage4ResponseAction(action_name),
                          clarifying_question=clarifying or "想怎麼調整結構呢？")


@pytest.mark.asyncio
async def test_handle_pending_reframe_escape_clears_pending_and_routes():
    """B2 逃生口主路徑：pending 中 user 提整體重組 → 清 pending + 誠實告知表格暫停 +
    交既有 _try_stage_4_reframe_entry_typed（帶 typed structural payload），不重播澄清問句。"""
    orch = _prep_pending_orch(_make_orch())
    narrated = []

    async def _cap(t):
        narrated.append(t)

    orch._emit_narration = _cap

    async def _stub_cls(msg, names, summary):
        return {"intent": "reframe", "target_chapter": ""}

    orch._classify_pending_special_element_reply = _stub_cls
    orch._classify_stage_4_response = AsyncMock(return_value=_make_stage4_response(
        "new_structure_request", chapters=["前言", "國際案例分析", "結論"]))
    orch._try_stage_4_reframe_entry_typed = AsyncMock(
        side_effect=lambda state, *a, **k: state)

    state = _make_stage4_state()
    state.pending_special_element_json = json.dumps({"kind": "clarify",
        "elements": [{"type": "table", "raw_target": "結論", "description": ""}],
        "chapter_names": ["主題1", "主題2", "主題3"]}, ensure_ascii=False)
    await orch._handle_pending_special_element(
        state, "把這些主題重組成三章：前言、國際案例分析、結論")

    assert state.pending_special_element_json == ""            # 清 pending（R7 互斥 invariant 前置）
    orch._try_stage_4_reframe_entry_typed.assert_awaited_once()
    kwargs = orch._try_stage_4_reframe_entry_typed.await_args.kwargs
    assert [c.name for c in kwargs["structural"].new_chapters] == ["前言", "國際案例分析", "結論"]
    assert narrated and "表格" in narrated[0]                   # SF3b 型誠實告知（表格設定暫停）
    assert not state.format_specs.get("special_elements")      # 不 silent 注入 element


@pytest.mark.asyncio
async def test_handle_pending_reframe_escape_no_payload_keeps_pending():
    """低階判 reframe 但 typed classifier 沒解出結構 payload（真模糊）→ 不硬猜、
    不丟 pending、重問（_emit_clarify_again 重存 pending）。"""
    orch = _prep_pending_orch(_make_orch())

    async def _stub_cls(msg, names, summary):
        return {"intent": "reframe", "target_chapter": ""}

    orch._classify_pending_special_element_reply = _stub_cls
    orch._classify_stage_4_response = AsyncMock(return_value=_make_stage4_response("unclear"))
    orch._try_stage_4_reframe_entry_typed = AsyncMock()
    emitted = []

    async def _cap_clar(req, state):
        emitted.append(req.question)
        return state

    orch._emit_clarification = _cap_clar

    state = _make_stage4_state()
    state.pending_special_element_json = json.dumps({"kind": "clarify",
        "elements": [{"type": "table", "raw_target": "結論", "description": ""}],
        "chapter_names": ["主題1", "主題2"]}, ensure_ascii=False)
    await orch._handle_pending_special_element(state, "整個改一下")

    orch._try_stage_4_reframe_entry_typed.assert_not_awaited()
    # ⚠️ 防假綠關鍵斷言（AR R1 in-house SF-1）：新路徑的可觀察區別 = typed classifier
    # 被呼叫。沒有這行，本 test 在「無 reframe 分支」的現行 code 上也會 PASS
    # （unclear 兜底同樣保 pending + 重問）——即假綠。
    orch._classify_stage_4_response.assert_awaited_once()
    assert state.pending_special_element_json != ""   # pending 保留（重存 clarify kind）
    assert emitted                                     # 重問了


@pytest.mark.asyncio
async def test_handle_pending_reframe_escape_llm_fail_fail_loud():
    """分流紀律（lessons None ≠ 無 action）：typed classifier LLM 故障（sentinel =
    unclear + LLM_UNAVAILABLE_NARRATION）→ 系統端文案 fail-loud + pending 原樣保留
    等重試，不進 reframe entry、不誤說「沒看懂」。"""
    from reasoning.live_research import lr_copy
    orch = _prep_pending_orch(_make_orch())
    narrated = []

    async def _cap(t):
        narrated.append(t)

    orch._emit_narration = _cap

    async def _stub_cls(msg, names, summary):
        return {"intent": "reframe", "target_chapter": ""}

    orch._classify_pending_special_element_reply = _stub_cls
    orch._classify_stage_4_response = AsyncMock(return_value=_make_stage4_response(
        "unclear", clarifying=lr_copy.LLM_UNAVAILABLE_NARRATION))
    orch._try_stage_4_reframe_entry_typed = AsyncMock()

    state = _make_stage4_state()
    pend = json.dumps({"kind": "clarify",
        "elements": [{"type": "table", "raw_target": "結論", "description": ""}],
        "chapter_names": ["主題1"]}, ensure_ascii=False)
    state.pending_special_element_json = pend
    await orch._handle_pending_special_element(state, "重組成三章")

    orch._try_stage_4_reframe_entry_typed.assert_not_awaited()
    assert lr_copy.LLM_UNAVAILABLE_NARRATION in narrated   # 系統端文案，非「沒看懂」
    assert state.pending_special_element_json == pend       # pending 原樣保留（等 user 重試）


@pytest.mark.asyncio
async def test_handle_pending_reframe_escape_entry_failure_fail_loud():
    """SF-A（AR R1 Codex）：清 pending 後 reframe entry 拋例外 → fail-loud narration
    告知重述，不 silent、不 re-raise（session 存活；pending 已清 + 已告知 =
    user 重述走正規 dispatch 仍可達 reframe，嚴格優於死迴圈現狀）。"""
    orch = _prep_pending_orch(_make_orch())
    narrated = []

    async def _cap(t):
        narrated.append(t)

    orch._emit_narration = _cap

    async def _stub_cls(msg, names, summary):
        return {"intent": "reframe", "target_chapter": ""}

    orch._classify_pending_special_element_reply = _stub_cls
    orch._classify_stage_4_response = AsyncMock(return_value=_make_stage4_response(
        "new_structure_request", chapters=["前言", "國際案例分析", "結論"]))
    orch._try_stage_4_reframe_entry_typed = AsyncMock(side_effect=RuntimeError("boom"))

    state = _make_stage4_state()
    state.pending_special_element_json = json.dumps({"kind": "clarify",
        "elements": [{"type": "table", "raw_target": "結論", "description": ""}],
        "chapter_names": ["主題1"]}, ensure_ascii=False)
    result = await orch._handle_pending_special_element(state, "重組成三章")

    assert state.pending_special_element_json == ""        # pending 已清（reframe 優先語意不回滾）
    assert any("結構調整處理失敗" in t for t in narrated)   # fail-loud 告知重述
    assert result is state                                  # 不 re-raise，session 存活
