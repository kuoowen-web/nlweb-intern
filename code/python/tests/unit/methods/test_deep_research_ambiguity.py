"""DeepResearchHandler._detect_all_ambiguities LLMError 降級明確訊息（不 silent fail）。

背景：LLMError sentinel（falsy dict）上線後，ask_llm 失敗回 LLMError → 原
`response.get('questions', [])` 回 [] → 走「No ambiguities detected」info log，
把 LLM 故障偽裝成正常無歧義結果（silent fail，舊版 None.get AttributeError 的
error log 也消失）。修法：在 response.get 前加 isinstance(response, LLMError) 分支，
留 error 級訊息 + 降級（仍回 [] proceed without clarification，降級方向不變）。

驗 plumbing（故障→降級路徑 + 明確訊息），不驗 LLM 判斷力本身（ask_llm 全 mock）。
注意：專案 custom JSON logger 走背景 worker thread + 直寫 stream，會繞過 caplog，
故用 capfd 抓 fd 級輸出（對照既有 LLMError-typing 測試的斷言方式）。
"""
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# Insert `code/python` into sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def _bare_handler(query="弗萊堡風場現況"):
    """繞過重量級 super().__init__，只填 _detect_all_ambiguities 用到的 attr。"""
    from methods.deep_research import DeepResearchHandler
    h = DeepResearchHandler.__new__(DeepResearchHandler)
    h.query = query
    h.query_params = {}
    h.temporal_range = None
    return h


@pytest.mark.asyncio
async def test_detect_ambiguities_llmerror_degrades_with_explicit_error(monkeypatch):
    """ask_llm 回 LLMError → 回 [] 降級 + error 級訊息出（不 silent 偽裝成無歧義）。

    ordering 免疫（full-scan-2026-07 收尾）：degraded 訊息由 deep_research 的
    module-level LazyLogger（get_configured_logger）走背景 worker thread emit，原用
    capfd + sleep(0.2) 賭 fd flush → 全套 ordering 下 fd-capture race + timing flaky。
    改 patch module logger.error 直攔呼叫捕捉訊息，繞過背景 worker + fd（lessons-
    testing-review §184 輕解）。行為斷言逐字不變（訊息含 marker + error_kind）。"""
    import methods.deep_research as dr_mod
    from core.llm import LLMError

    async def _fake_ask_llm(*a, **k):
        return LLMError("timeout", "x")

    monkeypatch.setattr("core.llm.ask_llm", _fake_ask_llm)

    err_calls = []
    orig_error = dr_mod.logger.error
    monkeypatch.setattr(
        dr_mod.logger, "error",
        lambda msg, *a, **k: (err_calls.append(str(msg)), orig_error(msg, *a, **k))[1],
    )

    handler = _bare_handler()
    result = await handler._detect_all_ambiguities()

    assert result == [], "LLMError 故障降級：仍回 [] proceed without clarification"

    combined = " ".join(err_calls)
    assert "[AMBIGUITY] Detection degraded" in combined, \
        "故障必須留明確 error 訊息，不可 silent 偽裝成『無歧義』"
    assert "timeout" in combined, "訊息須帶 error_kind（哪種故障）"


@pytest.mark.asyncio
async def test_detect_ambiguities_success_returns_questions(monkeypatch, capfd):
    """對照組：ask_llm 成功回問題 → 正常回 questions，不走降級分支。"""
    async def _fake_ask_llm(*a, **k):
        return {"questions": [{
            "clarification_type": "scope",
            "question": "你想看哪個面向？",
            "required": True,
            "options": [
                {"label": "技術", "intent": "tech", "query_modifier": "技術"},
                {"label": "政策", "intent": "policy", "query_modifier": "政策"},
            ],
        }]}

    monkeypatch.setattr("core.llm.ask_llm", _fake_ask_llm)

    handler = _bare_handler()
    result = await handler._detect_all_ambiguities()

    assert len(result) == 1
    assert result[0]["question_id"] == "q1"

    time.sleep(0.2)
    out, err = capfd.readouterr()
    combined = out + err
    assert "[AMBIGUITY] Detection degraded" not in combined, \
        "成功路徑不可誤觸發降級訊息"
