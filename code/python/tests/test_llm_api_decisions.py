# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
LLM API Decision Tests (Golden Tests)

測試 Analyst agent 面對不同問法時，LLM 是否產生正確的 gap_resolutions。

兩種測試模式：
1. Mock LLM - 注入預設回應，測試整個流程
2. Live LLM - 實際呼叫 LLM，驗證行為（需要 API key）

Usage:
    # Mock 測試（快速，不需要 API）
    pytest tests/test_llm_api_decisions.py -v -k "mock"

    # Live 測試（慢，需要 API key）
    pytest tests/test_llm_api_decisions.py -v -k "live" --run-live
"""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Dict, Any, List

from reasoning.schemas_enhanced import (
    GapResolution,
    GapResolutionType,
    AnalystResearchOutputEnhanced,
)


# ==============================================================================
# Golden Test Cases: Expected LLM Behavior
# ==============================================================================

GOLDEN_TEST_CASES = [
    # 台股查詢
    {
        "id": "tw_stock_basic",
        "query": "台積電現在股價多少",
        "expected_resolution": "stock_tw",
        "expected_search_query_contains": ["2330"],
        "description": "台股查詢應使用 STOCK_TW",
    },
    {
        "id": "tw_stock_code",
        "query": "2330 股價",
        "expected_resolution": "stock_tw",
        "expected_search_query_contains": ["2330"],
        "description": "股票代碼應識別為台股",
    },
    {
        "id": "tw_stock_name",
        "query": "鴻海今天收盤價",
        "expected_resolution": "stock_tw",
        "expected_search_query_contains": ["2317"],
        "description": "台股公司名稱應轉換為代碼",
    },

    # 美股查詢
    {
        "id": "us_stock_ticker",
        "query": "NVIDIA 股價",
        "expected_resolution": "stock_global",
        "expected_search_query_contains": ["NVDA"],
        "description": "美股 ticker 應使用 STOCK_GLOBAL",
    },
    {
        "id": "us_stock_name",
        "query": "Apple 現在股價多少",
        "expected_resolution": "stock_global",
        "expected_search_query_contains": ["AAPL"],
        "description": "美股公司名稱應轉換為 ticker",
    },

    # 台灣天氣
    {
        "id": "tw_weather_city",
        "query": "台北今天天氣如何",
        "expected_resolution": "weather_tw",
        "expected_search_query_contains": ["台北", "臺北"],
        "description": "台灣城市天氣應使用 WEATHER_TW",
    },
    {
        "id": "tw_weather_forecast",
        "query": "高雄明天會下雨嗎",
        "expected_resolution": "weather_tw",
        "expected_search_query_contains": ["高雄"],
        "description": "台灣天氣預報應使用 WEATHER_TW",
    },

    # 全球天氣
    {
        "id": "global_weather",
        "query": "東京現在天氣",
        "expected_resolution": "weather_global",
        "expected_search_query_contains": ["Tokyo", "東京"],
        "description": "非台灣城市應使用 WEATHER_GLOBAL",
    },

    # LLM 知識 vs Web Search
    {
        "id": "static_definition",
        "query": "什麼是 EUV 技術",
        "expected_resolution": "llm_knowledge",
        "description": "技術定義應使用 LLM_KNOWLEDGE",
    },
    {
        "id": "dynamic_ceo",
        "query": "亞馬遜現任 CEO 是誰",
        "expected_resolution": "web_search",
        "description": "現任職位應使用 WEB_SEARCH",
    },
    {
        "id": "dynamic_price",
        "query": "比特幣最新價格",
        "expected_resolution": "web_search",
        "description": "最新價格應使用 WEB_SEARCH",
    },

    # Wikipedia
    {
        "id": "company_background",
        "query": "NVIDIA 公司背景介紹",
        "expected_resolution": "wikipedia",
        "description": "公司背景應使用 WIKIPEDIA",
    },
]

# 複合型查詢測試案例（需要多個 API）
COMPLEX_TEST_CASES = [
    {
        "id": "investment_analysis",
        "query": "分析台積電的投資價值，包括目前股價和公司背景",
        "expected_resolutions": ["stock_tw", "wikipedia"],
        "description": "投資分析需要股價 + 背景",
    },
    {
        "id": "travel_planning",
        "query": "我下週要去東京旅遊，請問天氣如何？有什麼景點推薦？",
        "expected_resolutions": ["weather_global", "wikipedia"],
        "description": "旅遊規劃需要天氣 + 景點資訊",
    },
    {
        "id": "tech_comparison",
        "query": "比較 NVIDIA 和台積電的股價表現",
        "expected_resolutions": ["stock_global", "stock_tw"],
        "description": "股票比較需要兩個股價 API",
    },
]


# ==============================================================================
# Mock Handler and Config
# ==============================================================================

def create_mock_handler(for_live_test=False):
    """
    Create a mock handler for testing.

    Args:
        for_live_test: If True, create a handler suitable for live LLM calls
    """
    handler = MagicMock()
    handler.site = "test"
    handler.query = "test query"
    handler.query_id = "test-123"

    if for_live_test:
        # For live tests, use real dict instead of MagicMock
        handler.query_params = {}
        handler.llm_config = {}
    else:
        handler.llm_config = {"model": "gpt-4"}

    return handler


def get_mock_config():
    """Create mock CONFIG for testing."""
    mock_config = MagicMock()
    mock_config.reasoning_params = {
        "features": {
            "argument_graphs": True,
            "gap_knowledge_enrichment": True,
        },
        "tier_6": {
            "twse": {"enabled": True},
            "yfinance": {"enabled": True},
            "cwb_weather": {"enabled": True},
            "global_weather": {"enabled": True},
            "wikipedia": {"enabled": True},
        }
    }
    return mock_config


# ==============================================================================
# Mock LLM Response Generator
# ==============================================================================

def generate_mock_llm_response(
    resolution_type: str,
    search_query: str = None,
    llm_answer: str = None
) -> Dict[str, Any]:
    """Generate a mock LLM response with specified gap_resolution."""
    gap_resolution = {
        "gap_type": "current_data" if resolution_type in ["stock_tw", "stock_global", "weather_tw", "weather_global", "web_search"] else "definition",
        "resolution": resolution_type,
        "reason": f"Mock reason for {resolution_type}",
        "confidence": "high",
    }

    if search_query:
        gap_resolution["search_query"] = search_query
    if llm_answer:
        gap_resolution["llm_answer"] = llm_answer

    return {
        "status": "DRAFT_READY",
        "draft": "這是一個測試用的草稿內容，提供足夠長度以通過 Pydantic 驗證。系統正在處理您的查詢，請稍候。" * 2,
        "citations_used": [],
        "new_queries": [],
        "missing_information": [],
        "reasoning_chain": f"識別出需要 {resolution_type} 來解決此查詢",
        "gap_resolutions": [gap_resolution],
    }


# ==============================================================================
# Test Class: Mock LLM Tests
# ==============================================================================

class TestMockLLMDecisions:
    """Test LLM decisions using mock responses."""

    @pytest.mark.parametrize("test_case", GOLDEN_TEST_CASES, ids=lambda tc: tc["id"])
    def test_gap_resolution_parsing(self, test_case):
        """Test that gap_resolutions can be parsed correctly for each case."""
        # Generate mock response
        search_query = test_case.get("expected_search_query_contains", ["test"])[0]
        llm_answer = "Mock LLM answer" if test_case["expected_resolution"] == "llm_knowledge" else None

        mock_response = generate_mock_llm_response(
            resolution_type=test_case["expected_resolution"],
            search_query=search_query if test_case["expected_resolution"] != "llm_knowledge" else None,
            llm_answer=llm_answer
        )

        # Parse response
        output = AnalystResearchOutputEnhanced(**mock_response)

        # Validate
        assert len(output.gap_resolutions) == 1, f"Expected 1 gap_resolution for {test_case['id']}"
        gap = output.gap_resolutions[0]
        assert gap.resolution.value == test_case["expected_resolution"], \
            f"Expected {test_case['expected_resolution']}, got {gap.resolution.value}"

    @pytest.mark.asyncio
    async def test_analyst_with_mock_llm(self):
        """Test Analyst agent with mocked LLM response."""
        mock_llm_response = generate_mock_llm_response(
            resolution_type="stock_tw",
            search_query="2330"
        )

        with patch('reasoning.agents.base.ask_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_llm_response)

            with patch('core.config.CONFIG', get_mock_config()):
                from reasoning.agents.analyst import AnalystAgent

                handler = create_mock_handler()
                agent = AnalystAgent(handler, timeout=10)

                result = await agent.research(
                    query="台積電股價",
                    formatted_context="[1] 測試文件內容",
                    mode="discovery",
                    enable_web_search=True
                )

                # Verify LLM was called
                assert mock_llm.called

                # Verify result has gap_resolutions
                assert hasattr(result, 'gap_resolutions')
                assert len(result.gap_resolutions) == 1
                assert result.gap_resolutions[0].resolution == GapResolutionType.STOCK_TW


# ==============================================================================
# Test Class: Prompt Content Tests
# ==============================================================================

class TestPromptContent:
    """Test that prompts contain correct API routing instructions."""

    def test_prompt_contains_stock_tw_instructions(self):
        """Verify prompt mentions STOCK_TW for Taiwan stocks."""
        from reasoning.prompts.analyst import AnalystPromptBuilder

        builder = AnalystPromptBuilder()
        prompt = builder.build_research_prompt(
            query="台積電股價",
            formatted_context="[1] test",
            mode="discovery",
            enable_gap_enrichment=True,
            enable_web_search=True
        )

        # Check prompt mentions stock-related resolution types
        assert "stock" in prompt.lower() or "股" in prompt

    def test_prompt_contains_weather_instructions(self):
        """Verify prompt mentions weather API options."""
        from reasoning.prompts.analyst import AnalystPromptBuilder

        builder = AnalystPromptBuilder()
        prompt = builder.build_research_prompt(
            query="天氣預報",
            formatted_context="[1] test",
            mode="discovery",
            enable_gap_enrichment=True,
            enable_web_search=True
        )

        # Prompt should contain guidance about weather queries
        assert "weather" in prompt.lower() or "天氣" in prompt


# ==============================================================================
# Test Class: Live LLM Tests (Optional)
# ==============================================================================

class TestLiveLLMDecisions:
    """
    Live LLM tests - actually call the LLM to verify behavior.

    Run with: pytest tests/test_llm_api_decisions.py -v -k "live"
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_case", GOLDEN_TEST_CASES, ids=lambda tc: tc["id"])
    async def test_live_llm_single_api(self, test_case):
        """Test LLM decision for single-API queries (all 12 cases)."""
        from reasoning.agents.analyst import AnalystAgent

        handler = create_mock_handler(for_live_test=True)
        handler.query = test_case["query"]

        agent = AnalystAgent(handler, timeout=120)

        result = await agent.research(
            query=test_case["query"],
            formatted_context="[1] 這是測試用的上下文資料。",
            mode="discovery",
            enable_web_search=True
        )

        # Verify gap_resolutions
        assert hasattr(result, 'gap_resolutions'), f"No gap_resolutions for {test_case['id']}"

        if result.gap_resolutions:
            gap = result.gap_resolutions[0]
            assert gap.resolution.value == test_case["expected_resolution"], \
                f"Expected {test_case['expected_resolution']}, got {gap.resolution.value} for query: {test_case['query']}"

            # Check search_query contains expected terms
            if "expected_search_query_contains" in test_case and gap.search_query:
                found = any(term in gap.search_query for term in test_case["expected_search_query_contains"])
                assert found, f"search_query '{gap.search_query}' should contain one of {test_case['expected_search_query_contains']}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_case", COMPLEX_TEST_CASES, ids=lambda tc: tc["id"])
    async def test_live_llm_multi_api(self, test_case):
        """Test LLM decision for complex queries requiring multiple APIs."""
        from reasoning.agents.analyst import AnalystAgent

        handler = create_mock_handler(for_live_test=True)
        handler.query = test_case["query"]

        agent = AnalystAgent(handler, timeout=120)

        result = await agent.research(
            query=test_case["query"],
            formatted_context="[1] 這是測試用的上下文資料。",
            mode="discovery",
            enable_web_search=True
        )

        # Verify gap_resolutions
        assert hasattr(result, 'gap_resolutions'), f"No gap_resolutions for {test_case['id']}"
        assert len(result.gap_resolutions) >= 2, \
            f"Expected multiple gap_resolutions for complex query, got {len(result.gap_resolutions)}"

        # Check that expected resolution types are present
        actual_resolutions = [gap.resolution.value for gap in result.gap_resolutions]
        for expected in test_case["expected_resolutions"]:
            assert expected in actual_resolutions, \
                f"Expected {expected} in resolutions, got {actual_resolutions} for query: {test_case['query']}"


