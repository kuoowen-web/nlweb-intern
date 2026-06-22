# test_lr_revision_intent_recollect.py
import asyncio
from unittest.mock import MagicMock, patch
from reasoning.live_research.orchestrator import LiveResearchOrchestrator


def test_revision_intent_schema_enum_includes_recollect():
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = False
    orch.handler = MagicMock()
    orch.handler.query_params = {}

    captured = {}
    async def fake_ask_llm(prompt, schema, **kw):
        captured["schema"] = schema
        return {"action": "recollect", "reason": "資料不足"}

    with patch("core.llm.ask_llm", new=fake_ask_llm):
        result = asyncio.run(orch._parse_revision_intent("這部分資料不夠，去多查一些", []))

    enum = captured["schema"]["properties"]["action"]["enum"]
    assert "recollect" in enum, f"schema enum 必須含 recollect, got {enum}"
    assert result["action"] == "recollect"
