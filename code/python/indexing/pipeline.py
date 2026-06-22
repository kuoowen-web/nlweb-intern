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

from .chunking_engine import ChunkingEngine
from .dual_storage import VaultStorage
from .ingestion_engine import CanonicalDataModel, IngestionEngine
from .quality_gate import QualityGate
from .source_manager import SourceManager

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
        task_id: str = "",
    ):
        """
        Initialize pipeline.

        Args:
            vault: VaultStorage instance (creates default if None)
            config_path: Path to config_indexing.yaml
            task_id: Originating task ID for data lineage
        """
        self.ingestion = IngestionEngine()
        self.quality_gate = QualityGate(config_path)
        self.chunker = ChunkingEngine(config_path)
        self.source_manager = SourceManager(config_path)
        self.vault = vault or VaultStorage()
        self.task_id = task_id

        self._load_config(config_path)
        self.checkpoint: Optional[PipelineCheckpoint] = None
        self.checkpoint_file: Optional[Path] = None

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

            except Exception as e:
                logger.error(f"Failed to process article {cdm.url}: {e}")
                result.failed += 1

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

        # Chunk article
        chunks = self.chunker.chunk_article(cdm)
        if not chunks:
            return 0

        # Store in vault
        self.vault.store_chunks(chunks)

        return len(chunks)

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

    def close(self) -> None:
        """Close resources."""
        self.vault.close()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Index articles from TSV file')
    parser.add_argument('tsv_path', type=Path, nargs='?', help='Path to TSV file')
    parser.add_argument('--site', type=str, help='Override site for all articles')
    parser.add_argument('--resume', action='store_true', help='Resume from checkpoint')
    parser.add_argument('--checkpoint', type=Path, help='Custom checkpoint file path')

    args = parser.parse_args()

    if not args.tsv_path:
        parser.error("tsv_path is required")

    pipeline = IndexingPipeline()

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

    finally:
        pipeline.close()


if __name__ == '__main__':
    main()
