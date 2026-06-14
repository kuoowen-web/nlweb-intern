import os
import httpx
from typing import List, Optional

from misc.logger.logging_config_helper import get_configured_logger, LogLevel

logger = get_configured_logger("openrouter_embedding")

QUERY_PREFIX = "Instruct: Given a web search query, retrieve relevant passages that answer the query.\nQuery: "
OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
MODEL = "qwen/qwen3-embedding-4b"
TRUNCATE_DIM = 1024


def _get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY environment variable is not set")
    return key


async def get_openrouter_embedding(
    text: str, model: Optional[str] = None, timeout: float = 30.0
) -> List[float]:
    prefixed = QUERY_PREFIX + text
    use_model = model or MODEL

    logger.debug(f"OpenRouter embedding request, model={use_model}, text_length={len(text)}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            json={"model": use_model, "input": [prefixed]},
        )
        response.raise_for_status()
        data = response.json()

    # OpenRouter sometimes returns HTTP 200 with {"error": {...}} instead of {"data": [...]}
    # (e.g., upstream rate limits, model unavailable). Raise explicitly so callers see a
    # meaningful error instead of a confusing KeyError: 'data'.
    if "error" in data and "data" not in data:
        err = data["error"]
        raise RuntimeError(
            f"OpenRouter embedding API error: {err.get('message', err) if isinstance(err, dict) else err}"
        )

    embedding = data["data"][0]["embedding"]
    truncated = embedding[:TRUNCATE_DIM]

    usage = data.get("usage", {})
    logger.debug(
        f"OpenRouter embedding received, raw_dim={len(embedding)}, "
        f"truncated_dim={len(truncated)}, tokens={usage.get('total_tokens', '?')}"
    )
    return truncated


async def get_openrouter_batch_embeddings(
    texts: List[str], model: Optional[str] = None, timeout: float = 60.0
) -> List[List[float]]:
    prefixed = [QUERY_PREFIX + t for t in texts]
    use_model = model or MODEL

    logger.debug(f"OpenRouter batch embedding request, model={use_model}, batch_size={len(texts)}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            json={"model": use_model, "input": prefixed},
        )
        response.raise_for_status()
        data = response.json()

    # OpenRouter sometimes returns HTTP 200 with {"error": {...}} instead of {"data": [...]}
    if "error" in data and "data" not in data:
        err = data["error"]
        raise RuntimeError(
            f"OpenRouter embedding API error: {err.get('message', err) if isinstance(err, dict) else err}"
        )

    sorted_data = sorted(data["data"], key=lambda x: x["index"])
    results = [item["embedding"][:TRUNCATE_DIM] for item in sorted_data]

    usage = data.get("usage", {})
    logger.debug(
        f"OpenRouter batch embeddings received, count={len(results)}, "
        f"tokens={usage.get('total_tokens', '?')}"
    )
    return results