# ==============================================================================
# Test Class: Integration with Routing
# ==============================================================================

class TestEndToEndRouting:
    """Test full flow from LLM response to API routing."""

    def test_stock_tw_full_flow(self):
        """Test: Query -> LLM Response -> Gap Resolution -> Routing -> TWSE."""
        from tests.test_agent_api_routing import route_gap_resolutions

        # Simulate LLM response
        mock_response = generate_mock_llm_response(
            resolution_type="stock_tw",
            search_query="2330"
        )

        # Parse to model
        output = AnalystResearchOutputEnhanced(**mock_response)

        # Route
        routed = route_gap_resolutions(output.gap_resolutions)

        # Verify routing
        assert len(routed["stock_tw"]) == 1
        assert routed["stock_tw"][0].search_query == "2330"
        assert routed["stock_global"] == []
        assert routed["web_search"] == []

    def test_mixed_query_full_flow(self):
        """Test: Complex query requiring multiple APIs."""
        from tests.test_agent_api_routing import route_gap_resolutions

        # Simulate LLM response with multiple gap_resolutions
        mock_response = {
            "status": "DRAFT_READY",
            "draft": "這是一個測試用的草稿內容，提供足夠長度以通過 Pydantic 驗證。" * 3,
            "citations_used": [],
            "new_queries": [],
            "missing_information": [],
            "reasoning_chain": "複合查詢需要多個 API",
            "gap_resolutions": [
                {
                    "gap_type": "current_data",
                    "resolution": "stock_tw",
                    "search_query": "2330",
                    "reason": "需要台積電股價"
                },
                {
                    "gap_type": "background",
                    "resolution": "wikipedia",
                    "search_query": "TSMC",
                    "reason": "需要公司背景"
                },
                {
                    "gap_type": "current_data",
                    "resolution": "web_search",
                    "search_query": "台積電 法說會 2024",
                    "reason": "需要最新新聞"
                }
            ]
        }

        # Parse
        output = AnalystResearchOutputEnhanced(**mock_response)

        # Route
        routed = route_gap_resolutions(output.gap_resolutions)

        # Verify all three APIs are triggered
        assert len(routed["stock_tw"]) == 1
        assert len(routed["wikipedia"]) == 1
        assert len(routed["web_search"]) == 1
