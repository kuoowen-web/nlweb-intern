# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Gemini embedding implementation using Google GenAI.

WARNING: This code is under development and may undergo changes in future
releases. Backwards compatibility is not guaranteed at this time.
"""

import os
import asyncio
import threading
from typing import List, Optional

from google import genai
from google.genai import types
from core.config import CONFIG
from core.retry_util import retry_async

from misc.logger.logging_config_helper import get_configured_logger, LogLevel
logger = get_configured_logger("gemini_embedding")

# Add lock for thread-safe client initialization
_client_lock = threading.Lock()
_client = None

# MP-1 (full-scan 批7): Gemini 429 重試改為 bounded exponential backoff（對齊
# openrouter/deepinfra 的 retry_async 慣例），取代原本的 `while True` + 同步
# time.sleep(5)（無上限自旋 + 阻塞 event loop）。Gemini SDK 對 rate-limit 拋的
# 例外訊息含 "429"，用訊息比對判定 retryable；耗盡後由 retry_async raise 最後一個
# 例外（保留原訊息，no silent fail）。
_GEMINI_EMBED_MAX_RETRIES = 3


def _is_gemini_rate_limit(exc: BaseException) -> bool:
    """判斷 Gemini embedding 例外是否為可重試的 429 rate-limit。"""
    return "429" in str(exc)


def get_api_key() -> str:
    """
    Retrieve the API key for Gemini API from configuration.
    """
    # Get the API key from the embedding provider config
    provider_config = CONFIG.get_embedding_provider("gemini")
    
    if provider_config and provider_config.api_key:
        api_key = provider_config.api_key
        if api_key:
            return api_key.strip('"')  # Remove quotes if present
    
    # Fallback to environment variables
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        error_msg = "Gemini API key not found in configuration or environment"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    return api_key


def get_client():
    """
    Get or create the GenAI client for embeddings.
    """
    global _client
    with _client_lock:
        if _client is None:
            api_key = get_api_key()
            if not api_key:
                error_msg = "Gemini API key not found in configuration"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            _client = genai.Client(api_key=api_key)
            logger.debug("GenAI client initialized successfully")
        return _client


async def get_gemini_embeddings(
    text: str,
    model: Optional[str] = None,
    timeout: float = 30.0,
    task_type: str = "SEMANTIC_SIMILARITY"
) -> List[float]:
    """
    Generate an embedding for a single text using Google GenAI.
    
    Args:
        text: The text to embed
        model: Optional model ID to use, defaults to provider's configured
               model
        timeout: Maximum time to wait for the embedding response in seconds
        task_type: The task type for the embedding (e.g.,
                  "SEMANTIC_SIMILARITY", "RETRIEVAL_QUERY", etc.)
        
    Returns:
        List of floats representing the embedding vector
    """
    # If model not provided, get it from config
    if model is None:
        provider_config = CONFIG.get_embedding_provider("gemini")
        if provider_config and provider_config.model:
            model = provider_config.model
        else:
            # Default to a common Gemini embedding model
            model = "gemini-embedding-exp-03-07"
    
    logger.debug(f"Generating Gemini embedding with model: {model}")
    logger.debug(f"Text length: {len(text)} chars")
    
    # Get the GenAI client
    client = get_client()

    # Create embedding config
    config = types.EmbedContentConfig(task_type=task_type)

    async def _embed_once():
        # Use asyncio.to_thread to make the synchronous GenAI call non-blocking
        result = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: client.models.embed_content(
                    model=model,
                    contents=text,
                    config=config
                )
            ),
            timeout=timeout
        )
        return result.embeddings[0].values

    try:
        # MP-1: bounded retry（exp backoff）+ async sleep（不阻塞 event loop）。
        # 429 才重試；非 429（含 timeout / auth / schema）由 predicate 判 False →
        # retry_async 立即 raise（fail loud）。429 耗盡也 raise（no silent fail）。
        embedding = await retry_async(
            _embed_once,
            max_retries=_GEMINI_EMBED_MAX_RETRIES,
            is_retryable=_is_gemini_rate_limit,
            description="gemini_embedding",
        )
        logger.debug(
            f"Gemini embedding generated, dimension: {len(embedding)}"
        )
        return embedding
    except Exception as e:
        logger.exception("Error generating Gemini embedding")
        logger.log_with_context(
            LogLevel.ERROR,
            "Gemini embedding generation failed",
            {
                "model": model,
                "text_length": len(text),
                "error_type": type(e).__name__,
                "error_message": str(e),
            }
        )
        raise


async def get_gemini_batch_embeddings(
    texts: List[str],
    model: Optional[str] = None,
    timeout: float = 60.0,
    task_type: str = "SEMANTIC_SIMILARITY"
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts using Google GenAI.
    
    Note: Gemini API processes embeddings one at a time, so this function
    makes multiple sequential calls for batch processing.
    
    Args:
        texts: List of texts to embed
        model: Optional model ID to use, defaults to provider's configured
               model
        timeout: Maximum time to wait for each embedding response in seconds
        task_type: The task type for the embedding (e.g.,
                  "SEMANTIC_SIMILARITY", "RETRIEVAL_QUERY", etc.)
        
    Returns:
        List of embedding vectors, each a list of floats
    """
    # If model not provided, get it from config
    if model is None:
        provider_config = CONFIG.get_embedding_provider("gemini")
        if provider_config and provider_config.model:
            model = provider_config.model
        else:
            # Default to a common Gemini embedding model
            model = "gemini-embedding-exp-03-07"
    
    logger.debug(f"Generating Gemini batch embeddings with model: {model}")
    logger.debug(f"Batch size: {len(texts)} texts")
    
    # Get the GenAI client
    client = get_client()
    embeddings = []

    # Create embedding config
    config = types.EmbedContentConfig(task_type=task_type)
    
    # Process each text individually
    for i, text in enumerate(texts):
        logger.debug(f"Processing text {i+1}/{len(texts)}")

        async def _embed_once(t=text):
            # Use asyncio.to_thread to make the synchronous GenAI call non-blocking
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda tt=t: client.models.embed_content(
                        model=model,
                        contents=tt,
                        config=config
                    )
                ),
                timeout=timeout
            )
            return result.embeddings[0].values

        try:
            # MP-1: bounded retry + async sleep（取代 while True + time.sleep）。
            embedding = await retry_async(
                _embed_once,
                max_retries=_GEMINI_EMBED_MAX_RETRIES,
                is_retryable=_is_gemini_rate_limit,
                description=f"gemini_batch_embedding[{i}]",
            )
            embeddings.append(embedding)
        except Exception as e:
            logger.exception("Error generating Gemini batch embedding in batch")
            logger.log_with_context(
                LogLevel.ERROR,
                "Gemini batch embedding generation failed",
                {
                    "model": model,
                    "batch_size": len(texts),
                    "text_length": len(text),
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }
            )
            raise

    logger.debug(
        f"Gemini batch embeddings generated, count: {len(embeddings)}"
    )
    return embeddings


# Note: The GenAI client handles single embeddings efficiently.
# Batch processing can be implemented by making multiple calls if needed.
