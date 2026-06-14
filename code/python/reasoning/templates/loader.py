"""Template loader for writer output formatting."""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class TemplateLoader:
    """
    Loader for writer output templates.

    Supports multiple languages and research modes.
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize template loader.

        Args:
            config_path: Path to templates YAML file.
                         Defaults to config/writer_templates.yaml
        """
        if config_path is None:
            code_dir = Path(__file__).parent.parent.parent.parent
            config_path = code_dir / "config" / "writer_templates.yaml"

        self.config_path = config_path
        self._templates: Optional[Dict[str, Any]] = None

    @property
    def templates(self) -> Dict[str, Any]:
        """Lazy load templates."""
        if self._templates is None:
            self._load_templates()
        return self._templates

    def _load_templates(self) -> None:
        """Load templates from YAML file."""
        if not self.config_path.exists():
            logger.warning(f"Template config not found: {self.config_path}")
            self._templates = {}
            return

        with open(self.config_path, 'r', encoding='utf-8') as f:
            self._templates = yaml.safe_load(f) or {}

        logger.debug(f"Loaded templates from {self.config_path}")

    def reload(self) -> None:
        """Reload templates from file."""
        self._templates = None
        self._load_templates()

    def get_template(
        self,
        mode: str,
        lang: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get template for mode and language.

        Args:
            mode: Research mode (strict, discovery, monitor)
            lang: Language code (zh-TW, en-US). Uses default if not specified.

        Returns:
            Template dictionary with structure and components
        """
        if lang is None:
            lang = self.templates.get("default_language", "zh-TW")

        mode_templates = self.templates.get("templates", {}).get(mode, {})

        # Try requested language
        if lang in mode_templates:
            return mode_templates[lang]

        # Fall back to default language
        default_lang = self.templates.get("default_language", "zh-TW")
        if default_lang in mode_templates:
            logger.warning(f"Language '{lang}' not found, using '{default_lang}'")
            return mode_templates[default_lang]

        # Fall back to discovery mode
        logger.warning(f"Mode '{mode}' not found, using 'discovery'")
        return self.templates.get("templates", {}).get("discovery", {}).get(
            default_lang, {}
        )

    def get_structure(
        self,
        mode: str,
        lang: Optional[str] = None,
    ) -> str:
        """Get the main structure template."""
        template = self.get_template(mode, lang)
        return template.get("structure", "")

    def get_verdict_template(
        self,
        mode: str,
        verdict_type: str,
        lang: Optional[str] = None,
    ) -> str:
        """
        Get verdict template for strict mode.

        Args:
            mode: Research mode
            verdict_type: Type of verdict (verified, false, etc.)
            lang: Language code

        Returns:
            Verdict text template
        """
        template = self.get_template(mode, lang)
        verdicts = template.get("verdict_templates", {})
        return verdicts.get(verdict_type, f"[{verdict_type}]")

    def get_confidence_description(
        self,
        confidence: float,
        lang: Optional[str] = None,
    ) -> str:
        """
        Get confidence level description.

        Args:
            confidence: Confidence score (0.0-1.0)
            lang: Language code

        Returns:
            Description text for confidence level
        """
        if lang is None:
            lang = self.templates.get("default_language", "zh-TW")

        levels = self.templates.get("confidence_levels", {}).get(lang, {})

        if confidence >= 0.85:
            return levels.get("high", "High confidence")
        elif confidence >= 0.60:
            return levels.get("medium", "Medium confidence")
        else:
            return levels.get("low", "Low confidence")

    def format_citation(
        self,
        citation_id: int,
        title: str = "",
        source: str = "",
        date: str = "",
        url: str = "",
        format_type: str = "reference",
    ) -> str:
        """
        Format a citation according to template.

        Args:
            citation_id: Citation number
            title: Source title
            source: Source name
            date: Publication date
            url: Source URL
            format_type: Format type (inline, reference, reference_with_url)

        Returns:
            Formatted citation string
        """
        formats = self.templates.get("citation_formats", {})
        template = formats.get(format_type, "[{id}]")

        return template.format(
            id=citation_id,
            title=title,
            source=source,
            date=date,
            url=url,
        )


# Global instance
template_loader = TemplateLoader()


def get_template(mode: str, lang: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function to get template."""
    return template_loader.get_template(mode, lang)


def get_structure(mode: str, lang: Optional[str] = None) -> str:
    """Convenience function to get structure template."""
    return template_loader.get_structure(mode, lang)
