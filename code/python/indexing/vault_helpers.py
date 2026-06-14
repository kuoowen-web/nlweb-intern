"""
Vault Helper Functions for M0 Indexing Module.

Provides async interface for retrieving full text from the Vault.
Used by retriever and reasoning modules.
"""

import asyncio
from typing import Optional

from .chunking_engine import parse_chunk_id
from .dual_storage import VaultStorage

# Global vault instance (lazy loaded)
_vault: Optional[VaultStorage] = None


def _get_vault() -> VaultStorage:
    """Get or create global vault instance."""
    global _vault
    if _vault is None:
        _vault = VaultStorage()
    return _vault


async def get_full_text_for_chunk(chunk_id: str) -> Optional[str]:
    """
    Get full text for a chunk from the Vault.

    Args:
        chunk_id: Chunk ID in format "article_url::chunk::N"

    Returns:
        Full text of the chunk, or None if not found
    """
    vault = _get_vault()
    return await asyncio.to_thread(vault.get_chunk, chunk_id)


async def get_full_article_text(article_url: str) -> Optional[str]:
    """
    Get full article text by concatenating all chunks.

    Args:
        article_url: Article URL

    Returns:
        Full article text (all chunks joined), or None if not found
    """
    vault = _get_vault()
    chunks = await asyncio.to_thread(vault.get_article_chunks, article_url)

    if not chunks:
        return None

    return ''.join(chunks)


def get_chunk_metadata(chunk_id: str) -> Optional[dict]:
    """
    Parse chunk ID to get metadata.

    Returns dict with article_url and chunk_index, or None if invalid.
    """
    try:
        article_url, chunk_index = parse_chunk_id(chunk_id)
        return {'article_url': article_url, 'chunk_index': chunk_index}
    except (ValueError, IndexError):
        return None


def close_vault() -> None:
    """Close the global vault connection."""
    global _vault
    if _vault is not None:
        _vault.close()
        _vault = None
