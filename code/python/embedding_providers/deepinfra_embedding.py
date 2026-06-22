"""DeepInfra embedding provider — fallback for OpenRouter qwen3-embedding-4b.

Prod incident motivation: OpenRouter's qwen3-embedding-4b is a single provider
with no cross-provider fallback. When it returns engine_overloaded (HTTP 429) the
retry/backoff (1/2/4s) can still exhaust, and the internal PG retrieval path —
which depends on a fresh query embedding — collapses entirely.

This provider is the fallback. It MUST stay vector-space compatible with the
OpenRouter provider, because PG vectors are vector(1024) built from
Qwen3-Embedding-4B and a fallback embedding from a different model/dimension would
return garbage results. To guarantee that compatibility we:
  - use the SAME model (qwen3-embedding-4b, here served as Qwen/Qwen3-Embedding-4B)
  - reuse the SAME QUERY_PREFIX (imported from openrouter_embedding) so the query
    representation is identical
  - truncate to the SAME TRUNCATE_DIM=1024

NOTE (vector parity unverified): until DEEPINFRA_API_KEY exists and
tools/verify_deepinfra_embedding_parity.py has been run (cosine > 0.99 vs
OpenRouter), the fallback output is NOT yet proven interchangeable.

Uses DeepInfra's OpenAI-compatible embeddings endpoint, which mirrors the
OpenRouter request/response shape (POST {model, input:[...]} ->
{data:[{embedding, index}], usage}), so the structure stays close to
openrouter_embedding.py and is easy to maintain.
"""
import os
import httpx
from typing import List, Optional

from misc.logger.logging_config_helper import get_configured_logger

# Reuse OpenRouter's query prefix + truncation dim verbatim so the DeepInfra
# fallback produces a query representation in the SAME vector space as the
# primary provider (required for PG retrieval to stay correct on fallback).
from embedding_providers.openrouter_embedding import QUERY_PREFIX, TRUNCATE_DIM

logger = get_configured_logger("deepinfra_embedding")

# DeepInfra OpenAI-compatible embeddings endpoint (preferred over the native
# /v1/inference/... route — same httpx pattern as OpenRouter, easier to maintain).
DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/embeddings"
# DeepInfra serves the same model under its HF id (qwen3-embedding-4b).
MODEL = "Qwen/Qwen3-Embedding-4B"


def _get_api_key() -> str:
    key = os.environ.get("DEEPINFRA_API_KEY")
    if not key:
        raise ValueError("DEEPINFRA_API_KEY environment variable is not set")
    return key


async def get_deepinfra_embedding(
    text: str, model: Optional[str] = None, timeout: float = 30.0
) -> List[float]:
    prefixed = QUERY_PREFIX + text
    use_model = model or MODEL

    logger.debug(f"DeepInfra embedding request, model={use_model}, text_length={len(text)}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            DEEPINFRA_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            json={"model": use_model, "input": [prefixed]},
        )
        response.raise_for_status()
        data = response.json()

    # Like OpenRouter, an OpenAI-compatible endpoint can return HTTP 200 with an
    # {"error": {...}} body (upstream rate limit / model unavailable). Raise
    # explicitly so callers see a meaningful error instead of a KeyError: 'data'.
    if "error" in data and "data" not in data:
        err = data["error"]
        raise RuntimeError(
            f"DeepInfra embedding API error: {err.get('message', err) if isinstance(err, dict) else err}"
        )

    embedding = data["data"][0]["embedding"]
    truncated = embedding[:TRUNCATE_DIM]

    usage = data.get("usage", {})
    logger.debug(
        f"DeepInfra embedding received, raw_dim={len(embedding)}, "
        f"truncated_dim={len(truncated)}, tokens={usage.get('total_tokens', '?')}"
    )
    return truncated


async def get_deepinfra_batch_embeddings(
    texts: List[str], model: Optional[str] = None, timeout: float = 60.0
) -> List[List[float]]:
    prefixed = [QUERY_PREFIX + t for t in texts]
    use_model = model or MODEL

    logger.debug(f"DeepInfra batch embedding request, model={use_model}, batch_size={len(texts)}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            DEEPINFRA_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            json={"model": use_model, "input": prefixed},
        )
        response.raise_for_status()
        data = response.json()

    if "error" in data and "data" not in data:
        err = data["error"]
        raise RuntimeError(
            f"DeepInfra embedding API error: {err.get('message', err) if isinstance(err, dict) else err}"
        )

    sorted_data = sorted(data["data"], key=lambda x: x["index"])
    results = [item["embedding"][:TRUNCATE_DIM] for item in sorted_data]

    usage = data.get("usage", {})
    logger.debug(
        f"DeepInfra batch embeddings received, count={len(results)}, "
        f"tokens={usage.get('total_tokens', '?')}"
    )
    return results
