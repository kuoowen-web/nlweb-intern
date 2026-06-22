"""LR 斷線不取消（plan: lr-sse-reconnect-resume, 2026-06-15）。

CEO 拍板：client 斷線時 server **不取消**進行中任務，只標記離線，讓 orchestrator
把當前 stage 跑到下個 checkpoint 才停存檔。

`_on_lr_disconnect`（start + continue 兩處）共用 module-level helper
`_lr_mark_client_disconnected(handler)`。本測試驗該 helper：
- **不**呼叫 task.cancel()
- clear connection_alive_event
- 設 handler._client_offline_since（首次離線），重複呼叫不覆寫
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def _make_handler():
    h = MagicMock()
    # connection_alive_event：真實 asyncio.Event 行為由 is_set / clear 模擬
    evt = MagicMock()
    evt._set = True
    evt.clear = MagicMock(side_effect=lambda: setattr(evt, "_set", False))
    evt.is_set = MagicMock(side_effect=lambda: evt._set)
    h.connection_alive_event = evt
    h._lr_research_task = MagicMock()
    h._client_offline_since = None
    h.lr_session_id = "sess-1"
    return h


def test_on_lr_disconnect_does_not_cancel():
    from webserver.routes.api import _lr_mark_client_disconnected
    h = _make_handler()
    _lr_mark_client_disconnected(h)
    # 核心：絕不 cancel task
    h._lr_research_task.cancel.assert_not_called()
    # clear alive event
    h.connection_alive_event.clear.assert_called_once()
    assert h.connection_alive_event.is_set() is False
    # 設離線起點
    assert h._client_offline_since is not None
    assert isinstance(h._client_offline_since, float)


def test_on_lr_disconnect_does_not_overwrite_offline_since():
    """重連未到 checkpoint 仍離線時，再觸發 disconnect 不覆寫 offline_since（保留原始起點）。"""
    from webserver.routes.api import _lr_mark_client_disconnected
    h = _make_handler()
    _lr_mark_client_disconnected(h)
    first = h._client_offline_since
    _lr_mark_client_disconnected(h)
    assert h._client_offline_since == first


def test_on_lr_disconnect_handles_none_task():
    """task 尚未建立（None）時不 crash。"""
    from webserver.routes.api import _lr_mark_client_disconnected
    h = _make_handler()
    h._lr_research_task = None
    _lr_mark_client_disconnected(h)  # must not raise
    assert h.connection_alive_event.is_set() is False


def test_handler_init_sets_client_offline_since_none():
    """LiveResearchHandler.__init__ 初始化 _client_offline_since = None。"""
    from methods.live_research import LiveResearchHandler
    http_handler = MagicMock()
    h = LiveResearchHandler(query_params={'query': 'x', 'dry_run': 'true'}, http_handler=http_handler)
    assert h._client_offline_since is None
