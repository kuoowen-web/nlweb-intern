# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Google CSE 429 觀測性測試（票 2026-07-28：429 被偽裝成 timeout + 錯誤空結果無記錄）。

Prod 實況：free tier daily quota 用罄時 CSE 立即回 429（~320ms），但
_do_search 內的 retry backoff（1s、2s）必然撞爆外層 asyncio.wait_for 3s cap，
被砍斷後記成 timeout_occurred=1 —— 真因（429）在 analytics 完全不可見。

行為規格（本檔逐條斷言）：
1. CSE 429：不重試、立即降級（沿用既有 except → 空結果路徑），
   timeout_occurred 必須是 False（429 不是 timeout）。
2. log_tier_6_enrichment 的 metadata 必須能區分三種空結果：
   - 真 timeout        → metadata["error_type"] == "timeout"
   - 上游 HTTP 錯誤    → metadata["error_type"] == "http_<status>"（如 http_429）
   - 正常零結果        → metadata 無 "error_type" key
3. 寫進 log / metadata 的 error 文字必須先過 mask_sensitive_url_params
   （2026-06-20 prod 事故：httpx 429 message 內嵌完整 URL 含 key=AIzaSy...）。
4. 5xx retry 行為維持不變（見 test_retry_util.py::TestGoogleCseRetry）。

全 mock，不打真網路。Run:
    pytest tests/test_google_cse_429_observability.py -v
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core import retry_util

# 模擬 httpx raise_for_status 的 message：內嵌完整 request URL（含 credential）。
_SECRET_URL = "https://www.googleapis.com/customsearch/v1?key=AIzaSySECRET&cx=abc&q=x"


def _cse_http_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _SECRET_URL)
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{code}' for url '{_SECRET_URL}'",
        request=request,
        response=response,
    )


def _make_client():
    from retrieval_providers.google_search_client import GoogleSearchClient

    with patch("retrieval_providers.google_search_client.CONFIG") as mock_cfg:
        mock_cfg.reasoning_params = {"tier_6": {}}
        client = GoogleSearchClient()
    client.api_key = "k"
    client.search_engine_id = "cx"
    return client


class TestCse429Observability:
    """429 → 不重試 + 立即降級 + metadata 記 http_429 + timeout_occurred=0。"""

    @pytest.mark.asyncio
    async def test_429_no_retry_immediate_degrade_metadata_http_429(self):
        from retrieval_providers import google_search_client as gsc_mod

        client = _make_client()
        calls = {"n": 0}

        async def always_429(self, url, params=None, **kw):
            calls["n"] += 1
            raise _cse_http_error(429)

        ql = MagicMock()

        with patch("httpx.AsyncClient.get", new=always_429), \
             patch.object(retry_util.asyncio, "sleep", new=AsyncMock(return_value=None)), \
             patch("core.query_logger.get_query_logger", return_value=ql), \
             patch.object(gsc_mod.logger, "error") as err_log:
            results = await client.search_all_sites("query", num_results=5, query_id="qid-429")

        # 降級：空結果（沿用既有 except → [] 路徑）
        assert results == []
        # 429 不重試：只打一次上游（現行 bug：retry 到 3 次）
        assert calls["n"] == 1

        # analytics：timeout_occurred 必須為 False —— 429 不是 timeout
        kwargs = ql.log_tier_6_enrichment.call_args.kwargs
        assert not kwargs["timeout_occurred"]
        assert kwargs["result_count"] == 0
        metadata = kwargs["metadata"]
        assert metadata.get("error_type") == "http_429"

        # 紅線：credential 絕不可進 DB metadata（json 全文掃描）
        blob = json.dumps(metadata)
        assert "AIzaSySECRET" not in blob

        # 紅線：credential 絕不可進 app log；error 資訊本身要保留（診斷性）
        assert err_log.called
        logged = " ".join(str(c.args[0]) for c in err_log.call_args_list)
        assert "AIzaSySECRET" not in logged
        assert "429" in logged
        # exc_info=True 會印含原始（未 mask）message 的 traceback —— 不可用
        for c in err_log.call_args_list:
            assert c.kwargs.get("exc_info") is not True

    @pytest.mark.asyncio
    async def test_non_429_http_error_metadata_has_status_code(self):
        """非 429 的 HTTP 錯誤（retry 耗盡後 propagate）也要記 http_<status>。"""
        client = _make_client()

        client._do_search = AsyncMock(side_effect=_cse_http_error(500))
        ql = MagicMock()

        with patch("core.query_logger.get_query_logger", return_value=ql):
            results = await client.search_all_sites("query", query_id="qid-500")

        assert results == []
        kwargs = ql.log_tier_6_enrichment.call_args.kwargs
        assert not kwargs["timeout_occurred"]
        assert kwargs["metadata"].get("error_type") == "http_500"
        assert "AIzaSySECRET" not in json.dumps(kwargs["metadata"])


class TestTimeoutObservability:
    """真 timeout → timeout_occurred=1 + metadata 記 timeout（與 http 錯誤可區分）。"""

    @pytest.mark.asyncio
    async def test_timeout_metadata_marks_timeout(self):
        client = _make_client()
        client._timeout = 0.01

        async def hang(query, num_results):
            await asyncio.sleep(5)

        client._do_search = hang
        ql = MagicMock()

        with patch("core.query_logger.get_query_logger", return_value=ql):
            results = await client.search_all_sites("query", query_id="qid-timeout")

        assert results == []
        kwargs = ql.log_tier_6_enrichment.call_args.kwargs
        assert kwargs["timeout_occurred"]
        assert kwargs["metadata"].get("error_type") == "timeout"


class TestNormalZeroResults:
    """正常搜尋但零結果 → metadata 無 error_type（與錯誤空結果可區分）。"""

    @pytest.mark.asyncio
    async def test_zero_results_no_error_type(self):
        client = _make_client()
        client._do_search = AsyncMock(return_value=[])
        ql = MagicMock()

        with patch("core.query_logger.get_query_logger", return_value=ql):
            results = await client.search_all_sites("query", query_id="qid-empty")

        assert results == []
        kwargs = ql.log_tier_6_enrichment.call_args.kwargs
        assert not kwargs["timeout_occurred"]
        assert kwargs["result_count"] == 0
        assert "error_type" not in kwargs["metadata"]
