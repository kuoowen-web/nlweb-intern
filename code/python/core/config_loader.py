"""Centralized configuration loader."""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from functools import lru_cache
import logging
import os

logger = logging.getLogger(__name__)


class ConfigLoader:
    """
    Centralized configuration loader with caching.

    Loads YAML configuration files from the config directory
    and provides typed accessors for common configurations.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize ConfigLoader.

        Args:
            config_dir: Path to configuration directory.
                        Defaults to 'config/' relative to project root.
        """
        if config_dir is None:
            # Find config directory relative to this file
            code_dir = Path(__file__).parent.parent.parent.parent
            config_dir = code_dir / "config"

        self.config_dir = Path(config_dir)

        if not self.config_dir.exists():
            logger.warning(f"Config directory not found: {self.config_dir}")

    @lru_cache(maxsize=20)
    def load(self, config_name: str) -> Dict[str, Any]:
        """
        Load configuration file with caching.

        Args:
            config_name: Name of config file (without .yaml extension)

        Returns:
            Configuration dictionary

        Raises:
            FileNotFoundError: If config file doesn't exist
        """
        config_path = self.config_dir / f"{config_name}.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")

        logger.debug(f"Loading config: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        return config or {}

    def reload(self, config_name: Optional[str] = None) -> None:
        """
        Clear cache and reload configuration(s).

        Args:
            config_name: Specific config to reload, or None for all
        """
        self.load.cache_clear()
        logger.info(f"Config cache cleared: {config_name or 'all'}")

    # Typed accessors for common configurations

    def get_mode_patterns(self) -> Dict[str, Any]:
        """Get mode detection patterns."""
        config = self.load("mode_detection")
        return config.get("mode_patterns", {})

    def get_default_mode(self) -> str:
        """Get default research mode."""
        config = self.load("mode_detection")
        return config.get("default_mode", "discovery")

    def get_critic_rules(self, mode: str) -> Dict[str, Any]:
        """
        Get critic compliance rules for a mode.

        Args:
            mode: Research mode (strict, discovery, monitor)

        Returns:
            Rules dictionary for the mode
        """
        config = self.load("critic_rules")
        rules = config.get("mode_compliance_rules", {})
        return rules.get(mode, rules.get("discovery", {}))

    def get_source_tier_config(self, mode: str) -> Dict[str, Any]:
        """
        Get source tier filtering config for a mode.

        Args:
            mode: Research mode

        Returns:
            Tier configuration for the mode
        """
        config = self.load("source_filtering")
        tier_config = config.get("source_tier_config", {})
        return tier_config.get(mode, tier_config.get("discovery", {}))

    def get_tier_definitions(self) -> Dict[int, Dict[str, Any]]:
        """Get source tier definitions."""
        config = self.load("source_filtering")
        return config.get("tier_definitions", {})

    def get(self, config_name: str, key: str, default: Any = None) -> Any:
        """
        Get a specific key from a config file.

        Args:
            config_name: Config file name
            key: Dot-separated key path (e.g., "reasoning.max_iterations")
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        try:
            config = self.load(config_name)
        except FileNotFoundError:
            return default

        # Navigate dot-separated path
        keys = key.split(".")
        value = config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default

            if value is None:
                return default

        return value


# Global instance
config_loader = ConfigLoader()


# Convenience functions
def get_mode_patterns() -> Dict[str, Any]:
    """Get mode detection patterns."""
    return config_loader.get_mode_patterns()


def get_critic_rules(mode: str) -> Dict[str, Any]:
    """Get critic rules for mode."""
    return config_loader.get_critic_rules(mode)


def get_source_tier_config(mode: str) -> Dict[str, Any]:
    """Get source tier config for mode."""
    return config_loader.get_source_tier_config(mode)
