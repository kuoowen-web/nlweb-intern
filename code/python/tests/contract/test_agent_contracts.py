"""
Contract tests for Live Research agents — Phase B.

Each test makes one real LLM call and validates the response
against the expected Pydantic schema.

Requires:
  - Valid API key (OPENAI_API_KEY or NLWEB_ANTHROPIC_API_KEY) in .env
  - @pytest.mark.contract marker for selective runs

Run:
    cd code/python && python -m pytest tests/contract/test_agent_contracts.py -v -m contract

Skip conditions:
  - API key unavailable → test is SKIPPED (not FAILED)
  - API call fails with network error → test is SKIPPED

Cost estimate: ~8 LLM calls × ~500 tokens = ~$0.01-0.15 depending on model
"""

import os
import sys
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# ──────────────────────────────────────────────────────────────────────────────
# Load .env (must run before importing core.config)
# ──────────────────────────────────────────────────────────────────────────────

# [interface: unit-llm-safe] 只有顯式 opt-in 才注入真 key；預設由 conftest 中和為空。
_ALLOW_REAL_LLM = os.environ.get("NLWEB_ALLOW_REAL_LLM", "").strip() == "1"
if _ALLOW_REAL_LLM:
    try:
        from dotenv import load_dotenv
        # Try multiple candidate paths for .env (Git repo root)
        _candidates = [
            Path(__file__).parent.parent.parent.parent.parent / ".env",  # 5 levels up
            Path(__file__).parent.parent.parent.parent / ".env",         # 4 levels up
            Path(__file__).parent.parent.parent / ".env",                # code/python/.env
            Path("C:/users/user/nlweb/.env"),                            # Windows absolute
        ]
        for _candidate in _candidates:
            # Use str() with forward slashes to avoid Windows path issues
            _candidate_str = str(_candidate).replace("\\", "/")
            _candidate_win = str(_candidate).replace("/", "\\")
            for _p_str in [_candidate_str, _candidate_win]:
                _p = Path(_p_str)
                if _p.exists():
                    load_dotenv(_p_str, override=True)
                    break
    except ImportError:
        pass  # dotenv not available, rely on environment

# ──────────────────────────────────────────────────────────────────────────────
# Pytest marker configuration
# ──────────────────────────────────────────────────────────────────────────────

pytestmark = pytest.mark.contract


def _api_key_available() -> bool:
    """Check if any usable LLM API key is available."""
    keys = [
        "OPENAI_API_KEY",
        "NLWEB_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_OPENAI_API_KEY",
    ]
    return any(bool(os.environ.get(k, "").strip()) for k in keys)


def skip_if_no_api(reason: str = "No LLM API key available"):
    """Decorator to skip tests when no API key is set."""
    return pytest.mark.skipif(not _api_key_available(), reason=reason)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mock_handler():
    """Minimal mock handler for agent instantiation."""
    handler = MagicMock()
    handler.query = "台灣綠能發展"
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=True)
    handler.query_params = {}
    handler.site = "all"
    return handler


@pytest.fixture(scope="module")
def minimal_context_map():
    """A minimal ContextMap for derive/refine tests."""
    from reasoning.schemas_live import ContextMap, ContextMapTopic
    return ContextMap(
        research_question="台灣綠能發展",
        working_hypothesis="再生能源擴張與在地衝突並存",
        topics=[
            ContextMapTopic(
                topic_id="t1",
                name="太陽能土地使用",
                domain="能源政策",
                relevance="core",
                description="太陽能板設置對農地的影響",
            ),
            ContextMapTopic(
                topic_id="t2",
                name="社區參與機制",
                domain="公民社會",
                relevance="supporting",
                description="地方居民參與能源決策的管道",
            ),
        ],
        version=0,
    )


@pytest.fixture(scope="module")
def sample_retrieval_results():
    """Sample formatted retrieval results string for refine tests."""
    return """[1] 台灣太陽能板爭議報告
太陽能板設置與農地保育產生衝突，2022年超過30案件引發訴訟。
URL: https://example.com/solar-conflict

[2] 社區參與能源政策研究
公民參與綠能規劃能降低73%的抗議事件，提升在地接受度。
URL: https://example.com/community-energy
"""


