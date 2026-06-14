"""
HuggingFace sentence-transformers embedding implementation.

Uses local models (e.g., BAAI/bge-m3) for embedding generation.
No API key required - runs entirely on local hardware.
"""

import asyncio
import threading
from typing import List, Optional

from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger, LogLevel

logger = get_configured_logger("huggingface_embedding")

# Global model with thread-safe initialization
_model_lock = threading.Lock()
_model = None
_model_name = None


def _get_model_name() -> str:
    """Get model name from config."""
    provider_config = CONFIG.get_embedding_provider("huggingface")
    if provider_config and provider_config.model:
        return provider_config.model
    return "BAAI/bge-m3"


def _get_model():
    """Get or initialize the sentence-transformers model (lazy loading)."""
    global _model, _model_name
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model_name = _get_model_name()
            logger.info(f"Loading HuggingFace model: {_model_name}")
            _model = SentenceTransformer(_model_name)
            dim = _model.get_sentence_embedding_dimension()
            logger.info(f"Model loaded. Dimension: {dim}")
    return _model


def _embed_sync(text: str, model_name: Optional[str] = None) -> List[float]:
    """Synchronous embedding for a single text."""
    model = _get_model()
    embedding = model.encode(
        text,
        normalize_embeddings=True,
    )
    return embedding.tolist()


def _embed_batch_sync(texts: List[str], model_name: Optional[str] = None) -> List[List[float]]:
    """Synchronous batch embedding."""
    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 100,
    )
    return [e.tolist() for e in embeddings]


async def get_huggingface_embedding(
    text: str,
    model: Optional[str] = None,
    timeout: float = 120.0,
) -> List[float]:
    """
    Generate embedding using a local HuggingFace model.

    Args:
        text: The text to embed
        model: Model name (ignored, uses config)
        timeout: Maximum time in seconds

    Returns:
        List of floats representing the embedding vector
    """
    logger.debug(f"Generating HuggingFace embedding, text length: {len(text)} chars")

    try:
        embedding = await asyncio.wait_for(
            asyncio.to_thread(_embed_sync, text),
            timeout=timeout,
        )
        logger.debug(f"HuggingFace embedding generated, dimension: {len(embedding)}")
        return embedding
    except asyncio.TimeoutError:
        logger.error(f"HuggingFace embedding timed out after {timeout}s")
        raise
    except Exception as e:
        logger.exception("Error generating HuggingFace embedding")
        logger.log_with_context(
            LogLevel.ERROR,
            "HuggingFace embedding generation failed",
            {
                "model": _model_name or "not loaded",
                "text_length": len(text),
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        raise


async def get_huggingface_batch_embeddings(
    texts: List[str],
    model: Optional[str] = None,
    timeout: float = 300.0,
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts using a local HuggingFace model.

    Args:
        texts: List of texts to embed
        model: Model name (ignored, uses config)
        timeout: Maximum time in seconds

    Returns:
        List of embedding vectors
    """
    logger.debug(f"Generating HuggingFace batch embeddings, batch size: {len(texts)}")

    try:
        embeddings = await asyncio.wait_for(
            asyncio.to_thread(_embed_batch_sync, texts),
            timeout=timeout,
        )
        logger.debug(f"HuggingFace batch embeddings generated, count: {len(embeddings)}")
        return embeddings
    except asyncio.TimeoutError:
        logger.error(f"HuggingFace batch embedding timed out after {timeout}s")
        raise
    except Exception as e:
        logger.exception("Error generating HuggingFace batch embeddings")
        logger.log_with_context(
            LogLevel.ERROR,
            "HuggingFace batch embedding generation failed",
            {
                "model": _model_name or "not loaded",
                "batch_size": len(texts),
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        raise
