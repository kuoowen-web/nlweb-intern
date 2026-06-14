"""
Ingestion Engine for M0 Indexing Module.

Parses TSV files (url<TAB>JSON-LD) into Canonical Data Model.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse


@dataclass
class CanonicalDataModel:
    """Canonical representation of a news article."""
    url: str
    headline: str
    article_body: str
    source_id: str  # Domain extracted from URL
    author: Optional[str] = None
    date_published: Optional[datetime] = None
    publisher: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    raw_schema_json: str = ""
    detected_language: str = "zh"  # Default to Chinese
    is_valid: bool = True
    validation_errors: list[str] = field(default_factory=list)


class IngestionEngine:
    """Parses TSV files into CDM objects."""

    def parse_tsv_line(self, line: str) -> Optional[CanonicalDataModel]:
        """
        Parse a single TSV line into CDM.

        Args:
            line: TSV line in format "url<TAB>json-ld"

        Returns:
            CanonicalDataModel or None if parsing fails
        """
        line = line.strip()
        if not line:
            return None

        parts = line.split('\t', 1)
        if len(parts) != 2:
            return self._create_invalid_cdm(
                url=parts[0] if parts else "",
                error="TSV 格式錯誤：缺少 JSON-LD"
            )

        url, json_ld_str = parts

        try:
            schema_data = json.loads(json_ld_str)
        except json.JSONDecodeError as e:
            return self._create_invalid_cdm(url=url, error=f"JSON 解析失敗: {e}")

        return self._parse_schema(url, schema_data, json_ld_str)

    def parse_tsv_file(self, tsv_path: Path) -> Iterator[CanonicalDataModel]:
        """
        Parse entire TSV file.

        Args:
            tsv_path: Path to TSV file

        Yields:
            CanonicalDataModel for each line
        """
        with open(tsv_path, 'r', encoding='utf-8') as f:
            for line in f:
                cdm = self.parse_tsv_line(line)
                if cdm:
                    yield cdm

    def _parse_schema(
        self,
        url: str,
        schema_data: dict,
        raw_json: str
    ) -> CanonicalDataModel:
        """Parse JSON-LD schema into CDM."""
        errors = []

        # Extract headline
        headline = schema_data.get('headline', '') or schema_data.get('name', '')
        if not headline:
            errors.append("缺少 headline")

        # Extract article body
        article_body = schema_data.get('articleBody', '') or schema_data.get('text', '')
        if not article_body:
            errors.append("缺少 articleBody")

        # Extract source_id from URL
        source_id = self._extract_source_id(url)

        # Extract author
        author = self._extract_author(schema_data)

        # Extract date_published
        date_published = self._parse_date(schema_data.get('datePublished'))

        # Extract publisher
        publisher = self._extract_publisher(schema_data)

        # Extract keywords
        keywords = self._extract_keywords(schema_data)

        return CanonicalDataModel(
            url=url,
            headline=headline,
            article_body=article_body,
            source_id=source_id,
            author=author,
            date_published=date_published,
            publisher=publisher,
            keywords=keywords,
            raw_schema_json=raw_json,
            is_valid=len(errors) == 0,
            validation_errors=errors
        )

    def _extract_source_id(self, url: str) -> str:
        """Extract domain from URL as source_id."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Remove www. prefix
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except Exception:
            return ""

    def _extract_author(self, schema_data: dict) -> Optional[str]:
        """Extract author from schema."""
        author = schema_data.get('author')
        if not author:
            return None

        if isinstance(author, str):
            return author
        if isinstance(author, dict):
            return author.get('name')
        if isinstance(author, list) and author:
            first = author[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get('name')
        return None

    def _extract_publisher(self, schema_data: dict) -> Optional[str]:
        """Extract publisher from schema."""
        publisher = schema_data.get('publisher')
        if not publisher:
            return None

        if isinstance(publisher, str):
            return publisher
        if isinstance(publisher, dict):
            return publisher.get('name')
        return None

    def _extract_keywords(self, schema_data: dict) -> list[str]:
        """Extract keywords from schema."""
        keywords = schema_data.get('keywords', [])
        if isinstance(keywords, str):
            # Split by comma if string
            return [k.strip() for k in keywords.split(',') if k.strip()]
        if isinstance(keywords, list):
            return [str(k) for k in keywords if k]
        return []

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO date string to datetime."""
        if not date_str:
            return None

        # Remove timezone suffix like +08:00 for simpler parsing
        clean_date = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str).replace('Z', '')

        # Try common ISO formats
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(clean_date, fmt)
            except ValueError:
                continue

        return None

    def _create_invalid_cdm(self, url: str, error: str) -> CanonicalDataModel:
        """Create an invalid CDM with error message."""
        return CanonicalDataModel(
            url=url,
            headline="",
            article_body="",
            source_id=self._extract_source_id(url),
            is_valid=False,
            validation_errors=[error]
        )
