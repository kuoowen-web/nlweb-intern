# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Text chunking utilities for splitting documents into smaller pieces for embedding.
"""

from typing import List, Dict, Any
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("chunking")

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken not installed. Will use character-based chunking as fallback.")


class TextChunker:
    """Text chunking utility with token-aware splitting."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50, encoding_name: str = "cl100k_base"):
        """
        Initialize text chunker.

        Args:
            chunk_size: Target number of tokens per chunk
            chunk_overlap: Number of tokens to overlap between chunks
            encoding_name: Tiktoken encoding name (default: cl100k_base for GPT-4/text-embedding-ada-002)
        """
        # Validate parameters to prevent infinite loop
        if chunk_overlap >= chunk_size:
            logger.warning(f"chunk_overlap ({chunk_overlap}) >= chunk_size ({chunk_size}), clamping to chunk_size - 1")
            chunk_overlap = max(0, chunk_size - 1)

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        if TIKTOKEN_AVAILABLE:
            try:
                self.encoding = tiktoken.get_encoding(encoding_name)
                self.use_tokens = True
                logger.info(f"Using token-based chunking with {encoding_name}")
            except Exception as e:
                logger.warning(f"Failed to load tiktoken encoding: {e}. Falling back to character-based chunking.")
                self.use_tokens = False
        else:
            self.use_tokens = False
            logger.info("Using character-based chunking (tiktoken not available)")

    def chunk_text(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Split text into chunks.

        Args:
            text: Input text to chunk
            metadata: Optional metadata to attach to each chunk

        Returns:
            List of chunk dictionaries with 'content', 'metadata', and 'chunk_index' keys
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for chunking")
            return []

        if self.use_tokens:
            chunks = self._chunk_by_tokens(text)
        else:
            chunks = self._chunk_by_characters(text)

        # Attach metadata to each chunk
        result = []
        for i, chunk_text in enumerate(chunks):
            chunk_metadata = metadata.copy() if metadata else {}
            chunk_metadata['chunk_index'] = i
            chunk_metadata['total_chunks'] = len(chunks)

            result.append({
                'content': chunk_text,
                'metadata': chunk_metadata,
                'chunk_index': i
            })

        logger.info(f"Created {len(result)} chunks from text ({len(text)} chars)")
        return result

    def _chunk_by_tokens(self, text: str) -> List[str]:
        """
        Chunk text by tokens using tiktoken.

        Args:
            text: Input text

        Returns:
            List of text chunks
        """
        # Encode text to tokens
        tokens = self.encoding.encode(text)
        total_tokens = len(tokens)

        chunks = []
        start_idx = 0

        while start_idx < total_tokens:
            # Calculate end index
            end_idx = min(start_idx + self.chunk_size, total_tokens)

            # Extract chunk tokens
            chunk_tokens = tokens[start_idx:end_idx]

            # Decode back to text
            chunk_text = self.encoding.decode(chunk_tokens)
            chunks.append(chunk_text)

            # Move to next chunk with overlap
            start_idx = end_idx - self.chunk_overlap

            # Prevent infinite loop
            if end_idx >= total_tokens:
                break

        return chunks

    def _chunk_by_characters(self, text: str) -> List[str]:
        """
        Chunk text by characters (fallback when tiktoken not available).

        Uses approximate 4 chars per token ratio.

        Args:
            text: Input text

        Returns:
            List of text chunks
        """
        # Approximate: 1 token ≈ 4 characters for English text
        chars_per_token = 4
        chunk_size_chars = self.chunk_size * chars_per_token
        overlap_chars = self.chunk_overlap * chars_per_token

        chunks = []
        start_idx = 0
        text_length = len(text)

        while start_idx < text_length:
            end_idx = min(start_idx + chunk_size_chars, text_length)

            # Try to break at sentence boundary
            if end_idx < text_length:
                # Look for sentence endings within last 20% of chunk
                search_start = max(start_idx, end_idx - int(chunk_size_chars * 0.2))
                sentence_end = max(
                    text.rfind('. ', search_start, end_idx),
                    text.rfind('。', search_start, end_idx),
                    text.rfind('! ', search_start, end_idx),
                    text.rfind('? ', search_start, end_idx),
                    text.rfind('\n\n', search_start, end_idx)
                )

                if sentence_end > start_idx:
                    end_idx = sentence_end + 1

            chunk_text = text[start_idx:end_idx].strip()
            if chunk_text:
                chunks.append(chunk_text)

            # Move to next chunk with overlap
            start_idx = end_idx - overlap_chars

            # Prevent infinite loop
            if start_idx >= text_length or end_idx >= text_length:
                break

        return chunks


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50,
               metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Convenience function to chunk text.

    Args:
        text: Input text
        chunk_size: Target tokens per chunk
        chunk_overlap: Token overlap between chunks
        metadata: Optional metadata for chunks

    Returns:
        List of chunk dictionaries
    """
    chunker = TextChunker(chunk_size, chunk_overlap)
    return chunker.chunk_text(text, metadata)
