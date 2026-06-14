"""
PostgreSQL Batch Indexer.

Processes TSV files through the full pipeline into PostgreSQL:
    TSV → IngestionEngine → QualityGate → ChunkingEngine → PostgreSQLUploader

Usage:
    python -m indexing.pg_batch <tsv_path> --resume
    python -m indexing.pg_batch <tsv_path>  (no checkpoint)

PostgreSQLUploader handles embedding (Qwen3-4B INT8) + DB insert with
ON CONFLICT upsert for idempotency.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .chunking_engine import ChunkingEngine
from .ingestion_engine import IngestionEngine
from .postgresql_uploader import PostgreSQLUploader
from .quality_gate import QualityGate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

CHECKPOINT_INTERVAL = 10  # save every N articles


@dataclass
class PGCheckpoint:
    """Checkpoint for resumable PG indexing."""
    tsv_path: str
    processed_urls: set[str] = field(default_factory=set)
    failed_urls: dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    updated_at: str = ""

    def save(self, path: Path) -> None:
        data = {
            'tsv_path': self.tsv_path,
            'processed_urls': list(self.processed_urls),
            'failed_urls': self.failed_urls,
            'started_at': self.started_at,
            'updated_at': self.updated_at,
        }
        tmp = path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Optional['PGCheckpoint']:
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return cls(
                tsv_path=data['tsv_path'],
                processed_urls=set(data.get('processed_urls', [])),
                failed_urls=data.get('failed_urls', {}),
                started_at=data.get('started_at', ''),
                updated_at=data.get('updated_at', ''),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Corrupted checkpoint {path}: {e}")
            return None


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    success: int = 0
    failed: int = 0
    skipped_checkpoint: int = 0
    skipped_in_db: int = 0
    quality_rejected: int = 0
    total_chunks: int = 0


def process_tsv(
    tsv_path: Path,
    resume: bool = True,
    pre_indexed_urls: Optional[set[str]] = None,
) -> BatchResult:
    """
    Process a single TSV file into PostgreSQL.

    Args:
        tsv_path: Path to TSV file.
        resume: Whether to use checkpoint for resumption.
        pre_indexed_urls: Set of URLs already in PG (skip embedding).
    """
    ingestion = IngestionEngine()
    quality_gate = QualityGate()
    chunker = ChunkingEngine()
    uploader = PostgreSQLUploader()

    result = BatchResult()
    checkpoint_path = Path(f"{tsv_path}.pg_checkpoint.json")

    # Load or create checkpoint
    checkpoint = None
    if resume:
        checkpoint = PGCheckpoint.load(checkpoint_path)
    if checkpoint is None:
        checkpoint = PGCheckpoint(
            tsv_path=str(tsv_path),
            started_at=datetime.utcnow().isoformat(),
        )

    # Keep pre-indexed URLs as separate set (don't bloat checkpoint file)
    already_in_db = pre_indexed_urls or set()

    try:
        for cdm in ingestion.parse_tsv_file(tsv_path):
            if not cdm.is_valid:
                result.quality_rejected += 1
                continue

            # Skip already processed (checkpoint or DB)
            if cdm.url in checkpoint.processed_urls:
                result.skipped_checkpoint += 1
                continue
            if cdm.url in already_in_db:
                result.skipped_in_db += 1
                continue

            # Quality gate
            qr = quality_gate.validate(cdm)
            if not qr.passed:
                result.quality_rejected += 1
                checkpoint.processed_urls.add(cdm.url)
                continue

            # Chunk
            chunks = chunker.chunk_article(cdm)
            if not chunks:
                result.quality_rejected += 1
                checkpoint.processed_urls.add(cdm.url)
                continue

            # Upload to PG (handles embedding internally)
            try:
                ok = uploader.upload_article(cdm, chunks)
                if ok:
                    result.success += 1
                    result.total_chunks += len(chunks)
                    checkpoint.processed_urls.add(cdm.url)
                else:
                    result.failed += 1
                    checkpoint.failed_urls[cdm.url] = "upload_article returned False"
            except Exception as e:
                result.failed += 1
                checkpoint.failed_urls[cdm.url] = str(e)
                logger.error(f"Failed: {cdm.url}: {e}")

            # Save checkpoint periodically
            processed = result.success + result.failed + result.quality_rejected
            if processed % CHECKPOINT_INTERVAL == 0:
                checkpoint.updated_at = datetime.utcnow().isoformat()
                checkpoint.save(checkpoint_path)

    except KeyboardInterrupt:
        logger.warning("Interrupted — saving checkpoint")
        checkpoint.save(checkpoint_path)
        raise
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        checkpoint.save(checkpoint_path)
        raise

    # Success: save final checkpoint (don't delete — used for done tracking)
    checkpoint.updated_at = datetime.utcnow().isoformat()
    checkpoint.save(checkpoint_path)

    uploader.close()

    return result


def get_indexed_urls(uploader: PostgreSQLUploader) -> set[str]:
    """
    Get all article URLs that already have chunks in PG.
    This allows skipping embedding for already-indexed articles.
    """
    conn = uploader._get_connection()
    rows = conn.execute(
        "SELECT DISTINCT a.url FROM articles a "
        "INNER JOIN chunks c ON c.article_id = a.id"
    ).fetchall()
    return {r['url'] for r in rows}


# ---------------------------------------------------------------------------
# Batch mode: process all TSV files in a directory (single process)
# ---------------------------------------------------------------------------

def run_batch(tsv_dir: Path, done_file: Path, log_file: Path) -> None:
    """
    Process all TSV files in tsv_dir, tracking completion in done_file.
    Model loads once and persists across all files.
    """
    import glob

    tsv_files = sorted(glob.glob(str(tsv_dir / "*.tsv")))
    if not tsv_files:
        logger.error(f"No TSV files found in {tsv_dir}")
        return

    # Load done set
    done_file.touch(exist_ok=True)
    done_set = set()
    with open(done_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                done_set.add(line)

    remaining = [f for f in tsv_files if Path(f).name not in done_set]
    total = len(tsv_files)

    def log(msg: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        with open(log_file, 'a', encoding='utf-8') as lf:
            lf.write(line + "\n")

    log(f"=== NLWeb PG Batch Indexing ===")
    log(f"Total: {total} files, Done: {len(done_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All files already done!")
        return

    # Pre-fetch indexed URLs once
    log("Fetching already-indexed URLs from PG...")
    t0 = time.time()
    uploader = PostgreSQLUploader()
    try:
        indexed = get_indexed_urls(uploader)
    finally:
        uploader.close()
    log(f"Found {len(indexed)} already-indexed URLs in PG ({time.time()-t0:.1f}s)")

    # Force model pre-load (happens on first embed call, but let's be explicit)
    from .postgresql_uploader import _get_embedding_model
    log("Loading embedding model (one-time)...")
    t0 = time.time()
    _get_embedding_model()
    log(f"Model loaded in {time.time()-t0:.1f}s")

    # Process each file
    grand_success = 0
    grand_failed = 0
    grand_skipped = 0
    grand_chunks = 0

    initial_done = len(done_set)
    for i, tsv_file in enumerate(remaining):
        basename = Path(tsv_file).name
        file_num = initial_done + i + 1
        log(f"[{file_num}/{total}] Processing: {basename}")

        t0 = time.time()
        try:
            result = process_tsv(
                Path(tsv_file), resume=True, pre_indexed_urls=indexed,
            )
            elapsed = time.time() - t0

            log(f"  Success={result.success} Failed={result.failed} "
                f"Skipped(DB)={result.skipped_in_db} "
                f"Chunks={result.total_chunks} ({elapsed:.0f}s)")

            grand_success += result.success
            grand_failed += result.failed
            grand_skipped += result.skipped_in_db + result.skipped_checkpoint
            grand_chunks += result.total_chunks

            # Mark done
            with open(done_file, 'a') as f:
                f.write(basename + "\n")
            done_set.add(basename)

            # Update indexed URLs with newly processed
            # (so subsequent files can skip them too)
            if result.success > 0:
                uploader_tmp = PostgreSQLUploader()
                try:
                    indexed = get_indexed_urls(uploader_tmp)
                finally:
                    uploader_tmp.close()

        except KeyboardInterrupt:
            log(f"  INTERRUPTED at {basename} — checkpoint saved")
            break
        except Exception as e:
            log(f"  ERROR: {e}")
            log(f"  Skipping {basename}, continuing with next file")
            continue

    # Final stats
    uploader_final = PostgreSQLUploader()
    try:
        stats = uploader_final.get_stats()
    finally:
        uploader_final.close()

    log(f"=== Batch complete ===")
    log(f"Total: success={grand_success} failed={grand_failed} "
        f"skipped={grand_skipped} chunks={grand_chunks}")
    log(f"PostgreSQL: {stats['articles_count']} articles, "
        f"{stats['chunks_count']} chunks")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Index TSV into PostgreSQL')
    subparsers = parser.add_subparsers(dest='command')

    # Single file mode
    single = subparsers.add_parser('file', help='Process a single TSV file')
    single.add_argument('tsv_path', type=Path, help='Path to TSV file')
    single.add_argument('--resume', action='store_true', help='Resume from checkpoint')

    # Batch mode
    batch = subparsers.add_parser('batch', help='Process all TSV files in a directory')
    batch.add_argument('--dir', type=Path,
                       default=Path("C:/users/user/NLWeb/data/crawler/articles"),
                       help='Directory with TSV files')
    batch.add_argument('--done-file', type=Path,
                       default=Path("C:/users/user/NLWeb/data/.pg_indexing_done"),
                       help='File tracking completed TSVs')
    batch.add_argument('--log-file', type=Path,
                       default=Path("C:/users/user/NLWeb/data/pg_indexing.log"),
                       help='Log file path')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    if args.command == 'file':
        if not args.tsv_path.exists():
            print(f"ERROR: {args.tsv_path} not found", file=sys.stderr)
            sys.exit(1)

        logger.info("Fetching already-indexed URLs from PG...")
        t0 = time.time()
        uploader = PostgreSQLUploader()
        try:
            indexed = get_indexed_urls(uploader)
        finally:
            uploader.close()
        logger.info(f"Found {len(indexed)} already-indexed URLs in {time.time()-t0:.1f}s")

        t0 = time.time()
        result = process_tsv(args.tsv_path, resume=args.resume, pre_indexed_urls=indexed)
        elapsed = time.time() - t0

        print(f"Success: {result.success}")
        print(f"Failed: {result.failed}")
        print(f"Skipped (checkpoint): {result.skipped_checkpoint}")
        print(f"Skipped (in DB): {result.skipped_in_db}")
        print(f"Quality rejected: {result.quality_rejected}")
        print(f"Total chunks: {result.total_chunks}")
        print(f"Elapsed: {elapsed:.0f}s")

        uploader2 = PostgreSQLUploader()
        try:
            stats = uploader2.get_stats()
            print(f"PostgreSQL articles: {stats['articles_count']}")
            print(f"PostgreSQL chunks: {stats['chunks_count']}")
        finally:
            uploader2.close()

    elif args.command == 'batch':
        run_batch(args.dir, args.done_file, args.log_file)

    else:
        # Default: batch mode
        run_batch(
            tsv_dir=Path("C:/users/user/NLWeb/data/crawler/articles"),
            done_file=Path("C:/users/user/NLWeb/data/.pg_indexing_done"),
            log_file=Path("C:/users/user/NLWeb/data/pg_indexing.log"),
        )


if __name__ == '__main__':
    main()
