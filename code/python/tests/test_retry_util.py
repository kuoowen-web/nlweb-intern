# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit tests for external-API retry/backoff.

Covers:
1. core.retry_util.retry_async + is_retryable_exception (the shared helper).
2. core.embedding.get_embedding wrapper retry (Part A — the prod 429 incident).
3. retrieval_providers.google_search_client._do_search retry (Part C).

Discipline: TDD. These assert that a transient fault (429 / OpenRouter-200-error /
timeout / connect error) is retried with backoff, that a non-retryable fault is
NOT retried, that a mid-sequence success is caught, and that retries-exhausted
re-raises the original error (no silent fail).

Run:
    pytest tests/test_retry_util.py -v
"""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core import retry_util
from core.retry_util import (
    calculate_backoff,
    is_retryable_exception,
    retry_async,
)


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://example.test")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


# ==============================================================================
# is_retryable_exception predicate
# ==============================================================================

class TestIsRetryable:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 529])
    def test_retryable_status_codes(self, code):
        assert is_retryable_exception(_http_status_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_non_retryable_status_codes(self, code):
        assert is_retryable_exception(_http_status_error(code)) is False

    def test_timeout_is_retryable(self):
        assert is_retryable_exception(httpx.TimeoutException("slow")) is True
        assert is_retryable_exception(httpx.ConnectTimeout("slow")) is True

    def test_connect_error_is_retryable(self):
        assert is_retryable_exception(httpx.ConnectError("refused")) is True

    def test_openrouter_200_error_is_retryable(self):
        # The RuntimeError raised by openrouter_embedding on HTTP-200-with-error-body.
        exc = RuntimeError("OpenRouter embedding API error: engine_overloaded")
        assert is_retryable_exception(exc) is True

    def test_generic_runtime_error_not_retryable(self):
        assert is_retryable_exception(RuntimeError("something else")) is False

    def test_value_error_not_retryable(self):
        assert is_retryable_exception(ValueError("bad input")) is False


# ==============================================================================
# calculate_backoff
# ==============================================================================

class TestBackoff:
    def test_exponential_sequence(self):
        assert calculate_backoff(0) == 1
        assert calculate_backoff(1) == 2
        assert calculate_backoff(2) == 4
        assert calculate_backoff(3) == 8

    def test_cap(self):
        assert calculate_backoff(10) == 30  # capped


# ==============================================================================
# retry_async core behavior (sleep patched out for speed)
# ==============================================================================

@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Patch asyncio.sleep inside retry_util so tests don't actually wait."""
    async def _fast(_delay):
        return None
    with patch.object(retry_util.asyncio, "sleep", side_effect=_fast) as m:
        yield m


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_success_first_try_no_retry(self):
        func = AsyncMock(return_value="ok")
        result = await retry_async(func, max_retries=3)
        assert result == "ok"
        assert func.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        # Fail on first call (429), succeed on the second.
        func = AsyncMock(side_effect=[_http_status_error(429), "recovered"])
        result = await retry_async(func, max_retries=3)
        assert result == "recovered"
        assert func.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_exhausted_reraises_original(self, _no_real_sleep):
        err = _http_status_error(429)
        func = AsyncMock(side_effect=err)
        with pytest.raises(httpx.HTTPStatusError) as ei:
            await retry_async(func, max_retries=3)
        # original error surfaced (no silent fail)
        assert ei.value is err
        # 1 initial + 3 retries = 4 total attempts
        assert func.await_count == 4
        # 3 backoff sleeps: 1s, 2s, 4s
        assert [c.args[0] for c in _no_real_sleep.call_args_list] == [1, 2, 4]

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        err = _http_status_error(400)
        func = AsyncMock(side_effect=err)
        with pytest.raises(httpx.HTTPStatusError):
            await retry_async(func, max_retries=3)
        assert func.await_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_openrouter_200_error_retried(self):
        func = AsyncMock(side_effect=[
            RuntimeError("OpenRouter embedding API error: engine_overloaded"),
            [0.1, 0.2, 0.3],
        ])
        result = await retry_async(func, max_retries=3)
        assert result == [0.1, 0.2, 0.3]
        assert func.await_count == 2

    @pytest.mark.asyncio
    async def test_timeout_retried_then_exhausted(self, _no_real_sleep):
        func = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        with pytest.raises(httpx.TimeoutException):
            await retry_async(func, max_retries=2)
        assert func.await_count == 3  # 1 + 2
        assert [c.args[0] for c in _no_real_sleep.call_args_list] == [1, 2]


# ==============================================================================
# Part A: core.embedding.get_embedding wrapper retry
# ==============================================================================

