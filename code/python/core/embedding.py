# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Wrapper around the various embedding providers.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

from typing import Optional, List
import asyncio
import threading

from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger, LogLevel

logger = get_configured_logger("embedding_wrapper")

# Add locks for thread-safe provider access
_provider_locks = {
    "openai": threading.Lock(),
    "gemini": threading.Lock(),
    "azure_openai": threading.Lock(),
    "snowflake": threading.Lock(),
    "elasticsearch": threading.Lock()
}

async def get_embedding(
    text: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 30,
    query_params: Optional[dict] = None
) -> List[float]:
    """
    Get embedding for the provided text using the specified provider and model.
    
    Args:
        text: The text to embed
        provider: Optional provider name, defaults to preferred_embedding_provider
        model: Optional model name, defaults to the provider's configured model
        timeout: Maximum time to wait for embedding response in seconds
        query_params: Optional query parameters from HTTP request
        
    Returns:
        List of floats representing the embedding vector
    """
    # Allow overriding provider in development mode
    if CONFIG.is_development_mode() and query_params:
        if 'embedding_provider' in query_params:
            provider = query_params['embedding_provider']
            logger.debug(f"Overriding embedding provider to: {provider}")
    
    provider = provider or CONFIG.preferred_embedding_provider
    
    # Truncate text to 20k characters to avoid token limit issues
    MAX_CHARS = 20000
    original_length = len(text)
    if original_length > MAX_CHARS:
        text = text[:MAX_CHARS]
        logger.warning(f"Truncated text from {original_length} to {MAX_CHARS} characters for embedding generation")
    
    logger.debug(f"Getting embedding with provider: {provider}")
    logger.debug(f"Text length: {len(text)} chars")
    
    if provider not in CONFIG.embedding_providers:
        error_msg = f"Unknown embedding provider '{provider}'"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Get provider config using the helper method
    provider_config = CONFIG.get_embedding_provider(provider)
    if not provider_config:
        error_msg = f"Missing configuration for embedding provider '{provider}'"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Use the provided model or fall back to the configured model
    model_id = model or provider_config.model
    if not model_id:
        error_msg = f"No embedding model specified for provider '{provider}'"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.debug(f"Using embedding model: {model_id}")

    try:
        # Use a timeout wrapper for all embedding calls
        if provider == "openai":
            logger.debug("Getting OpenAI embeddings")
            from embedding_providers.openai_embedding import get_openai_embeddings
            result = await asyncio.wait_for(
                get_openai_embeddings(text, model=model_id),
                timeout=timeout
            )
            logger.debug(f"OpenAI embeddings received, dimension: {len(result)}")
            return result

        if provider == "gemini":
            logger.debug("Getting Gemini embeddings")
            from embedding_providers.gemini_embedding import get_gemini_embeddings
            result = await asyncio.wait_for(
                get_gemini_embeddings(text, model=model_id),
                timeout=timeout
            )
            logger.debug(f"Gemini embeddings received, dimension: {len(result)}")
            return result

        if provider == "huggingface":
            logger.debug("Getting HuggingFace embeddings")
            from embedding_providers.huggingface_embedding import get_huggingface_embedding
            result = await asyncio.wait_for(
                get_huggingface_embedding(text, model=model_id),
                timeout=timeout
            )
            logger.debug(f"HuggingFace embeddings received, dimension: {len(result)}")
            return result

        if provider == "qwen3":
            logger.debug("Getting Qwen3 embeddings")
            from embedding_providers.qwen3_embedding import get_qwen3_embedding
            result = await asyncio.wait_for(
                get_qwen3_embedding(text, model=model_id),
                timeout=timeout
            )
            logger.debug(f"Qwen3 embeddings received, dimension: {len(result)}")
            return result

        if provider == "openrouter":
            logger.debug("Getting OpenRouter embeddings")
            from embedding_providers.openrouter_embedding import get_openrouter_embedding
            result = await asyncio.wait_for(
                get_openrouter_embedding(text, model=model_id, timeout=float(timeout)),
                timeout=timeout
            )
            logger.debug(f"OpenRouter embeddings received, dimension: {len(result)}")
            return result

        error_msg = f"No embedding implementation for provider '{provider}'"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    except asyncio.TimeoutError:
        logger.error(f"Embedding request timed out after {timeout}s with provider {provider}")
        raise
    except Exception as e:
        logger.exception(f"Error during embedding generation with provider {provider}")
        logger.log_with_context(
            LogLevel.ERROR,
            "Embedding generation failed",
            {
                "provider": provider,
                "model": model_id,
                "text_length": len(text),
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )
        raise

async def batch_get_embeddings(
    texts: List[str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 60
) -> List[List[float]]:
    """
    Get embeddings for a batch of texts.
    
    Args:
        texts: List of texts to embed
        provider: Optional provider name, defaults to preferred_embedding_provider
        model: Optional model name, defaults to the provider's configured model
        timeout: Maximum time to wait for batch embedding response in seconds
        
    Returns:
        List of embedding vectors, each a list of floats
    """
    provider = provider or CONFIG.preferred_embedding_provider
    
    # Truncate texts to 20k characters to avoid token limit issues
    MAX_CHARS = 20000
    truncated_texts = []
    for i, text in enumerate(texts):
        original_length = len(text)
        if original_length > MAX_CHARS:
            truncated_text = text[:MAX_CHARS]
            truncated_texts.append(truncated_text)
            logger.warning(f"Truncated text {i} from {original_length} to {MAX_CHARS} characters for embedding generation")
        else:
            truncated_texts.append(text)
    texts = truncated_texts
    
    logger.debug(f"Getting batch embeddings with provider: {provider}")
    logger.debug(f"Batch size: {len(texts)} texts")
    
    # Get provider config using the helper method
    provider_config = CONFIG.get_embedding_provider(provider)
    if not provider_config:
        error_msg = f"Missing configuration for embedding provider '{provider}'"
        logger.error(error_msg)
        raise ValueError(error_msg)
        
    model_id = model or provider_config.model
    if not model_id:
        error_msg = f"No embedding model specified for provider '{provider}'"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    try:
        # Provider-specific batch implementations with timeout handling
        if provider == "openai":
            logger.debug("Getting OpenAI batch embeddings")
            from embedding_providers.openai_embedding import get_openai_batch_embeddings
            result = await asyncio.wait_for(
                get_openai_batch_embeddings(texts, model=model_id),
                timeout=timeout
            )
            logger.debug(f"OpenAI batch embeddings received, count: {len(result)}")
            return result

        if provider == "gemini":
            logger.debug("Getting Gemini batch embeddings (sequential)")
            from embedding_providers.gemini_embedding import get_gemini_batch_embeddings
            result = await asyncio.wait_for(
                get_gemini_batch_embeddings(texts, model=model_id),
                timeout=timeout
            )
            logger.debug(f"Gemini batch embeddings received, count: {len(result)}")
            return result

        if provider == "huggingface":
            logger.debug("Getting HuggingFace batch embeddings")
            from embedding_providers.huggingface_embedding import get_huggingface_batch_embeddings
            result = await asyncio.wait_for(
                get_huggingface_batch_embeddings(texts, model=model_id),
                timeout=timeout
            )
            logger.debug(f"HuggingFace batch embeddings received, count: {len(result)}")
            return result

        if provider == "qwen3":
            logger.debug("Getting Qwen3 batch embeddings")
            from embedding_providers.qwen3_embedding import get_qwen3_batch_embeddings
            result = await asyncio.wait_for(
                get_qwen3_batch_embeddings(texts, model=model_id),
                timeout=timeout
            )
            logger.debug(f"Qwen3 batch embeddings received, count: {len(result)}")
            return result

        if provider == "openrouter":
            logger.debug("Getting OpenRouter batch embeddings")
            from embedding_providers.openrouter_embedding import get_openrouter_batch_embeddings
            result = await asyncio.wait_for(
                get_openrouter_batch_embeddings(texts, model=model_id, timeout=float(timeout)),
                timeout=timeout
            )
            logger.debug(f"OpenRouter batch embeddings received, count: {len(result)}")
            return result

        # Removed batch providers: ollama, elasticsearch (unified on OpenAI / Gemini)

        # Default implementation if provider doesn't match any above
        logger.debug(f"No specific batch implementation for {provider}, processing sequentially")
        results = []
        for text in texts:
            embedding = await get_embedding(text, provider, model)
            results.append(embedding)
        
        return results
        
    except asyncio.TimeoutError:
        logger.error(f"Batch embedding request timed out after {timeout}s with provider {provider}")
        raise
    except Exception as e:
        logger.exception(f"Error during batch embedding generation with provider {provider}")
        logger.log_with_context(
            LogLevel.ERROR,
            "Batch embedding generation failed",
            {
                "provider": provider,
                "model": model_id,
                "batch_size": len(texts),
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )
        raise