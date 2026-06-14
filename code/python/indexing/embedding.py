"""
embedding.py - Indexing Embedding 模組

Profile-aware: 根據 QDRANT_PROFILE 決定使用哪個 embedding provider。
- offline (或未設定): 本地 bge-m3 (1024D)
- online: 委派給 core/embedding.py (OpenAI 1536D)
"""

import asyncio
import logging

import numpy as np

logger = logging.getLogger(__name__)

# Lazy load to avoid slow import on module load
_model = None
_model_name = "BAAI/bge-m3"

_CORE_EMBED_CONCURRENCY = 10  # Max concurrent API calls


def _get_active_profile():
    """Return the active Qdrant profile, or None if unset."""
    from core.qdrant_profile import get_active_profile
    return get_active_profile()


def _get_active_provider() -> str:
    """Return the embedding provider name based on the active Qdrant profile."""
    profile = _get_active_profile()
    if profile:
        return profile.embedding_provider
    return "huggingface"  # default: local bge-m3


def get_model():
    """Get or initialize the bge-m3 embedding model (lazy loading)."""
    global _model
    if _model is None:
        logger.info(f"Loading embedding model: {_model_name}")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_model_name)
        logger.info(f"Model loaded. Dimension: {_model.get_sentence_embedding_dimension()}")
    return _model


def get_embedding_dimension() -> int:
    """Get the embedding dimension based on active profile."""
    profile = _get_active_profile()
    if profile:
        return profile.dimension
    return 1024  # bge-m3 default


def _embed_texts_via_core(texts: list[str]) -> np.ndarray:
    """
    Delegate embedding to core/embedding.py (async providers like OpenAI).
    Uses asyncio.gather with concurrency control for batch efficiency.
    """
    from core.embedding import get_embedding

    semaphore = asyncio.Semaphore(_CORE_EMBED_CONCURRENCY)

    async def _embed_with_limit(text: str):
        async with semaphore:
            return await get_embedding(text)

    async def _batch():
        return await asyncio.gather(*[_embed_with_limit(t) for t in texts])

    return np.array(asyncio.run(_batch()))


def embed_texts(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """
    Embed a list of texts using the active provider.

    Args:
        texts: List of texts to embed
        batch_size: Batch size for encoding

    Returns:
        numpy array of shape (len(texts), dimension)
    """
    if not texts:
        return np.array([])

    provider = _get_active_provider()

    if provider == "huggingface":
        model = get_model()
        return model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=len(texts) > 100,
            normalize_embeddings=True,  # L2 normalize for cosine similarity
        )

    logger.info(f"Using core embedding provider: {provider} for {len(texts)} texts")
    return _embed_texts_via_core(texts)


def embed_text(text: str) -> np.ndarray:
    """
    Embed a single text.

    Args:
        text: Text to embed

    Returns:
        numpy array of shape (dimension,)
    """
    return embed_texts([text])[0]


def warmup() -> None:
    """Warmup the model by loading it and running a test embedding."""
    provider = _get_active_provider()
    if provider == "huggingface":
        get_model().encode(["測試"])
        logger.info("Embedding model (bge-m3) warmed up")
    else:
        logger.info(f"Using external embedding provider ({provider}), skipping local warmup")
