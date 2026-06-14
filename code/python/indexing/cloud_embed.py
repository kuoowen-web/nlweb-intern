"""
Cloud Embedding Script — runs on GCP GPU VM.

Reads TSV files, does chunking + Qwen3-4B embedding, outputs JSONL+NPY files.
No database connection needed. Results are downloaded and bulk-loaded separately.

Usage:
    python cloud_embed.py /path/to/tsv_dir /path/to/output_dir

Output per TSV file:
    {basename}.jsonl  — one JSON line per article with chunks metadata
    {basename}.npy    — numpy array of all embeddings (N, 1024)

JSONL format per line:
    {
        "url": "...", "title": "...", "author": "...", "source": "...",
        "date_published": "...", "content": "...", "metadata": {...},
        "chunks": [{"chunk_index": 0, "chunk_text": "...", "embedding_offset": 0}, ...]
    }

The embedding_offset maps into the .npy array.
"""

import json
import glob
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimal inlined pipeline (no dependency on project config files)
# ---------------------------------------------------------------------------

# --- Ingestion ---

@dataclass
class Article:
    url: str
    headline: str
    article_body: str
    source_id: str
    author: Optional[str] = None
    date_published: Optional[str] = None
    publisher: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    raw_schema_json: str = ""
    is_valid: bool = True


def parse_tsv_line(line: str) -> Optional[Article]:
    line = line.strip()
    if not line:
        return None
    parts = line.split('\t', 1)
    if len(parts) != 2:
        return None
    url, json_ld_str = parts
    try:
        data = json.loads(json_ld_str)
    except json.JSONDecodeError:
        return None

    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace("www.", "")

    headline = data.get("headline", "") or data.get("name", "") or ""
    body = data.get("articleBody", "") or ""
    if not body or len(body) < 50:
        return None

    author = None
    author_field = data.get("author")
    if isinstance(author_field, dict):
        author = author_field.get("name", "")
    elif isinstance(author_field, list) and author_field:
        author = author_field[0].get("name", "") if isinstance(author_field[0], dict) else str(author_field[0])
    elif isinstance(author_field, str):
        author = author_field

    date_str = data.get("datePublished", "")

    return Article(
        url=url,
        headline=headline,
        article_body=body,
        source_id=domain,
        author=author,
        date_published=date_str,
        publisher=data.get("publisher", {}).get("name", "") if isinstance(data.get("publisher"), dict) else "",
        keywords=data.get("keywords", []) if isinstance(data.get("keywords"), list) else [],
        raw_schema_json=json_ld_str[:500],
    )


# --- Quality Gate (minimal) ---

def passes_quality(article: Article) -> bool:
    body = article.article_body
    if len(body) < 50:
        return False
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', body))
    if len(body) > 0 and chinese_chars / len(body) < 0.2:
        return False
    return True


# --- Chunking (length-based, 170 chars, sentence boundary) ---

SENTENCE_ENDINGS = re.compile(r'([。！？])')
TARGET_LENGTH = 170
MIN_LENGTH = 100
SHORT_THRESHOLD = 200
OVERLAP_CHARS = 30


@dataclass
class Chunk:
    chunk_index: int
    full_text: str
    embedding_text: str  # with overlap


def chunk_article(article: Article) -> list[Chunk]:
    text = article.article_body.strip()
    if not text:
        return []

    if len(text) < SHORT_THRESHOLD:
        return [Chunk(chunk_index=0, full_text=text, embedding_text=text)]

    # Split into sentences
    parts = SENTENCE_ENDINGS.split(text)
    sentences = []
    for i in range(0, len(parts) - 1, 2):
        sentences.append(parts[i] + parts[i + 1])
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1])

    if not sentences:
        return [Chunk(chunk_index=0, full_text=text, embedding_text=text)]

    # Group sentences into chunks
    chunks_raw = []
    current = []
    current_len = 0
    for sent in sentences:
        if current_len + len(sent) > TARGET_LENGTH and current_len >= MIN_LENGTH:
            chunks_raw.append("".join(current))
            current = [sent]
            current_len = len(sent)
        else:
            current.append(sent)
            current_len += len(sent)
    if current:
        chunks_raw.append("".join(current))

    # Merge last chunk if too short
    if len(chunks_raw) > 1 and len(chunks_raw[-1]) < MIN_LENGTH:
        chunks_raw[-2] += chunks_raw[-1]
        chunks_raw.pop()

    # Add overlap for embedding
    chunks = []
    for i, chunk_text in enumerate(chunks_raw):
        if i > 0 and OVERLAP_CHARS > 0:
            prev = chunks_raw[i - 1]
            overlap = prev[-OVERLAP_CHARS:]
            embedding_text = overlap + chunk_text
        else:
            embedding_text = chunk_text
        chunks.append(Chunk(chunk_index=i, full_text=chunk_text, embedding_text=embedding_text))

    return chunks


# --- Embedding ---

_model = None


def get_model():
    global _model
    if _model is not None:
        return _model
    logger.info("Loading Qwen3-Embedding-4B (INT8)...")
    t0 = time.time()
    from sentence_transformers import SentenceTransformer
    from transformers import BitsAndBytesConfig
    qconfig = BitsAndBytesConfig(load_in_8bit=True)
    _model = SentenceTransformer(
        "Qwen/Qwen3-Embedding-4B",
        model_kwargs={"quantization_config": qconfig},
        truncate_dim=1024,
    )
    logger.info(f"Model loaded in {time.time()-t0:.1f}s")
    return _model


