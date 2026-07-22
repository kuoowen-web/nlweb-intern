"""
Qwen3-Embedding-4B local embedding provider (INT8/FP16 quantization).

Uses sentence-transformers with Qwen3-Embedding-4B for local embedding generation.
Singleton pattern: model loaded once, reused across all calls.
Outputs 1024-dimensional embeddings.

NOTE (2026-07-16): Dormant path under current production config —
config_embedding.yaml prefers "openrouter" (fallback "deepinfra"); this local
provider only runs if the provider is explicitly switched to "qwen3" (config or
dev query-param override). Offline indexing (cloud_embed.py) loads
SentenceTransformer directly and does NOT go through this module.
"""

import asyncio
import threading
from typing import List, Optional

from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger, LogLevel

logger = get_configured_logger("qwen3_embedding")

# Global model with thread-safe initialization
_model_lock = threading.Lock()
_model = None
_model_name = None


def _get_model_name() -> str:
    """Get model name from config."""
    provider_config = CONFIG.get_embedding_provider("qwen3")
    if provider_config and provider_config.model:
        return provider_config.model
    return "Qwen/Qwen3-Embedding-4B"


def _get_model():
    """Get or initialize the Qwen3 model (lazy loading, singleton).

    Uses INT8 quantization via bitsandbytes (same as S3 pipeline).
    First load takes ~35 seconds. Subsequent calls return cached model.
    """
    global _model, _model_name
    with _model_lock:
        if _model is None:
            import time
            from sentence_transformers import SentenceTransformer
            from transformers import BitsAndBytesConfig

            _model_name = _get_model_name()
            logger.info(f"Loading Qwen3 embedding model: {_model_name} (INT8 quantization)...")
            t0 = time.time()

            quantization_config = BitsAndBytesConfig(load_in_8bit=True)
            _model = SentenceTransformer(
                _model_name,
                model_kwargs={"quantization_config": quantization_config},
                truncate_dim=1024,
            )

            elapsed = time.time() - t0
            logger.info(f"Qwen3 model loaded in {elapsed:.1f}s. Output dim: 1024")
    return _model


def _embed_sync(text: str) -> List[float]:
    """Synchronous embedding for a single query text."""
    model = _get_model()
    embedding = model.encode(
        text,
        prompt_name="query",
        show_progress_bar=False,
    )
    # Truncate to 1024 dimensions
    return embedding[:1024].tolist()


def _embed_batch_sync(texts: List[str]) -> List[List[float]]:
    """Synchronous batch embedding."""
    model = _get_model()
    embeddings = model.encode(
        texts,
        prompt_name="query",
        batch_size=8,
        show_progress_bar=len(texts) > 50,
    )
    return [e[:1024].tolist() for e in embeddings]


async def get_qwen3_embedding(
    text: str,
    model: Optional[str] = None,
    timeout: float = 120.0,
) -> List[float]:
    """
    Generate embedding using local Qwen3-Embedding-4B model.

    Args:
        text: The text to embed
        model: Model name (ignored, uses config)
        timeout: Maximum time in seconds

    Returns:
        List of floats (1024 dimensions)
    """
    logger.debug(f"Generating Qwen3 embedding, text length: {len(text)} chars")

    try:
        embedding = await asyncio.wait_for(
            asyncio.to_thread(_embed_sync, text),
            timeout=timeout,
        )
        logger.debug(f"Qwen3 embedding generated, dimension: {len(embedding)}")
        return embedding
    except asyncio.TimeoutError:
        logger.error(f"Qwen3 embedding timed out after {timeout}s")
        raise
    except Exception as e:
        logger.exception("Error generating Qwen3 embedding")
        logger.log_with_context(
            LogLevel.ERROR,
            "Qwen3 embedding generation failed",
            {
                "model": _model_name or "not loaded",
                "text_length": len(text),
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        raise


async def get_qwen3_batch_embeddings(
    texts: List[str],
    model: Optional[str] = None,
    timeout: float = 300.0,
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts using local Qwen3-Embedding-4B model.

    Args:
        texts: List of texts to embed
        model: Model name (ignored, uses config)
        timeout: Maximum time in seconds

    Returns:
        List of embedding vectors (each 1024 dimensions)
    """
    logger.debug(f"Generating Qwen3 batch embeddings, batch size: {len(texts)}")

    try:
        embeddings = await asyncio.wait_for(
            asyncio.to_thread(_embed_batch_sync, texts),
            timeout=timeout,
        )
        logger.debug(f"Qwen3 batch embeddings generated, count: {len(embeddings)}")
        return embeddings
    except asyncio.TimeoutError:
        logger.error(f"Qwen3 batch embedding timed out after {timeout}s")
        raise
    except Exception as e:
        logger.exception("Error generating Qwen3 batch embeddings")
        logger.log_with_context(
            LogLevel.ERROR,
            "Qwen3 batch embedding generation failed",
            {
                "model": _model_name or "not loaded",
                "batch_size": len(texts),
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        raise