# ──────────────────────────────────────────────────────────────────────────────
# Helper: skip-on-API-error wrapper
# ──────────────────────────────────────────────────────────────────────────────

async def _call_with_skip_on_api_error(coro):
    """
    Run async coro, converting genuine network/API-availability errors to pytest.skip().

    收窄（2026-06-11）：timeout 類錯誤**不再**豁免成 skip。timeout 代表
    「測試預算 < 模型工作量」或真 hang，是必須浮現的訊號（過去被吞成 skip
    造成假綠燈）。timeout / LLMError(timeout) → 往上拋，由 pytest 記為失敗。
    僅「API key 缺失 / 未授權 / 連線不通」等環境性不可用才 skip。
    """
    try:
        return await coro
    except (TimeoutError, asyncio.TimeoutError):
        raise  # timeout 不豁免：必須浮現為失敗
    except Exception as e:
        err_str = str(e).lower()
        # timeout 訊息即使包在一般 Exception 也不豁免
        if "timeout" in err_str or "timed out" in err_str:
            raise
        # 僅環境性不可用（key/授權/連線 + provider 5xx 服務端不穩）才 skip。
        # AR round 1（in-house/Gemini）：保留 provider 5xx 覆蓋 —— 移掉寬鬆的 "openai"
        # 兜底後，OpenAI SDK 的 InternalServerError/503/overloaded 等「服務端暫時不穩」
        # 訊息未必含 key/連線關鍵字，若不補會由 SKIP 變 FAIL 製造 flaky。改用精準的
        # 5xx / service-unavailable 關鍵字涵蓋，不再用 "openai" 寬鬆字串（會誤吞含
        # "openai" 的 schema 錯誤）。
        if any(kw in err_str for kw in [
            "api key", "apikey", "unauthorized", "authentication",
            "rate limit", "quota", "connection",
            "network", "ssl", "httpx", "aiohttp",
            # provider 服務端暫時不可用（環境性 flaky，非測試邏輯錯）→ skip
            "service unavailable", "internal server error", "overloaded",
            "502", "503", "504", "error code: 500", "status code: 500",
        ]):
            pytest.skip(f"LLM API unavailable (SKIP): {e}")
        if "not set" in err_str or "not found" in err_str:
            pytest.skip(f"Config/API unavailable (SKIP): {e}")
        raise  # schema validation / logic / empty-response 等照常 FAIL


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 1: AssociatorAgent.build_context_map
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(330)  # config analyst_timeout(300) + 30s 緩衝；見 Task 3 marker sweep
@pytest.mark.asyncio
async def test_contract_associator_build_context_map(mock_handler):
    """
    Real LLM call: AssociatorAgent.build_context_map("台灣綠能發展")
    → AssociatorBuildOutput valid (has context_map with ≥1 topic, narration non-empty)
    """
    from reasoning.agents.associator import AssociatorAgent
    from reasoning.schemas_live import AssociatorBuildOutput
    from core.config import CONFIG

    # 對齊 prod：prod AssociatorAgent timeout 由 config analyst_timeout 驅動
    # (reasoning/live_research/orchestrator.py:650 CONFIG.reasoning_params.get("analyst_timeout", 90))
    # AR round 1：不用 .get(fallback) —— 本 test 的意義就是對齊 config 的 prod timeout，
    # config 缺鍵時對齊前提已破，應 fail-loud（KeyError 浮現），不可 silent fallback 遮蔽 regression。
    associator_timeout = CONFIG.reasoning_params["analyst_timeout"]
    agent = AssociatorAgent(handler=mock_handler, timeout=associator_timeout)

    async def _run():
        return await agent.build_context_map(query="台灣綠能發展")

    result = await _call_with_skip_on_api_error(_run())

    assert isinstance(result, AssociatorBuildOutput), f"Expected AssociatorBuildOutput, got {type(result)}"
    assert result.context_map is not None
    assert len(result.context_map.topics) >= 1, "Expected at least 1 topic in ContextMap"
    assert result.narration, "narration should not be empty"
    assert result.context_map.research_question, "research_question should be set"


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 2: AssociatorAgent.derive_search_plan
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_associator_derive_search_plan(mock_handler, minimal_context_map):
    """
    Real LLM call: AssociatorAgent.derive_search_plan(context_map)
    → AssociatorDeriveOutput valid (has ≥1 search_seeds, narration non-empty)
    """
    from reasoning.agents.associator import AssociatorAgent
    from reasoning.schemas_live import AssociatorDeriveOutput

    agent = AssociatorAgent(handler=mock_handler, timeout=60)

    async def _run():
        return await agent.derive_search_plan(
            context_map=minimal_context_map,
            executed_searches=[],
        )

    result = await _call_with_skip_on_api_error(_run())

    assert isinstance(result, AssociatorDeriveOutput), f"Expected AssociatorDeriveOutput, got {type(result)}"
    assert len(result.search_seeds) >= 1, "Expected at least 1 search seed"
    assert result.narration, "narration should not be empty"
    # Each seed should have a non-empty query
    for seed in result.search_seeds:
        assert seed.query, "search seed query should not be empty"
        assert seed.target_topic_id, "search seed target_topic_id should not be empty"


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 3: AssociatorAgent.refine_context_map
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_associator_refine_context_map(
    mock_handler, minimal_context_map, sample_retrieval_results
):
    """
    Real LLM call: AssociatorAgent.refine_context_map(map + results)
    → AssociatorRefineOutput valid (updated_context_map, delta, is_stable, narration)
    """
    from reasoning.agents.associator import AssociatorAgent
    from reasoning.schemas_live import AssociatorRefineOutput

    agent = AssociatorAgent(handler=mock_handler, timeout=60)

    async def _run():
        return await agent.refine_context_map(
            current_context_map=minimal_context_map,
            initial_context_map=minimal_context_map,
            retrieval_results=sample_retrieval_results,
        )

    result = await _call_with_skip_on_api_error(_run())

    assert isinstance(result, AssociatorRefineOutput), f"Expected AssociatorRefineOutput, got {type(result)}"
    assert result.updated_context_map is not None
    assert len(result.updated_context_map.topics) >= 1
    assert result.delta is not None
    assert isinstance(result.is_stable, bool)
    assert result.narration, "narration should not be empty"


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 4: AnalystAgent.research (live mode)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_analyst_research_live_mode(mock_handler, sample_retrieval_results):
    """
    Real LLM call: AnalystAgent.research(query, formatted_context, mode="discovery",
                                         enable_live_research=True)
    → AnalystResearchOutput valid (has draft, reasoning)
    """
    from reasoning.agents.analyst import AnalystAgent
    from reasoning.schemas import AnalystResearchOutput

    agent = AnalystAgent(handler=mock_handler, timeout=60)

    async def _run():
        return await agent.research(
            query="台灣綠能發展面臨什麼挑戰？",
            formatted_context=sample_retrieval_results,
            mode="discovery",
            enable_live_research=True,
        )

    result = await _call_with_skip_on_api_error(_run())

    assert result is not None, "Analyst research should return a result"
    assert hasattr(result, "draft"), "Result should have a 'draft' field"
    assert result.draft, "draft should not be empty"


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 5: CriticAgent.review (live mode)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_critic_review_live_mode(mock_handler, sample_retrieval_results):
    """
    Real LLM call: CriticAgent.review(draft, query, mode="discovery", enable_live_research=True)
    → CriticReviewOutput valid (verdict, feedback)
    """
    from reasoning.agents.critic import CriticAgent

    agent = CriticAgent(handler=mock_handler, timeout=60)

    sample_draft = (
        "台灣綠能發展面臨土地使用和社區衝突兩大挑戰。"
        "太陽能板設置影響農地保育，而社區參與機制尚待完善。"
        "根據研究，公民參與能顯著降低抗議事件。[1][2]"
    )

    async def _run():
        return await agent.review(
            draft=sample_draft,
            query="台灣綠能發展面臨什麼挑戰？",
            mode="discovery",
            formatted_context=sample_retrieval_results,
            enable_live_research=True,
        )

    result = await _call_with_skip_on_api_error(_run())

    assert result is not None, "Critic review should return a result"
    # CriticReviewOutput uses 'status' field (Literal['PASS', 'WARN', 'REJECT'])
    assert hasattr(result, "status"), "Result should have a 'status' field"
    assert result.status in ("PASS", "WARN", "REJECT"), f"Unexpected status: {result.status}"
    assert result.critique, "critique should not be empty"


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 6: StyleAnalysis prompt + ask_llm
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_style_analysis_prompt(mock_handler):
    """
    Real LLM call: orchestrator._run_style_analysis(sample_text) → StyleAnalysisOutput valid.

    Tests the full production code path (StyleAnalysisPromptBuilder + ask_llm + model_validate)
    as it runs in LiveResearchOrchestrator._run_style_analysis().
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import StyleAnalysisOutput
    from unittest.mock import patch

    sample_text = (
        "台灣的能源政策正處於轉型的十字路口。"
        "面對氣候變遷的迫切壓力，再生能源的擴張已不可逆轉；"
        "然而，快速建設帶來的土地衝突，卻成為政策推動者難以迴避的隱憂。"
        "這不是技術問題，而是治理問題——如何在速度與包容之間取得平衡。"
    )

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=mock_handler)

    async def _run():
        return await orch._run_style_analysis(sample_text)

    result = await _call_with_skip_on_api_error(_run())

    assert isinstance(result, StyleAnalysisOutput)
    # prod blocker fix 2026-05-30: min_length 3→1（sparse 範本 1 個 feature 亦合法）
    assert len(result.features) >= 1, f"Expected ≥1 feature, got {len(result.features)}"
    assert result.overall_tone, "overall_tone should not be empty"
    for feature in result.features:
        assert feature.dimension, "Each feature should have a dimension"
        assert feature.observation, "Each feature should have an observation"
        assert feature.instruction, "Each feature should have an instruction"


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 6b: StyleAnalysis on SPARSE input (prod blocker regression guard)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_style_analysis_sparse_input(mock_handler):
    """Real LLM call: _run_style_analysis on an extremely short / sparse sample
    must NOT crash with ValidationError and must yield ≥1 feature.

    Regression guard for the prod blocker where sparse input made the LLM return
    <3 features → StyleAnalysisOutput min_length=3 ValidationError → whole LR aborted.
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import StyleAnalysisOutput
    from unittest.mock import patch

    # Deliberately sparse: a single short sentence — minimal style signal.
    # O7 適配（2026-06-11 review 收斂）：原樣本「今天天氣很好。」在 O7 input-type
    # 守門下屬「閒聊 vs 極短範本」邊界，真 LLM 判定不穩定。換成明確是文章片段的
    # sparse 單句——regression guard 意圖不變（sparse 輸入不得炸 ValidationError）。
    sparse_sample = "夜市的燈火亮起，攤販的吆喝聲此起彼落，城市的另一種生活才正要開始。"

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=mock_handler)

    async def _run():
        return await orch._run_style_analysis(sparse_sample)

    # Must not raise ValidationError (schema validation errors propagate, not skipped).
    result = await _call_with_skip_on_api_error(_run())

    assert isinstance(result, StyleAnalysisOutput)
    assert len(result.features) >= 1, (
        f"Sparse input must still produce ≥1 feature (no hard crash), "
        f"got {len(result.features)}"
    )
    assert result.overall_tone, "overall_tone should not be empty"
    # O7：明確的文章片段不得被守門誤判為非範本（防 false 過度觸發）。
    assert result.input_is_writing_sample is True


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 7: WriterAgent section (live research mode)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_writer_section_live_mode(mock_handler, minimal_context_map):
    """
    Real LLM call: WriterAgent.call_llm_validated with LiveWriterSectionOutput schema.
    → LiveWriterSectionOutput valid (section_title, section_content, sources_used)
    """
    from reasoning.agents.writer import WriterAgent
    from reasoning.schemas_live import LiveWriterSectionOutput, context_map_extract_for_section

    agent = WriterAgent(handler=mock_handler, timeout=60)

    topic = minimal_context_map.topics[0]  # 太陽能土地使用
    section_context = context_map_extract_for_section(minimal_context_map, [topic.topic_id])

    prompt = f"""你正在撰寫一份研究報告的其中一個段落。

## 段落主題
{topic.name}：{topic.description}

## 研究上下文（來自 Context Map）
{section_context}

## 輸出要求
- 使用繁體中文撰寫
- section_title：段落標題
- section_content：完整的 Markdown 段落內容（至少2-3句話）
- sources_used：使用的來源 ID 列表（可為空）
- confidence_level：High/Medium/Low
- narration：簡述撰寫考量（繁體中文）
"""

    async def _run():
        result, _, _ = await agent.call_llm_validated(
            prompt=prompt,
            response_schema=LiveWriterSectionOutput,
            level="high",
        )
        return result

    result = await _call_with_skip_on_api_error(_run())

    assert isinstance(result, LiveWriterSectionOutput), f"Expected LiveWriterSectionOutput, got {type(result)}"
    assert result.section_title, "section_title should not be empty"
    assert result.section_content, "section_content should not be empty"
    assert len(result.section_content) >= 20, "section_content should have meaningful content"
    assert result.confidence_level in ("High", "Medium", "Low")


