"""
Source Manager for M0 Indexing Module.

Manages source tier classification for news sources.
"""

from enum import IntEnum
from pathlib import Path
from typing import Optional

import yaml


class SourceTier(IntEnum):
    """Source credibility tiers."""
    AUTHORITATIVE = 1  # 官方、通訊社
    VERIFIED = 2       # 主流媒體
    STANDARD = 3       # 一般新聞（預設）
    AGGREGATOR = 4     # 聚合站


class SourceManager:
    """Manages source tier mappings from config."""

    DEFAULT_TIER = SourceTier.STANDARD

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize SourceManager.

        Args:
            config_path: Path to config_indexing.yaml.
                        If None, uses default location.
        """
        if config_path is None:
            # Default: config/config_indexing.yaml relative to project root
            config_path = Path(__file__).parents[3] / "config" / "config_indexing.yaml"

        self._mappings: dict[str, int] = {}
        self._load_config(config_path)

    def _load_config(self, config_path: Path) -> None:
        """Load source mappings from config file."""
        if not config_path.exists():
            # Config not found - use empty mappings, default tier applies
            return

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        self._mappings = config.get('source_mappings', {})

    def get_tier(self, source_id: str) -> SourceTier:
        """
        Get tier for a source.

        Args:
            source_id: Domain or source identifier (e.g., 'udn.com')

        Returns:
            SourceTier for the source, defaults to STANDARD if not mapped.
        """
        # Normalize: lowercase, strip whitespace
        normalized = source_id.lower().strip()

        tier_value = self._mappings.get(normalized)

        if tier_value is None:
            return self.DEFAULT_TIER

        try:
            return SourceTier(tier_value)
        except ValueError:
            # Invalid tier value in config
            return self.DEFAULT_TIER

    def get_tier_label(self, source_id: str) -> str:
        """
        Get human-readable tier label for a source.

        Args:
            source_id: Domain or source identifier

        Returns:
            Tier label string (e.g., 'authoritative', 'verified')
        """
        tier = self.get_tier(source_id)
        return tier.name.lower()
