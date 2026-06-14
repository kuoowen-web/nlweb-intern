# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Async processor for user-uploaded files.

Handles the complete processing pipeline:
1. Parse file to extract text
2. Chunk text into smaller pieces
3. Generate embeddings for each chunk
4. Index vectors in PostgreSQL (user_document_chunks)
5. Update database metadata
"""

import asyncio
from typing import Dict, Any, List, Optional, Callable

from core.user_data_manager import get_user_data_manager
from core.chunking import chunk_text
from core.embedding import get_embedding
from retrieval_providers.user_postgres_provider import get_user_postgres_provider
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_data_processor")


class UserDataProcessor:
    """Async processor for user data files."""

    def __init__(self):
        """Initialize the processor."""
        self.manager = get_user_data_manager()
        logger.info("UserDataProcessor initialized")

    async def process_file(
        self,
        user_id: str,
        source_id: str,
        progress_callback: Optional[Callable[[int, str, str], None]] = None,
        org_id: str = None,
    ) -> Dict[str, Any]:
        """
        Process an uploaded file through the complete pipeline.

        Args:
            user_id: User identifier
            source_id: Source identifier
            progress_callback: Optional callback function(progress_percent, status, message)
            org_id: Organization identifier (stored in PostgreSQL for org-level isolation)

        Returns:
            Processing result dictionary
        """
        try:
            # Update status to processing
            await self.manager.update_source_status(source_id, 'processing')

            # Step 1: Parse file (25% progress)
            if progress_callback:
                progress_callback(25, 'parsing', '正在解析文件...')

            file_path = self.manager.storage.get_file_path(user_id, source_id)
            parsed = self.manager.parse_file(file_path)
            text = parsed['text']
            file_metadata = parsed['metadata']

            logger.info(f"Parsed file: {len(text)} characters")

            # Step 2: Chunk text (50% progress)
            if progress_callback:
                progress_callback(50, 'chunking', '正在分割文本...')

            chunk_size = self.manager.config['processing']['chunk_size']
            chunk_overlap = self.manager.config['processing']['chunk_overlap']

            chunks = chunk_text(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                metadata=file_metadata
            )

            logger.info(f"Created {len(chunks)} chunks")

            # Step 3: Create document record first to get consistent doc_id
            checksum = self.manager.compute_checksum(text)
            doc_id = await self.manager.create_document_record(source_id, checksum, len(chunks))

            # Step 4: Generate embeddings and index to PostgreSQL (75% progress)
            if progress_callback:
                progress_callback(75, 'embedding', '正在生成向量並索引...')

            await self._index_chunks(user_id, source_id, doc_id, chunks, org_id=org_id)

            await self.manager.update_source_status(source_id, 'ready')

            if progress_callback:
                progress_callback(100, 'completed', '處理完成！')

            logger.info(f"File processing completed: source_id={source_id}, doc_id={doc_id}")

            return {
                'success': True,
                'doc_id': doc_id,
                'chunk_count': len(chunks),
                'char_count': len(text)
            }

        except Exception as e:
            error_msg = str(e) or type(e).__name__
            logger.exception(f"File processing failed: {error_msg}")
            await self.manager.update_source_status(source_id, 'failed', error_msg)

            if progress_callback:
                progress_callback(0, 'failed', f'處理失敗: {error_msg}')

            return {
                'success': False,
                'error': error_msg
            }

    async def _get_embedding_with_retry(
        self,
        text: str,
        chunk_index: int,
        total_chunks: int,
        max_retries: int = 3,
        base_delay: float = 2.0,
        timeout: int = 60,
    ) -> List[float]:
        """
        Get embedding for a single chunk with retry and exponential backoff.

        Args:
            text: Text content to embed
            chunk_index: Index of the chunk (for logging)
            total_chunks: Total number of chunks (for logging)
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds for exponential backoff
            timeout: Timeout per embedding request in seconds

        Returns:
            Embedding vector as list of floats

        Raises:
            Last exception if all retries are exhausted
        """
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                embedding = await get_embedding(text, timeout=timeout)
                if attempt > 0:
                    logger.info(
                        f"Chunk {chunk_index}/{total_chunks}: embedding succeeded on retry {attempt}"
                    )
                return embedding
            except (asyncio.TimeoutError, Exception) as e:
                last_error = e
                error_name = type(e).__name__
                # Check if it's a retryable error (timeout or network)
                is_retryable = isinstance(e, (asyncio.TimeoutError,)) or \
                    'Timeout' in error_name or 'ConnectionError' in error_name
                if not is_retryable or attempt >= max_retries:
                    logger.error(
                        f"Chunk {chunk_index}/{total_chunks}: embedding failed after "
                        f"{attempt + 1} attempt(s): {error_name}"
                    )
                    raise
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Chunk {chunk_index}/{total_chunks}: {error_name}, "
                    f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)

        raise last_error  # Should not reach here, but safety net

    async def _index_chunks(
        self,
        user_id: str,
        source_id: str,
        doc_id: str,
        chunks: List[Dict[str, Any]],
        org_id: str = None,
    ):
        """
        Generate embeddings for each chunk and insert them into PostgreSQL.

        Args:
            user_id: User identifier
            source_id: Source identifier
            doc_id: Document identifier (from database)
            chunks: List of chunk dictionaries
            org_id: Organization identifier (stored for filtering)
        """
        if not chunks:
            raise ValueError(f"No chunks provided for source_id={source_id}")

        try:
            provider = get_user_postgres_provider()
            total = len(chunks)

            rows = []
            for i, chunk in enumerate(chunks):
                embedding = await self._get_embedding_with_retry(
                    text=chunk['content'],
                    chunk_index=i,
                    total_chunks=total,
                    max_retries=3,
                    timeout=60,
                )
                rows.append({
                    'user_id': user_id,
                    'org_id': org_id,
                    'source_id': source_id,
                    'doc_id': doc_id,
                    'chunk_index': chunk['chunk_index'],
                    'total_chunks': chunk['metadata']['total_chunks'],
                    'content': chunk['content'],
                    'metadata': chunk['metadata'],
                    'embedding': embedding,
                })
                if (i + 1) % 10 == 0 or i == total - 1:
                    logger.info(f"Embedding progress: {i + 1}/{total} chunks")

            inserted = await provider.insert_chunks(rows)
            logger.info(f"Indexed {inserted} chunks to PostgreSQL for source_id={source_id}")

        except Exception as e:
            logger.exception(f"Failed to index chunks: {str(e)}")
            raise


# Global processor instance
_processor_instance = None


def get_user_data_processor() -> UserDataProcessor:
    """
    Get or create the global UserDataProcessor instance.

    Returns:
        UserDataProcessor instance
    """
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = UserDataProcessor()
    return _processor_instance
