"""W1（silent fail 修復）：DR 非阻塞路徑收到 asyncio.CancelledError 時，
不可 fabricate 一份空的「成功」final_result 送給前端。

背景（根因，已於 deep_research.py 確認）：execute_deep_research 非阻塞路徑
`try: results = await self._research_task / except asyncio.CancelledError:
results = []`，接著照樣往下走 create_assistant_result([]) + _generate_final_report([])
送出空報告 → 前端 render 成「成功但空白」的報告，中斷被偽裝成完成。

修法：CancelledError 分支改為標記 return_value['status']='interrupted'、主動發送
SSE research_interrupted 通知、**提前 return**，不再往下走 fabricate 空報告的路徑。

新增獨立測試檔（而非併入既有 test_deep_research_persist.py / test_deep_research_ambiguity.py）：
本檔測的是 execute_deep_research 非阻塞路徑的中斷語意，主題與既有兩檔（server-side persist /
LLM ambiguity 降級）不同，獨立成檔案較易辨識、未來擴充中斷相關 case 也不會混進不相關檔案。

策略：沿用 test_deep_research_ambiguity.py 的「用 __new__ 繞過重量級 super().__init__，
只手動注入該次呼叫路徑實際用到的 attr」pattern（不走完整 DB session flow，因為
user_id/org_id 留空字串會讓 _create_dr_session 走 bare-UUID fallback，不觸資料庫）。
"""
import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from methods.deep_research import DeepResearchHandler  # noqa: E402


def _bare_handler(query="台灣半導體產業現況"):
    """繞過重量級 super().__init__，只填 execute_deep_research 非阻塞路徑用到的 attr。"""
    h = DeepResearchHandler.__new__(DeepResearchHandler)
    h.query = query
    h.query_params = {}
    h.site = "all"
    h.research_mode = "discovery"
    h.final_retrieved_items = []
    h.temporal_range = None
    h.conversation_id = "conv_test_" + uuid.uuid4().hex[:8]
    h.enable_kg = False
    h.enable_web_search = False
    h.http_handler = None  # 不觸發 session_created event（_dr_session_is_new 預設 False）
    h.user_id = ""  # 空 → _create_dr_session 走 bare UUID fallback，不觸資料庫
    h.org_id = ""
    h.loaded_session_id = ""
    h.dr_session_id = None
    h._dr_session_is_new = False
    h._research_task = None
    h.return_value = {}
    h.message_sender = MagicMock()
    h.message_sender.send_message = AsyncMock()
    return h


def _enable_nonblocking_config(monkeypatch):
    """CONFIG.reasoning_params 兩個 flag 都要 True 才走非阻塞路徑（deep_research.py:213-220）。

    現行 config/config_reasoning.yaml 預設 nonblocking_research=false，此處用 monkeypatch
    改整段 reasoning_params dict（比 patch 單一巢狀 key 更不易被結構變動打壞既有 flag）。
    """
    from core.config import CONFIG
    new_params = dict(CONFIG.reasoning_params)
    new_params["enabled"] = True
    new_params["features"] = dict(new_params.get("features", {}))
    new_params["features"]["composable_pipeline"] = True
    new_params["features"]["nonblocking_research"] = True
    monkeypatch.setattr(CONFIG, "reasoning_params", new_params)


@pytest.mark.asyncio
async def test_cancelled_research_marks_interrupted_and_skips_fabrication(monkeypatch):
    """核心 case：非阻塞路徑收到 CancelledError →
    - return_value['status'] == 'interrupted'
    - return_value['answer'] == ''
    - _send_research_interrupted 被呼叫一次
    - create_assistant_result / _generate_final_report 都沒被呼叫（沒 fabricate 空報告）
    """
    _enable_nonblocking_config(monkeypatch)
    handler = _bare_handler()

    # orchestrator.run_research 是個會被 asyncio.create_task 包起來、await 時拋
    # CancelledError 的 coroutine（模擬前端斷線 / soft interrupt 的真實情境）。
    mock_orchestrator = MagicMock()

    async def _cancelled_run_research(*args, **kwargs):
        raise asyncio.CancelledError()

    mock_orchestrator.run_research = _cancelled_run_research

    interrupted_spy = AsyncMock()

    with patch("reasoning.orchestrator.DeepResearchOrchestrator", return_value=mock_orchestrator), \
         patch("core.schemas.create_assistant_result") as mock_create_result, \
         patch.object(DeepResearchHandler, "_generate_final_report") as mock_final_report, \
         patch.object(DeepResearchHandler, "_send_research_interrupted", interrupted_spy):
        await handler.execute_deep_research()

    assert handler.return_value.get("status") == "interrupted", \
        "CancelledError 後 return_value['status'] 應為 'interrupted'（誠實標記中斷，不偽裝成功）"
    assert handler.return_value.get("answer") == "", \
        "中斷時 answer 應為空字串（沒有可信報告內容），不可帶 fabricated 內容"
    interrupted_spy.assert_awaited_once()
    assert mock_create_result.call_count == 0, \
        "CancelledError 後不應呼叫 create_assistant_result（不可 fabricate 空報告送前端）"
    assert mock_final_report.call_count == 0, \
        "CancelledError 後不應呼叫 _generate_final_report（不可 fabricate 空報告內容）"
    # finally 區塊必須仍執行（Python 保證 finally 在 return 前執行）
    assert handler._research_task is None, \
        "finally: self._research_task = None 應仍執行（即使 except 分支提前 return）"


@pytest.mark.asyncio
async def test_send_research_interrupted_emits_sse_message():
    """_send_research_interrupted 單獨測試：呼叫 message_sender.send_message，
    payload message_type == 'research_interrupted'。"""
    handler = _bare_handler()
    await handler._send_research_interrupted()

    handler.message_sender.send_message.assert_awaited_once()
    payload = handler.message_sender.send_message.await_args.args[0]
    assert payload["message_type"] == "research_interrupted"
    assert "message" in payload


@pytest.mark.asyncio
async def test_send_research_interrupted_best_effort_no_raise_when_sender_missing():
    """message_sender 不存在時 _send_research_interrupted 不可炸掉呼叫端（best-effort，仿
    _send_research_error 的 hasattr 慣用法）。"""
    handler = DeepResearchHandler.__new__(DeepResearchHandler)
    # 故意不設 message_sender attr，模擬極端情境下該 attr 缺失
    await handler._send_research_interrupted()  # 不應拋例外