EMBED_CHUNK_LIMIT = 5000  # max texts per encode() call to avoid VRAM OOM


def _free_vram():
    """Release GPU memory after encoding."""
    import gc
    import torch
    gc.collect()
    torch.cuda.empty_cache()


def embed_texts(texts: list[str], batch_size: int = 32) -> np.ndarray:
    if not texts:
        return np.empty((0, 1024), dtype=np.float32)
    model = get_model()

    if len(texts) <= EMBED_CHUNK_LIMIT:
        embs = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
        result = embs.astype(np.float32)
        del embs
        _free_vram()
        return result

    # Split into sub-batches to avoid VRAM OOM on large files
    all_embs = []
    total_batches = (len(texts) - 1) // EMBED_CHUNK_LIMIT + 1
    for i in range(0, len(texts), EMBED_CHUNK_LIMIT):
        sub = texts[i:i + EMBED_CHUNK_LIMIT]
        batch_num = i // EMBED_CHUNK_LIMIT + 1
        logger.info(f"    Sub-batch {batch_num}/{total_batches}: {len(sub)} texts")
        embs = model.encode(sub, batch_size=batch_size, show_progress_bar=False)
        all_embs.append(embs.astype(np.float32))
        del embs
        _free_vram()
    return np.vstack(all_embs)


# --- Checkpoint ---

def load_done_set(done_file: Path) -> set[str]:
    if not done_file.exists():
        return set()
    with open(done_file, 'r') as f:
        return {line.strip() for line in f if line.strip()}


# --- Main ---

def process_tsv(tsv_path: Path, output_dir: Path, batch_size: int = 32) -> dict:
    """Process one TSV: chunk + embed → JSONL + NPY."""
    basename = tsv_path.stem
    jsonl_path = output_dir / f"{basename}.jsonl"
    npy_path = output_dir / f"{basename}.npy"

    stats = {"success": 0, "failed": 0, "skipped": 0, "chunks": 0}

    all_articles_data = []
    all_embed_texts = []
    embed_offset = 0

    with open(tsv_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            article = parse_tsv_line(line)
            if article is None or not passes_quality(article):
                stats["skipped"] += 1
                continue

            chunks = chunk_article(article)
            if not chunks:
                stats["skipped"] += 1
                continue

            chunk_data = []
            for c in chunks:
                chunk_data.append({
                    "chunk_index": c.chunk_index,
                    "chunk_text": c.full_text,
                    "embedding_offset": embed_offset,
                })
                all_embed_texts.append(c.embedding_text)
                embed_offset += 1

            metadata = {
                "keywords": article.keywords,
                "publisher": article.publisher or "",
                "raw_schema_json": article.raw_schema_json,
            }

            all_articles_data.append({
                "url": article.url,
                "title": article.headline,
                "author": article.author or "",
                "source": article.source_id,
                "date_published": article.date_published or "",
                "content": article.article_body,
                "metadata": metadata,
                "chunks": chunk_data,
            })

            stats["success"] += 1
            stats["chunks"] += len(chunks)

    if not all_embed_texts:
        logger.info(f"  No texts to embed in {basename}")
        return stats

    # Embed in sub-batches if needed (avoids VRAM OOM on large files)
    logger.info(f"  Embedding {len(all_embed_texts)} chunks...")
    t0 = time.time()
    embeddings = embed_texts(all_embed_texts, batch_size=batch_size)
    logger.info(f"  Embedded in {time.time()-t0:.1f}s")

    # Save
    np.save(npy_path, embeddings)

    with open(jsonl_path, 'w', encoding='utf-8') as f:
        for article_data in all_articles_data:
            f.write(json.dumps(article_data, ensure_ascii=False) + "\n")

    return stats


def main():
    if len(sys.argv) < 3:
        print("Usage: python cloud_embed.py <tsv_dir> <output_dir>")
        sys.exit(1)

    tsv_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    done_file = output_dir / ".done"

    tsv_files = sorted(glob.glob(str(tsv_dir / "*.tsv")))
    done_set = load_done_set(done_file)

    remaining = [f for f in tsv_files if Path(f).name not in done_set]
    total = len(tsv_files)

    logger.info(f"=== Cloud Embedding ===")
    logger.info(f"Total: {total}, Done: {len(done_set)}, Remaining: {len(remaining)}")

    if not remaining:
        logger.info("All done!")
        return

    # Pre-load model
    get_model()

    grand = {"success": 0, "failed": 0, "skipped": 0, "chunks": 0}

    for i, tsv_file in enumerate(remaining):
        basename = Path(tsv_file).name
        logger.info(f"[{len(done_set)+i+1}/{total}] {basename}")

        t0 = time.time()
        try:
            stats = process_tsv(Path(tsv_file), output_dir, batch_size=32)
            elapsed = time.time() - t0
            logger.info(f"  OK: {stats['success']} articles, {stats['chunks']} chunks ({elapsed:.0f}s)")

            for k in grand:
                grand[k] += stats[k]

            with open(done_file, 'a') as f:
                f.write(basename + "\n")

        except Exception as e:
            logger.error(f"  ERROR: {e}")
            continue

    logger.info(f"=== Complete ===")
    logger.info(f"Total: {grand['success']} articles, {grand['chunks']} chunks")


if __name__ == "__main__":
    main()
