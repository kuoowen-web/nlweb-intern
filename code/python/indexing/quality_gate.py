"""
Quality Gate for M0 Indexing Module.

Validates articles before chunking and storage.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from .ingestion_engine import CanonicalDataModel


class QualityStatus(Enum):
    """Result of quality gate check."""
    PASSED = "passed"
    BUFFERED = "buffered"  # Failed but saved for review
    SKIPPED = "skipped"    # Duplicate, not saved


@dataclass
class QualityResult:
    """Result of quality gate validation."""
    status: QualityStatus
    cdm: CanonicalDataModel
    failure_reasons: list[str]

    @property
    def passed(self) -> bool:
        return self.status == QualityStatus.PASSED


class QualityGate:
    """Validates articles against quality criteria."""

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize QualityGate.

        Args:
            config_path: Path to config_indexing.yaml.
                        If None, uses default location.
        """
        if config_path is None:
            config_path = Path(__file__).parents[3] / "config" / "config_indexing.yaml"

        self._load_config(config_path)

    def _load_config(self, config_path: Path) -> None:
        """Load quality gate config."""
        # Defaults
        self.min_body_length = 50
        self.min_chinese_ratio = 0.2
        self.max_html_ratio = 0.3

        if not config_path.exists():
            return

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        qg_config = config.get('quality_gate', {})
        self.min_body_length = qg_config.get('min_body_length', self.min_body_length)
        self.min_chinese_ratio = qg_config.get('min_chinese_ratio', self.min_chinese_ratio)
        self.max_html_ratio = qg_config.get('max_html_ratio', self.max_html_ratio)

    def validate(self, cdm: CanonicalDataModel) -> QualityResult:
        """
        Validate a CDM against quality criteria.

        Args:
            cdm: CanonicalDataModel to validate

        Returns:
            QualityResult with status and failure reasons
        """
        failures = []

        # Check 1: CDM parsing was valid
        if not cdm.is_valid:
            failures.extend(cdm.validation_errors)

        # Check 2: Headline exists
        if not cdm.headline or not cdm.headline.strip():
            failures.append("缺少標題")

        # Check 3: Article body length
        if len(cdm.article_body) < self.min_body_length:
            failures.append(f"內容長度不足（{len(cdm.article_body)} < {self.min_body_length}）")

        # Check 4: Content quality (HTML ratio + Chinese ratio)
        if cdm.article_body:
            content_valid, content_reason = self._check_content_quality(cdm.article_body)
            if not content_valid:
                failures.append(content_reason)

        if failures:
            return QualityResult(
                status=QualityStatus.BUFFERED,
                cdm=cdm,
                failure_reasons=failures
            )

        return QualityResult(
            status=QualityStatus.PASSED,
            cdm=cdm,
            failure_reasons=[]
        )

    def _check_content_quality(self, article_body: str) -> tuple[bool, str]:
        """
        Check if content is valid article (not HTML residue, script, ads).

        Returns:
            (is_valid, reason)
        """
        if not article_body:
            return False, "內容為空"

        # 1. HTML tag ratio check
        html_pattern = r'<[^>]+>'
        html_matches = re.findall(html_pattern, article_body)
        html_chars = sum(len(m) for m in html_matches)
        html_ratio = html_chars / len(article_body)
        if html_ratio > self.max_html_ratio:
            return False, f"HTML 標籤比例過高 ({html_ratio:.1%} > {self.max_html_ratio:.0%})"

        # 2. Script/Style content check
        # Only flag clear JavaScript patterns, avoid false positives on normal text
        script_patterns = [
            r'function\s+\w+\s*\(',           # function declarations
            r'function\s*\(\s*\)\s*\{',       # anonymous functions
            r'var\s+\w+\s*=\s*[\[\{]',        # var with array/object
            r'const\s+\w+\s*=\s*[\[\{]',      # const with array/object
            r'let\s+\w+\s*=\s*[\[\{]',        # let with array/object
            r'document\.\w+',                  # DOM access
            r'window\.\w+',                    # window access
            r'=>\s*\{',                        # arrow functions
        ]
        for pattern in script_patterns:
            if re.search(pattern, article_body):
                return False, "疑似包含 script 內容"

        # 3. Chinese character ratio check (also serves as language check)
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', article_body))
        chinese_ratio = chinese_chars / len(article_body)
        if chinese_ratio < self.min_chinese_ratio:
            return False, f"中文字比例過低 ({chinese_ratio:.1%} < {self.min_chinese_ratio:.0%})"

        return True, ""

    def check_duplicate(self, url: str, existing_urls: set[str]) -> bool:
        """Check if URL already exists in the set."""
        return url in existing_urls
