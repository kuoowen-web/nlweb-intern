"""
qdrant_uploader.py - Qdrant 向量上傳模組

負責將 chunks 的 embedding 上傳到 Qdrant。
"""

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    OptimizersConfigDiff,
)

from .embedding import embed_texts, get_embedding_dimension
from .chunking_engine import Chunk
from .dual_storage import MapPayload

logger = logging.getLogger(__name__)


@dataclass
class QdrantConfig:
    """Qdrant connection configuration."""
    url: str = "http://localhost:6333"
    api_key: Optional[str] = None
    collection_name: str = "nlweb"

    @classmethod
    def from_env(cls) -> "QdrantConfig":
        """Load config from active Qdrant profile, falling back to environment variables."""
        from core.qdrant_profile import get_active_qdrant_config
        profile_config = get_active_qdrant_config()
        if profile_config:
            return profile_config
        return cls(
            url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
            api_key=os.environ.get("QDRANT_API_KEY"),
            collection_name=os.environ.get("QDRANT_COLLECTION", "nlweb"),
        )


class QdrantUploader:
    """
    Upload chunks to Qdrant vector database.

    Handles:
    - Collection creation with proper vector config
    - Batch embedding generation
    - Batch upsert to Qdrant
    """

    def __init__(self, config: Optional[QdrantConfig] = None):
        """
        Initialize the uploader.

        Args:
            config: Qdrant configuration. If None, loads from environment.
        """
        self.config = config or QdrantConfig.from_env()
        self.client = QdrantClient(
            url=self.config.url,
            api_key=self.config.api_key,
        )
        self.logger = logging.getLogger(self.__class__.__name__)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Ensure the collection exists with proper configuration."""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.config.collection_name for c in collections)

        if not exists:
            self.logger.info(f"Creating collection: {self.config.collection_name}")
            self.client.create_collection(
                collection_name=self.config.collection_name,
                vectors_config=VectorParams(
                    size=get_embedding_dimension(),
                    distance=Distance.COSINE,
                ),
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=10000,  # Start indexing after 10k points
                ),
            )
            self.logger.info(f"Collection created: {self.config.collection_name}")
        else:
            self.logger.info(f"Collection exists: {self.config.collection_name}")

    def upload_chunks(
        self,
        chunks: list[Chunk],
        site: str,
        payloads: Optional[list[MapPayload]] = None,
        batch_size: int = 64
    ) -> int:
        """
        Upload chunks to Qdrant.

        Args:
            chunks: List of Chunk objects to upload
            site: Site identifier (e.g., 'ltn', 'udn')
            payloads: Optional pre-built MapPayload list (1:1 with chunks).
                      If None, builds minimal payload from chunk data.
            batch_size: Batch size for embedding and upload

        Returns:
            Number of chunks uploaded
        """
        if not chunks:
            return 0

        total_uploaded = 0

        # Process in batches
        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_payloads = payloads[i:i + batch_size] if payloads else None

            # Get texts for embedding (use embedding_text if available, else full_text)
            texts = [
                c.embedding_text if c.embedding_text else c.full_text
                for c in batch_chunks
            ]

            # Generate embeddings with retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    embeddings = embed_texts(texts)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        wait = 2 ** attempt  # 1s, 2s, 4s
                        self.logger.warning(f"embed_texts failed (attempt {attempt+1}), retrying in {wait}s: {e}")
                        time.sleep(wait)
                    else:
                        self.logger.error(f"embed_texts failed after {max_retries} attempts: {e}")
                        raise

            # Validate embedding count matches input
            if len(embeddings) != len(texts):
                raise ValueError(f"Embedding count mismatch: got {len(embeddings)} embeddings for {len(texts)} texts")

            # Create points
            points = []
            for j, chunk in enumerate(batch_chunks):
                if batch_payloads:
                    payload_dict = batch_payloads[j].to_dict()
                else:
                    payload_dict = {
                        "url": chunk.article_url,
                        "name": chunk.summary,
                        "site": site,
                        "chunk_id": chunk.chunk_id,
                        "article_url": chunk.article_url,
                        "chunk_index": chunk.chunk_index,
                        "char_start": chunk.char_start,
                        "char_end": chunk.char_end,
                        "version": 2,
                    }

                points.append(PointStruct(
                    id=self._generate_point_id(chunk.chunk_id),
                    vector=embeddings[j].tolist(),
                    payload=payload_dict,
                ))

            # Upsert to Qdrant
            self.client.upsert(
                collection_name=self.config.collection_name,
                points=points,
            )

            total_uploaded += len(batch_chunks)
            self.logger.info(f"Uploaded {total_uploaded}/{len(chunks)} chunks")

        return total_uploaded

    # Deterministic namespace for UUID5 generation (stable across restarts)
    _UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "nlweb.chunk")

    def _generate_point_id(self, chunk_id: str) -> str:
        """
        Generate a UUID5 point ID from chunk_id.

        UUID5 is deterministic (same chunk_id → same UUID) and based on SHA-1
        (128-bit), making collisions effectively impossible.
        """
        return str(uuid.uuid5(self._UUID_NAMESPACE, chunk_id))

    def get_collection_info(self) -> dict:
        """Get collection statistics."""
        info = self.client.get_collection(self.config.collection_name)
        return {
            "name": self.config.collection_name,
            "vectors_count": info.vectors_count,
            "points_count": info.points_count,
            "status": info.status,
        }

    def check_exists(self, chunk_ids: set) -> set:
        """
        Check which chunk_ids already exist in Qdrant as point IDs.

        Args:
            chunk_ids: Set of chunk_id strings to check

        Returns:
            Set of chunk_ids that exist in Qdrant
        """
        existing = set()
        # Convert chunk_ids to point IDs and check in batches
        id_map = {self._generate_point_id(cid): cid for cid in chunk_ids}
        point_ids = list(id_map.keys())

        # Qdrant scroll with point ID filter, batch of 100
        failed_batches = 0
        for i in range(0, len(point_ids), 100):
            batch = point_ids[i:i + 100]
            try:
                results = self.client.retrieve(
                    collection_name=self.config.collection_name,
                    ids=batch,
                )
                for point in results:
                    pid = point.id
                    if pid in id_map:
                        existing.add(id_map[pid])
            except Exception as e:
                failed_batches += 1
                self.logger.error(f"Error checking existence batch {i//100 + 1}: {e}")

        if failed_batches > 0:
            self.logger.warning(
                f"check_exists: {failed_batches} batch(es) failed — "
                f"result may be incomplete ({len(existing)}/{len(chunk_ids)} confirmed)"
            )

        return existing

    def delete_by_article_url(self, article_url: str):
        """
        Delete all chunks for a specific article.

        Args:
            article_url: The article URL to delete chunks for

        Returns:
            UpdateResult status from Qdrant (e.g. UpdateStatus.COMPLETED)
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        result = self.client.delete(
            collection_name=self.config.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="article_url",
                        match=MatchValue(value=article_url)
                    )
                ]
            ),
        )
        return result.status

    def close(self) -> None:
        """Close the client connection."""
        self.client.close()
