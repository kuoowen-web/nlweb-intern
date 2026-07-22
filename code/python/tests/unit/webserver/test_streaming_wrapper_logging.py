"""層 1 觀測性：wrapper 用 configured logger + 斷線/短路事件可見。"""
import os
import sys
import time
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from webserver import aiohttp_streaming_wrapper as wrap_mod  # noqa: E402
# [R5 BLOCKER1] get_configured_logger 回傳 LazyLogger（logging_config_helper.py:414-419），
# **不是** stdlib logging.Logger——LazyLogger 有 .module_name（:360），**沒有 .name**。
from misc.logger.logging_config_helper import (  # noqa: E402
    LazyLogger, get_configured_logger, _get_async_processor,
)


def test_wrapper_logger_is_configured_type():
    """wrapper 的 module logger 必須經 get_configured_logger 建立（進 nlweb.log）。

    [R5 BLOCKER1 修正] get_configured_logger 回傳的是 **LazyLogger** 實例
    （logging_config_helper.py:414-419），LazyLogger.__init__（:359-363）只有
    self.module_name / self._real_logger / self._initialized / self.async_processor
    ——**有 module_name、沒有 .name**。故原斷言 `logger.name != '...'` 對 LazyLogger 型別無效
    （LazyLogger 根本無 .name 屬性，AttributeError 而非證偽 logger 未改）。
    正確判準：型別是 LazyLogger + module_name 對得上 Step 3 改的 "aiohttp_streaming_wrapper"。
    """
    logger = wrap_mod.logger
    assert logger is not None
    # 判準1：型別是 configured 的 LazyLogger（原生 logging.getLogger 回 stdlib Logger、非 LazyLogger）
    assert isinstance(logger, LazyLogger), \
        "logger 仍是原生 logging.getLogger(__name__)（非 LazyLogger），未改用 get_configured_logger"
    # 判準2：module_name 對上 Step 3 的 get_configured_logger("aiohttp_streaming_wrapper")
    assert logger.module_name == "aiohttp_streaming_wrapper", \
        "logger 的 module_name 不符——Step 3 應改為 get_configured_logger('aiohttp_streaming_wrapper')"


@pytest.mark.asyncio
async def test_write_stream_shortcircuit_logs_warning():
    """connection_alive=False 時 write_stream 短路 return，必須留 warning（不可零 log 靜默丟棄）。

    [R5 BLOCKER1 修正——不可用 caplog] LazyLogger 的 log 方法（debug/info/warning/error，
    logging_config_helper.py:373-391）全走 async_processor.enqueue_log → 背景 daemon worker thread
    → LoggerUtility（logger.py:93）→ logging.getLogger(module_name)（logger.py:134），且該底層 logger
    **propagate=False**（logger.py:136）+ 建構時 clear 既有 handlers（logger.py:142-143）。
    → pytest **caplog 抓不到**（propagate=False 切斷傳播鏈到 root，caplog 掛在 root handler）。
    正確攔截手段照抄 repo 現成範本 A（test_live_orchestrator.py:429-502）：
      (1) pre-warm：逼 worker 端 real_logger 先建好（否則我掛的 handler 會被 lazy 建構 clear 掉）；
      (2) 自訂 _CaptureHandler 收 record.getMessage()，addHandler 到底層 non-propagating logger；
      (3) 觸發後 poll 等背景 worker flush（enqueue 是非同步的，log 不會同步落地）；
      (4) finally removeHandler。
    module_name 對上 Step 3 改的 get_configured_logger("aiohttp_streaming_wrapper")。
    """
    response = MagicMock()
    response.write = AsyncMock()
    request = MagicMock()
    request.transport = MagicMock()
    request.transport.is_closing.return_value = False
    # [R4 C1] 真實建構子讀 request.method / request.path / dict(request.headers)
    # （aiohttp_streaming_wrapper.py:35-37）——不 mock 這三個 request 屬性，
    # 建構會在 dict(request.headers)（MagicMock headers 非 mapping）於 setup 就炸，到不了斷言。
    request.method = "GET"
    request.path = "/api/deep_research"
    request.headers = {}

    # [R5 BLOCKER1] pre-warm：逼 worker 端底層 real_logger 先建好（LoggerUtility.__init__ 會 clear
    # 既有 handlers、propagate=False），之後掛的 _CaptureHandler 才不會被 lazy 建構清掉。
    # module_name 必須對上 Step 3 的 get_configured_logger("aiohttp_streaming_wrapper")。
    _get_async_processor()._get_real_logger("aiohttp_streaming_wrapper")

    log_records = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record.getMessage())

    _capture = _CaptureHandler()
    _capture.setLevel(logging.WARNING)
    # 底層 non-propagating logger（LazyLogger → LoggerUtility → logging.getLogger(module_name)）
    _underlying = logging.getLogger("aiohttp_streaming_wrapper")
    _prev_level = _underlying.level
    _underlying.addHandler(_capture)
    if _underlying.level > logging.WARNING:
        _underlying.setLevel(logging.WARNING)
    try:
        # [R4 C1] 真實建構子是三參數 (request, response, query_params)（:27，[verified]）——
        # 傳第三參數空 query_params dict。**不可只傳兩參數**（過去 snippet 的 bug）。
        w = wrap_mod.AioHttpStreamingWrapper(request, response, {})
        w.connection_alive = False  # 模擬已斷線

        await w.write_stream({"message_type": "final_result", "final_report": "x"})

        # 短路後 response.write 不應被呼叫
        response.write.assert_not_called()
        # poll 等背景 worker thread 把 warning dispatch 到底層 logger（非同步 enqueue queue）
        _deadline = time.time() + 3.0
        while time.time() < _deadline and not any(
            ("connection_alive" in m or "dropped" in m.lower() or "斷線" in m)
            for m in log_records
        ):
            time.sleep(0.05)
        # 必須留一條可見（warning）log 指出訊息被丟棄
        assert any("connection_alive" in m or "dropped" in m.lower() or "斷線" in m
                   for m in log_records), \
            "write_stream 短路丟棄 final_result 時未留 warning log（觀測點=底層 non-propagating logger）"
    finally:
        _underlying.removeHandler(_capture)
        _underlying.setLevel(_prev_level)


def test_api_route_logger_is_configured_type():
    """api.py route module logger 必須經 get_configured_logger（進 nlweb.log）。

    [R5 BLOCKER1 修正] 同 wrapper——get_configured_logger 回 LazyLogger（有 module_name、無 .name）。
    原斷言 `api_mod.logger.name != ...` 對 LazyLogger 型別無效（AttributeError）。改判型別 + module_name。
    """
    from webserver.routes import api as api_mod
    assert isinstance(api_mod.logger, LazyLogger), \
        "api.py logger 仍是原生 logging.getLogger(__name__)（非 LazyLogger）"
    assert api_mod.logger.module_name == "api_routes", \
        "api.py logger module_name 不符——Step 3 應改為 get_configured_logger('api_routes')"
