"""
Chunking Engine for M0 Indexing Module.

Length-based chunking strategy validated by POC (2026-01-28):
- Target: 170 chars/chunk
- Split at sentence boundaries (。！？)
- Distinctiveness ~0.56 (ideal range 0.4-0.6)
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .ingestion_engine import CanonicalDataModel


@dataclass
class Chunk:
    """A chunk of article text."""
    chunk_id: str              # "{article_url}::chunk::{idx}"
    article_url: str
    chunk_index: int
    sentences: list[str]
    full_text: str             # original text without overlap
    summary: str               # headline + representative sentences
    char_start: int
    char_end: int
    embedding_text: str = ""   # text with overlap for embedding (set by add_overlap)


def make_chunk_id(article_url: str, chunk_index: int) -> str:
    """Generate chunk ID with ::chunk:: separator to avoid URL # conflicts."""
    return f"{article_url}::chunk::{chunk_index}"


def parse_chunk_id(chunk_id: str) -> tuple[str, int]:
    """Parse chunk ID, returns (article_url, chunk_index)."""
    parts = chunk_id.rsplit("::chunk::", 1)
    return parts[0], int(parts[1])


class ChunkingEngine:
    """Length-based chunking engine."""

    # Sentence-ending punctuation for Chinese
    SENTENCE_ENDINGS = re.compile(r'([。！？])')

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize ChunkingEngine.

        Args:
            config_path: Path to config_indexing.yaml.
                        If None, uses default location.
        """
        if config_path is None:
            config_path = Path(__file__).parents[3] / "config" / "config_indexing.yaml"

        self._load_config(config_path)

    def _load_config(self, config_path: Path) -> None:
        """Load chunking config."""
        # Defaults from POC validation
        self.target_length = 170
        self.min_length = 100
        self.short_article_threshold = 200
        self.summary_max_length = 400
        self.extractive_sentences = 3
        self.overlap_chars = 30  # overlap for embedding optimization

        if not config_path.exists():
            return

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        chunk_config = config.get('chunking', {})
        self.target_length = chunk_config.get('target_length', self.target_length)
        self.min_length = chunk_config.get('min_length', self.min_length)
        self.short_article_threshold = chunk_config.get('short_article_threshold', self.short_article_threshold)
        self.summary_max_length = chunk_config.get('summary_max_length', self.summary_max_length)
        self.extractive_sentences = chunk_config.get('extractive_summary_sentences', self.extractive_sentences)
        self.overlap_chars = chunk_config.get('overlap_chars', self.overlap_chars)

    def chunk_article(self, cdm: CanonicalDataModel, add_overlap: bool = True) -> list[Chunk]:
        """
        Chunk an article using length-based strategy.

        Args:
            cdm: CanonicalDataModel to chunk
            add_overlap: Whether to add overlap text for embedding (default True)

        Returns:
            List of Chunk objects
        """
        text = cdm.article_body.strip()

        # Short article: entire text as one chunk
        if len(text) < self.short_article_threshold:
            chunks = [self._create_chunk(
                cdm=cdm,
                sentences=[text],
                chunk_index=0,
                char_start=0,
                char_end=len(text)
            )]
            if add_overlap:
                self._add_overlap(chunks)
            return chunks

        # Split into sentences
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        # Group sentences into chunks
        chunks = []
        current_sentences = []
        current_length = 0
        char_position = 0

        for sentence in sentences:
            sentence_len = len(sentence)

            # If adding this sentence exceeds target and we have content
            if current_length + sentence_len > self.target_length and current_sentences:
                # Create chunk from accumulated sentences
                chunk_text = ''.join(current_sentences)
                chunks.append(self._create_chunk(
                    cdm=cdm,
                    sentences=current_sentences.copy(),
                    chunk_index=len(chunks),
                    char_start=char_position - current_length,
                    char_end=char_position
                ))
                current_sentences = []
                current_length = 0

            current_sentences.append(sentence)
            current_length += sentence_len
            char_position += sentence_len

        # Handle remaining sentences
        if current_sentences:
            # If too short, merge with previous chunk
            if current_length < self.min_length and chunks:
                last_chunk = chunks[-1]
                merged_sentences = last_chunk.sentences + current_sentences
                chunks[-1] = self._create_chunk(
                    cdm=cdm,
                    sentences=merged_sentences,
                    chunk_index=last_chunk.chunk_index,
                    char_start=last_chunk.char_start,
                    char_end=char_position
                )
            else:
                chunks.append(self._create_chunk(
                    cdm=cdm,
                    sentences=current_sentences,
                    chunk_index=len(chunks),
                    char_start=char_position - current_length,
                    char_end=char_position
                ))

        # Add overlap for embedding optimization
        if add_overlap:
            self._add_overlap(chunks)

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """
        Split text into sentences at Chinese punctuation boundaries.

        Keeps punctuation attached to the sentence.
        """
        # Split but keep delimiters
        parts = self.SENTENCE_ENDINGS.split(text)

        sentences = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and self.SENTENCE_ENDINGS.match(parts[i + 1]):
                # Combine text with its ending punctuation
                sentences.append(parts[i] + parts[i + 1])
                i += 2
            else:
                # No punctuation follows, add as-is if not empty
                if parts[i].strip():
                    sentences.append(parts[i])
                i += 1

        return sentences

    def _create_chunk(
        self,
        cdm: CanonicalDataModel,
        sentences: list[str],
        chunk_index: int,
        char_start: int,
        char_end: int
    ) -> Chunk:
        """Create a Chunk with generated summary."""
        full_text = ''.join(sentences)
        summary = self._generate_summary(cdm.headline, sentences)

        return Chunk(
            chunk_id=make_chunk_id(cdm.url, chunk_index),
            article_url=cdm.url,
            chunk_index=chunk_index,
            sentences=sentences,
            full_text=full_text,
            summary=summary,
            char_start=char_start,
            char_end=char_end
        )

    def _generate_summary(self, headline: str, sentences: list[str]) -> str:
        """
        Generate extractive summary for a chunk.

        Strategy: Select first, middle, and last sentences (news inverted pyramid),
        then prepend headline.
        """
        # Select representative sentences
        if len(sentences) <= 2:
            selected = sentences
        elif self.extractive_sentences <= 2:
            selected = [sentences[0], sentences[-1]]
        else:
            mid_idx = len(sentences) // 2
            selected = [sentences[0], sentences[mid_idx], sentences[-1]]

        content = ''.join(selected)
        summary = f"{headline}。{content}" if headline else content

        # Truncate if too long
        if len(summary) > self.summary_max_length:
            summary = summary[:self.summary_max_length - 3] + "..."

        return summary

    def _add_overlap(self, chunks: list[Chunk]) -> None:
        """
        Add overlap text to chunks for better embedding quality.

        The overlap text is stored in embedding_text field, which includes:
        - Previous chunk's last N characters (prefix)
        - Current chunk's full text
        - Next chunk's first N characters (suffix)

        Edge cases:
        - First chunk: no prefix
        - Last chunk: no suffix
        - Single chunk: no overlap (embedding_text = full_text)

        Note: char_start/char_end remain unchanged (original positions).
        """
        if not chunks:
            return

        overlap = self.overlap_chars

        for i, chunk in enumerate(chunks):
            prefix = ""
            suffix = ""

            # Not first chunk: get last N chars from previous chunk
            if i > 0:
                prev_text = chunks[i - 1].full_text
                if len(prev_text) >= overlap:
                    prefix = prev_text[-overlap:]
                else:
                    prefix = prev_text

            # Not last chunk: get first N chars from next chunk
            if i < len(chunks) - 1:
                next_text = chunks[i + 1].full_text
                if len(next_text) >= overlap:
                    suffix = next_text[:overlap]
                else:
                    suffix = next_text

            # Combine: prefix + original text + suffix
            chunk.embedding_text = prefix + chunk.full_text + suffix
