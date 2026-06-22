"""core.embedding fallback chain: OpenRouter -> DeepInfra.

Prod incident: OpenRouter qwen3-embedding-4b is a single provider with no
cross-provider fallback. When it returns engine_overloaded (HTTP 429) the
retry/backoff exhausts and PG retrieval collapses. This suite pins the fallback
behavior added to get_embedding:

  - OpenRouter retry exhausts (persistent 429) -> fall back to DeepInfra.
  - DeepInfra succeeds -> get_embedding returns DeepInfra's vector (fallback caught).
  - DeepInfra ALSO fails -> get_embedding raises (no silent fail).
  - No fallback configured -> original single-provider behavior (raises, no DeepInfra call).

Everything is mocked — NO real API is called (there is no real DEEPINFRA_API_KEY yet).
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from core.embedding import get_embedding


def _make_429() -> httpx.HTTPStatusError:
    """A retryable HTTP 429 (engine_overloaded), as the prod incident saw."""
    request = httpx.Request("POST", "https://example/embeddings")
    response = httpx.Response(429, request=request, json={"error": "engine_overloaded"})
    return httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)


def _patch_config_with_fallback(fallback="deepinfra"):
    """Patch CONFIG so provider='openrouter', model resolves, fallback=deepinfra.

    get_embedding looks up CONFIG.preferred_embedding_provider,
    CONFIG.embedding_providers (membership), CONFIG.get_embedding_provider().model,
    and CONFIG.embedding_fallback_provider.
    """
    cfg = patch("core.embedding.CONFIG")
    mock_cfg = cfg.start()
    mock_cfg.is_development_mode.return_value = False
    mock_cfg.preferred_embedding_provider = "openrouter"
    mock_cfg.embedding_providers = {"openrouter": object(), "deepinfra": object()}
    mock_cfg.embedding_fallback_provider = fallback

    class _Prov:
        model = "qwen/qwen3-embedding-4b"

    mock_cfg.get_embedding_provider.return_value = _Prov()
    return cfg, mock_cfg


@pytest.mark.asyncio
async def test_openrouter_exhausts_falls_back_to_deepinfra_success():
    """Persistent OpenRouter 429 -> DeepInfra called -> its vector returned."""
    cfg, _ = _patch_config_with_fallback("deepinfra")
    try:
        or_mock = AsyncMock(side_effect=_make_429())
        di_mock = AsyncMock(return_value=[0.5] * 1024)

        with patch("embedding_providers.openrouter_embedding.get_openrouter_embedding", or_mock), \
             patch("embedding_providers.deepinfra_embedding.get_deepinfra_embedding", di_mock), \
             patch("core.embedding.asyncio.sleep", AsyncMock()):  # skip real backoff waits
            result = await get_embedding("hello world", timeout=5)

        # DeepInfra caught the request after OpenRouter exhausted its retries.
        assert result == [0.5] * 1024
        assert di_mock.await_count == 1
        # OpenRouter was tried 1 initial + 3 retries = 4 attempts before fallback.
        assert or_mock.await_count == 4
    finally:
        cfg.stop()


@pytest.mark.asyncio
async def test_both_providers_fail_raises():
    """OpenRouter 429 AND DeepInfra 429 -> get_embedding raises (no silent fail)."""
    cfg, _ = _patch_config_with_fallback("deepinfra")
    try:
        or_mock = AsyncMock(side_effect=_make_429())
        di_mock = AsyncMock(side_effect=_make_429())

        with patch("embedding_providers.openrouter_embedding.get_openrouter_embedding", or_mock), \
             patch("embedding_providers.deepinfra_embedding.get_deepinfra_embedding", di_mock), \
             patch("core.embedding.asyncio.sleep", AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError):
                await get_embedding("hello world", timeout=5)

        # Both providers were actually exercised (each: 1 + 3 retries).
        assert or_mock.await_count == 4
        assert di_mock.await_count == 4
    finally:
        cfg.stop()


@pytest.mark.asyncio
async def test_no_fallback_configured_keeps_single_provider_behavior():
    """fallback_provider=None -> OpenRouter raises, DeepInfra never called."""
    cfg, _ = _patch_config_with_fallback(fallback=None)
    try:
        or_mock = AsyncMock(side_effect=_make_429())
        di_mock = AsyncMock(return_value=[0.5] * 1024)

        with patch("embedding_providers.openrouter_embedding.get_openrouter_embedding", or_mock), \
             patch("embedding_providers.deepinfra_embedding.get_deepinfra_embedding", di_mock), \
             patch("core.embedding.asyncio.sleep", AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError):
                await get_embedding("hello world", timeout=5)

        assert di_mock.await_count == 0  # fallback disabled -> never called
        assert or_mock.await_count == 4
    finally:
        cfg.stop()


@pytest.mark.asyncio
async def test_openrouter_success_does_not_call_deepinfra():
    """Happy path: OpenRouter succeeds first try -> DeepInfra untouched."""
    cfg, _ = _patch_config_with_fallback("deepinfra")
    try:
        or_mock = AsyncMock(return_value=[0.1] * 1024)
        di_mock = AsyncMock(return_value=[0.5] * 1024)

        with patch("embedding_providers.openrouter_embedding.get_openrouter_embedding", or_mock), \
             patch("embedding_providers.deepinfra_embedding.get_deepinfra_embedding", di_mock):
            result = await get_embedding("hello world", timeout=5)

        assert result == [0.1] * 1024
        assert or_mock.await_count == 1
        assert di_mock.await_count == 0
    finally:
        cfg.stop()
