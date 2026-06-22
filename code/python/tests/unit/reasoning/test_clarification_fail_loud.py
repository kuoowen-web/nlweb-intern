import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_clarification_degradation_is_marked_not_silent():
    """LLM 失敗時走 fallback，但回傳必須帶明確降級標記（非 silent）。"""
    from reasoning.agents.clarification import ClarificationAgent
    import logging

    agent = ClarificationAgent.__new__(ClarificationAgent)
    agent.logger = logging.getLogger("test")

    class _H:
        query_params = {}

    agent.handler = _H()

    # generate_options(self, query, ambiguity_type="time")，無 context 參數
    with patch("core.llm.ask_llm", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await agent.generate_options(query="模糊查詢", ambiguity_type="time")
    assert "options" in result
    assert result.get("metadata", {}).get("degraded") is True
    assert result["metadata"].get("reason")
