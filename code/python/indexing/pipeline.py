"""
Indexing Pipeline for M0 Indexing Module.

Orchestrates the full indexing flow with checkpoint support.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from .chunking_engine import ChunkingEngine, Chunk
from .dual_storage import VaultStorage, MapPayload
from .ingestion_engine import CanonicalDataModel, IngestionEngine
from .quality_gate import QualityGate
from .source_manager import SourceManager
from .qdrant_uploader import QdrantUploader, QdrantConfig

logger = logging.getLogger(__name__)


@dataclass
class PipelineCheckpoint:
    """Checkpoint for resumable processing."""
    tsv_path: str
    processed_urls: set[str] = field(default_factory=set)
    failed_urls: dict[str, str] = field(default_factory=dict)  # url -> error
    last_processed_line: int = 0
    started_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            'tsv_path': self.tsv_path,
            'processed_urls': list(self.processed_urls),
            'failed_urls': self.failed_urls,
            'last_processed_line': self.last_processed_line,
            'started_at': self.started_at,
            'updated_at': self.updated_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PipelineCheckpoint':
        """Create from dict."""
        return cls(
            tsv_path=data['tsv_path'],
            processed_urls=set(data.get('processed_urls', [])),
            failed_urls=data.get('failed_urls', {}),
            last_processed_line=data.get('last_processed_line', 0),
            started_at=data.get('started_at', ''),
            updated_at=data.get('updated_at', '')
        )


@dataclass
class PipelineResult:
    """Result of pipeline execution."""
    success: int = 0
    failed: int = 0
    skipped: int = 0
    buffered: int = 0  # Quality gate failures
    total_chunks: int = 0


class IndexingPipeline:
    """
    Main indexing pipeline.

    Flow: TSV → Ingestion → QualityGate → Chunking → Storage
    """

    def __init__(
        self,
        vault: Optional[VaultStorage] = None,
        config_path: Optional[Path] = None,
        upload_to_qdrant: bool = False,
        qdrant_config: Optional[QdrantConfig] = None,
        task_id: str = "",
    ):
        """
        Initialize pipeline.

        Args:
            vault: VaultStorage instance (creates default if None)
            config_path: Path to config_indexing.yaml
            upload_to_qdrant: Whether to upload vectors to Qdrant
            qdrant_config: Qdrant configuration (uses env vars if None)
            task_id: Originating task ID for data lineage
        """
        self.ingestion = IngestionEngine()
        self.quality_gate = QualityGate(config_path)
        self.chunker = ChunkingEngine(config_path)
        self.source_manager = SourceManager(config_path)
        self.vault = vault or VaultStorage()
        self.task_id = task_id

        # Qdrant uploader (optional)
        self.upload_to_qdrant = upload_to_qdrant
        self.qdrant: Optional[QdrantUploader] = None
        if upload_to_qdrant:
            self.qdrant = QdrantUploader(qdrant_config)

        self._load_config(config_path)
        self.checkpoint: Optional[PipelineCheckpoint] = None
        self.checkpoint_file: Optional[Path] = None

        # Batch buffer for Qdrant upload: (chunk, site, MapPayload)
        self._chunk_buffer: list[tuple[Chunk, str, MapPayload]] = []

    def _load_config(self, config_path: Optional[Path]) -> None:
        """Load pipeline config."""
        self.checkpoint_interval = 10
        self.batch_size = 100

        if config_path is None:
            config_path = Path(__file__).parents[3] / "config" / "config_indexing.yaml"

        if not config_path.exists():
            return

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        pipeline_config = config.get('pipeline', {})
        self.checkpoint_interval = pipeline_config.get('checkpoint_interval', 10)
        self.batch_size = pipeline_config.get('batch_size', 100)

    def process_tsv(
        self,
        tsv_path: Path,
        site_override: Optional[str] = None
    ) -> PipelineResult:
        """
        Process a TSV file without checkpoint support.

        Args:
            tsv_path: Path to TSV file
            site_override: Override site for all articles

        Returns:
            PipelineResult with statistics
        """
        result = PipelineResult()

        for cdm in self.ingestion.parse_tsv_file(tsv_path):
            try:
                chunks_created = self._process_article(cdm, site_override)
                if chunks_created > 0:
                    result.success += 1
                    result.total_chunks += chunks_created
                elif chunks_created == 0:
                    result.buffered += 1
                else:
                    result.skipped += 1

                # Flush to Qdrant periodically
                if self.upload_to_qdrant and len(self._chunk_buffer) >= self.batch_size:
                    self._flush_qdrant_buffer()

            except Exception as e:
                logger.error(f"Failed to process article {cdm.url}: {e}")
                result.failed += 1

        # Final flush
        self._flush_qdrant_buffer()

        return result

    def process_tsv_resumable(
        self,
        tsv_path: Path,
        checkpoint_file: Optional[Path] = None,
        site_override: Optional[str] = None
    ) -> PipelineResult:
        """
        Process TSV with checkpoint support for resumption.

        Args:
            tsv_path: Path to TSV file
            checkpoint_file: Path to checkpoint file (default: tsv_path.checkpoint.json)
            site_override: Override site for all articles

        Returns:
            PipelineResult with statistics
        """
        # Setup checkpoint
        self.checkpoint_file = checkpoint_file or Path(f"{tsv_path}.checkpoint.json")
        self.checkpoint = self._load_checkpoint() or PipelineCheckpoint(
            tsv_path=str(tsv_path),
            started_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat()
        )

        result = PipelineResult()

        try:
            with open(tsv_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f):
                    # Skip already processed lines
                    if line_num < self.checkpoint.last_processed_line:
                        continue

                    cdm = self.ingestion.parse_tsv_line(line)
                    if cdm is None:
                        continue

                    # Skip already processed URLs
                    if cdm.url in self.checkpoint.processed_urls:
                        result.skipped += 1
                        continue

                    try:
                        chunks_created = self._process_article(cdm, site_override)
                        self.checkpoint.processed_urls.add(cdm.url)

                        if chunks_created > 0:
                            result.success += 1
                            result.total_chunks += chunks_created
                        else:
                            result.buffered += 1

                    except Exception as e:
                        logger.error(f"Failed to process article {cdm.url}: {e}")
                        self.checkpoint.failed_urls[cdm.url] = str(e)
                        result.failed += 1

                    # Flush to Qdrant periodically
                    if self.upload_to_qdrant and len(self._chunk_buffer) >= self.batch_size:
                        try:
                            self._flush_qdrant_buffer()
                        except Exception as e:
                            logger.error(f"Qdrant flush failed, NOT advancing checkpoint: {e}")
                            self._save_checkpoint()
                            raise

                    # Save checkpoint periodically
                    processed = result.success + result.failed + result.buffered
                    if processed % self.checkpoint_interval == 0:
                        self.checkpoint.last_processed_line = line_num + 1  # Next line to process
                        self.checkpoint.updated_at = datetime.utcnow().isoformat()
                        self._save_checkpoint()

        except Exception as e:
            # Save checkpoint on error
            self._save_checkpoint()
            raise

        # Final flush
        self._flush_qdrant_buffer()

        # Success: delete checkpoint
        self._delete_checkpoint()
        return result

    def _process_article(
        self,
        cdm: CanonicalDataModel,
        site_override: Optional[str]
    ) -> int:
        """
        Process a single article.

        Returns:
            Number of chunks created, 0 if buffered, -1 if skipped
        """
        # Quality gate
        qr = self.quality_gate.validate(cdm)
        if not qr.passed:
            self._buffer_article(cdm, qr.failure_reasons)
            return 0

        # Determine site
        site = site_override or cdm.source_id

        # Chunk article
        chunks = self.chunker.chunk_article(cdm)
        if not chunks:
            return 0

        # Store in vault
        self.vault.store_chunks(chunks)

        # Buffer chunks for Qdrant upload with article-level metadata
        if self.upload_to_qdrant:
            date_published_str = cdm.date_published.isoformat() if cdm.date_published else ""
            description = cdm.article_body[:200] if cdm.article_body else ""

            for chunk in chunks:
                payload = MapPayload.from_chunk(
                    chunk=chunk,
                    site=site,
                    headline=cdm.headline or "",
                    date_published=date_published_str,
                    author=cdm.author or "",
                    publisher=cdm.publisher or "",
                    keywords=cdm.keywords or [],
                    description=description,
                    task_id=self.task_id,
                )
                self._chunk_buffer.append((chunk, site, payload))

        return len(chunks)

    def _flush_qdrant_buffer(self) -> int:
        """
        Flush buffered chunks to Qdrant.

        Returns:
            Number of chunks uploaded
        """
        if not self.upload_to_qdrant or not self.qdrant or not self._chunk_buffer:
            return 0

        # Group by site, preserving chunk-payload pairs
        site_data: dict[str, tuple[list[Chunk], list[MapPayload]]] = {}
        for chunk, site, payload in self._chunk_buffer:
            chunks_list, payloads_list = site_data.setdefault(site, ([], []))
            chunks_list.append(chunk)
            payloads_list.append(payload)

        total_uploaded = 0
        for site, (chunks, payloads) in site_data.items():
            uploaded = self.qdrant.upload_chunks(chunks, site, payloads=payloads)
            total_uploaded += uploaded

        self._chunk_buffer.clear()

        # Truncate buffer.jsonl after successful flush to prevent unbounded growth
        buffer_path = Path(__file__).parents[3] / "data" / "indexing" / "buffer.jsonl"
        if buffer_path.exists():
            try:
                with open(buffer_path, 'w') as f:
                    pass  # Truncate
            except Exception as e:
                logger.warning(f"Failed to truncate buffer.jsonl: {e}")

        return total_uploaded

    def _buffer_article(self, cdm: CanonicalDataModel, reasons: list[str]) -> None:
        """Save failed article to buffer for review."""
        buffer_path = Path(__file__).parents[3] / "data" / "indexing" / "buffer.jsonl"
        buffer_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            'url': cdm.url,
            'headline': cdm.headline,
            'source_id': cdm.source_id,
            'reasons': reasons,
            'timestamp': datetime.utcnow().isoformat()
        }

        with open(buffer_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _load_checkpoint(self) -> Optional[PipelineCheckpoint]:
        """Load checkpoint from file."""
        if self.checkpoint_file and self.checkpoint_file.exists():
            with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return PipelineCheckpoint.from_dict(data)
        return None

    def _save_checkpoint(self) -> None:
        """Save checkpoint to file."""
        if self.checkpoint_file and self.checkpoint:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(self.checkpoint.to_dict(), f, ensure_ascii=False, indent=2)

    def _delete_checkpoint(self) -> None:
        """Delete checkpoint file after successful completion."""
        if self.checkpoint_file and self.checkpoint_file.exists():
            self.checkpoint_file.unlink()

    def reconcile(self, site: str = None, batch_size: int = 10000) -> dict:
        """
        Compare Vault chunk_ids vs Qdrant point_ids, re-upload missing chunks.

        Uses batch iterator to avoid OOM with large datasets.

        Args:
            site: Optional site filter
            batch_size: Chunk IDs per batch

        Returns:
            Dict with missing_fixed count
        """
        if not self.qdrant:
            self.qdrant = QdrantUploader()
            self.upload_to_qdrant = True

        missing_total = 0

        for vault_batch in self.vault.iter_chunk_ids(site=site, batch_size=batch_size):
            qdrant_existing = self.qdrant.check_exists(vault_batch)
            missing = vault_batch - qdrant_existing

            if missing:
                logger.info(f"Reconcile: {len(missing)} missing chunks in this batch")
                chunk_data = self.vault.get_chunks_by_ids(missing)

                # Convert to minimal Chunk objects for upload
                chunks = []
                for cd in chunk_data:
                    c = Chunk(
                        chunk_id=cd['chunk_id'],
                        article_url=cd['article_url'],
                        chunk_index=cd['chunk_index'],
                        sentences=[],
                        full_text=cd['full_text'],
                        summary=cd['full_text'][:200],
                        char_start=0,
                        char_end=len(cd['full_text']),
                    )
                    chunks.append(c)

                if chunks:
                    site_name = site or "unknown"
                    self.qdrant.upload_chunks(chunks, site_name)
                    missing_total += len(chunks)

        logger.info(f"Reconciliation complete: {missing_total} chunks re-uploaded")
        return {'missing_fixed': missing_total}

    def close(self) -> None:
        """Close resources."""
        self.vault.close()
        if self.qdrant:
            self.qdrant.close()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Index articles from TSV file')
    parser.add_argument('tsv_path', type=Path, nargs='?', help='Path to TSV file')
    parser.add_argument('--site', type=str, help='Override site for all articles')
    parser.add_argument('--resume', action='store_true', help='Resume from checkpoint')
    parser.add_argument('--checkpoint', type=Path, help='Custom checkpoint file path')
    parser.add_argument('--upload', action='store_true', help='Upload vectors to Qdrant')
    parser.add_argument('--reconcile', action='store_true',
                        help='Compare Vault vs Qdrant and re-upload missing chunks')

    args = parser.parse_args()

    # Reconcile mode: no TSV needed
    if args.reconcile:
        pipeline = IndexingPipeline(upload_to_qdrant=True)
        try:
            result = pipeline.reconcile(site=args.site)
            print(f"Missing chunks re-uploaded: {result['missing_fixed']}")
        finally:
            pipeline.close()
        return

    if not args.tsv_path:
        parser.error("tsv_path is required (unless --reconcile is used)")

    pipeline = IndexingPipeline(upload_to_qdrant=args.upload)

    try:
        if args.resume or args.checkpoint:
            result = pipeline.process_tsv_resumable(
                args.tsv_path,
                checkpoint_file=args.checkpoint,
                site_override=args.site
            )
        else:
            result = pipeline.process_tsv(args.tsv_path, site_override=args.site)

        print(f"Success: {result.success}")
        print(f"Failed: {result.failed}")
        print(f"Buffered: {result.buffered}")
        print(f"Skipped: {result.skipped}")
        print(f"Total chunks: {result.total_chunks}")

        # Show Qdrant info if uploaded
        if args.upload and pipeline.qdrant:
            info = pipeline.qdrant.get_collection_info()
            print(f"Qdrant vectors: {info['vectors_count']}")

    finally:
        pipeline.close()


if __name__ == '__main__':
    main()
