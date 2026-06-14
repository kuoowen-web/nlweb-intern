# Indexing Module (M0)
# Semantic chunking and dual-tier storage for news articles

# Phase 1: Core Infrastructure
from .source_manager import SourceManager, SourceTier

# Phase 2: Data Flow
from .ingestion_engine import IngestionEngine, CanonicalDataModel
from .quality_gate import QualityGate, QualityResult, QualityStatus
from .chunking_engine import ChunkingEngine, Chunk, make_chunk_id, parse_chunk_id

# Phase 3: Storage & Safety
from .dual_storage import VaultStorage, VaultConfig, MapPayload
from .rollback_manager import RollbackManager, MigrationRecord
from .pipeline import IndexingPipeline, PipelineResult, PipelineCheckpoint

# Phase 4: Integration Helpers
from .vault_helpers import (
    get_full_text_for_chunk,
    get_full_article_text,
    get_chunk_metadata,
    close_vault
)

__all__ = [
    # Phase 1
    'SourceManager',
    'SourceTier',
    # Phase 2
    'IngestionEngine',
    'CanonicalDataModel',
    'QualityGate',
    'QualityResult',
    'QualityStatus',
    'ChunkingEngine',
    'Chunk',
    'make_chunk_id',
    'parse_chunk_id',
    # Phase 3
    'VaultStorage',
    'VaultConfig',
    'MapPayload',
    'RollbackManager',
    'MigrationRecord',
    'IndexingPipeline',
    'PipelineResult',
    'PipelineCheckpoint',
    # Phase 4
    'get_full_text_for_chunk',
    'get_full_article_text',
    'get_chunk_metadata',
    'close_vault',
]