# ──────────────────────────────────────────────────────────────────────────────
# Contract Test 8: Intent parsing (style confirmation)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.contract
@skip_if_no_api()
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_contract_intent_parsing_style_confirmation(mock_handler):
    """
    Real LLM call: _parse_style_confirmation_intent with "confirm" user message.
    → Intent dict with valid 'action' field (confirm/adjust)
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature
    from unittest.mock import patch

    # Build orchestrator with mocked AssociatorAgent
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=mock_handler)

    # Build a sample style features JSON to confirm
    style = StyleAnalysisOutput(
        features=[
            StyleFeature(
                dimension="句式結構",
                observation="使用短句，平均15字一句",
                instruction="維持短句風格，每句不超過20字",
            ),
            StyleFeature(
                dimension="用詞層次",
                observation="結合學術術語與白話說明",
                instruction="學術詞彙後立即用括號補充白話解釋",
            ),
            StyleFeature(
                dimension="段落節奏",
                observation="先結論後論據的倒三角結構",
                instruction="每段首句為核心論點，次句為論據，末句為呼應",
            ),
        ],
        overall_tone="學術嚴謹但不失可讀性",
    )

    user_confirm_msg = "分析很準確，就這樣吧"

    async def _run():
        return await orch._parse_style_confirmation_intent(
            user_message=user_confirm_msg,
            style_features_json=style.model_dump_json(),
        )

    result = await _call_with_skip_on_api_error(_run())

    # #21 契約更新：_parse_style_confirmation_intent 現在回 Optional[dict]。
    # API 失敗時回 None（不拋 exception）；成功時回含 action 的 dict。
    # 若 result 為 None，表示此次 API call 失敗，視同 skip（不強制 fail）。
    if result is None:
        pytest.skip("_parse_style_confirmation_intent returned None (LLM API unavailable)")

    assert isinstance(result, dict), f"Expected dict or None, got {type(result)}"
    assert "action" in result, f"Expected 'action' key in result, got keys: {list(result.keys())}"
    assert result["action"] in ("confirm", "adjust"), \
        f"Expected action in (confirm, adjust), got: {result['action']}"

    # For a clear "confirm" message, action should be "confirm"
    # (LLM contract: natural language must correctly interpret this as confirmation)
    assert result["action"] == "confirm", \
        f"Expected 'confirm' action for clear confirmation message, got: {result['action']}"