class TestEmbeddingWrapperRetry:
    """The prod incident: OpenRouter embedding 429 must be retried inside the
    get_embedding wrapper so a transient overload doesn't kill the LR run."""

    def _patch_config(self):
        """Patch CONFIG so get_embedding resolves the openrouter provider."""
        from core import embedding as emb_mod

        class _ProviderCfg:
            model = "qwen/qwen3-embedding-4b"

        cfg = patch.multiple(
            emb_mod.CONFIG,
            preferred_embedding_provider="openrouter",
            embedding_providers={"openrouter": _ProviderCfg()},
            create=True,
        )
        return cfg

    @pytest.mark.asyncio
    async def test_openrouter_429_retried_then_succeeds(self):
        from core import embedding as emb_mod

        attempts = {"n": 0}

        async def flaky(text, model=None, timeout=30.0):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _http_status_error(429)
            return [0.5] * 1024

        with self._patch_config(), \
             patch.object(emb_mod.CONFIG, "is_development_mode", return_value=False, create=True), \
             patch.object(emb_mod.CONFIG, "get_embedding_provider",
                          return_value=type("C", (), {"model": "qwen/qwen3-embedding-4b"})(),
                          create=True), \
             patch("embedding_providers.openrouter_embedding.get_openrouter_embedding",
                   side_effect=flaky), \
             patch.object(retry_util.asyncio, "sleep", new=AsyncMock(return_value=None)):
            result = await emb_mod.get_embedding("hello", provider="openrouter")

        assert len(result) == 1024
        assert attempts["n"] == 2  # retried once

    @pytest.mark.asyncio
    async def test_openrouter_429_exhausted_reraises(self):
        from core import embedding as emb_mod

        async def always_429(text, model=None, timeout=30.0):
            raise _http_status_error(429)

        with self._patch_config(), \
             patch.object(emb_mod.CONFIG, "is_development_mode", return_value=False, create=True), \
             patch.object(emb_mod.CONFIG, "get_embedding_provider",
                          return_value=type("C", (), {"model": "qwen/qwen3-embedding-4b"})(),
                          create=True), \
             patch("embedding_providers.openrouter_embedding.get_openrouter_embedding",
                   side_effect=always_429), \
             patch.object(retry_util.asyncio, "sleep", new=AsyncMock(return_value=None)):
            with pytest.raises(httpx.HTTPStatusError):
                await emb_mod.get_embedding("hello", provider="openrouter")

    @pytest.mark.asyncio
    async def test_openrouter_200_error_retried_in_wrapper(self):
        from core import embedding as emb_mod

        attempts = {"n": 0}

        async def flaky(text, model=None, timeout=30.0):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("OpenRouter embedding API error: engine_overloaded")
            return [0.25] * 1024

        with self._patch_config(), \
             patch.object(emb_mod.CONFIG, "is_development_mode", return_value=False, create=True), \
             patch.object(emb_mod.CONFIG, "get_embedding_provider",
                          return_value=type("C", (), {"model": "qwen/qwen3-embedding-4b"})(),
                          create=True), \
             patch("embedding_providers.openrouter_embedding.get_openrouter_embedding",
                   side_effect=flaky), \
             patch.object(retry_util.asyncio, "sleep", new=AsyncMock(return_value=None)):
            result = await emb_mod.get_embedding("hello", provider="openrouter")

        assert len(result) == 1024
        assert attempts["n"] == 3


# ==============================================================================
# Part C: Google CSE _do_search retry
# ==============================================================================

class TestGoogleCseRetry:
    def _make_client(self):
        from retrieval_providers.google_search_client import GoogleSearchClient

        with patch("retrieval_providers.google_search_client.CONFIG") as mock_cfg:
            mock_cfg.reasoning_params = {"tier_6": {}}
            mock_cfg.get.return_value = None
            client = GoogleSearchClient()
        client.api_key = "k"
        client.search_engine_id = "cx"
        return client

    @pytest.mark.asyncio
    async def test_do_search_retries_on_429_then_succeeds(self):
        client = self._make_client()
        calls = {"n": 0}

        async def fake_get(self, url, params=None, **kw):
            calls["n"] += 1
            request = httpx.Request("GET", url)
            if calls["n"] == 1:
                resp = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("429", request=request, response=resp)
            return httpx.Response(200, request=request, json={"items": []})

        with patch("httpx.AsyncClient.get", new=fake_get), \
             patch.object(retry_util.asyncio, "sleep", new=AsyncMock(return_value=None)):
            results = await client._do_search("query", 5)

        assert results == []
        assert calls["n"] == 2  # retried once

    @pytest.mark.asyncio
    async def test_do_search_retries_exhausted_reraises(self):
        client = self._make_client()
        calls = {"n": 0}

        async def always_429(self, url, params=None, **kw):
            calls["n"] += 1
            request = httpx.Request("GET", url)
            resp = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("429", request=request, response=resp)

        with patch("httpx.AsyncClient.get", new=always_429), \
             patch.object(retry_util.asyncio, "sleep", new=AsyncMock(return_value=None)):
            with pytest.raises(httpx.HTTPStatusError):
                await client._do_search("query", 5)

        # 1 initial + 2 retries (Part C uses max_retries=2)
        assert calls["n"] == 3
