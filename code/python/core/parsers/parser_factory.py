# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Parser factory for selecting the appropriate parser based on file type.
"""

from typing import Optional, List, Type
from pathlib import Path
from .base_parser import BaseParser
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("parser_factory")


class ParserFactory:
    """Factory class for creating file parsers."""

    # Register parser classes (not instances) - lazy instantiation
    _parser_classes: List[Type[BaseParser]] = []
    _initialized = False

    @classmethod
    def _ensure_initialized(cls):
        """Lazily initialize parser classes to avoid import-time failures."""
        if cls._initialized:
            return

        # Import parsers here to catch import errors gracefully
        try:
            from .pdf_parser import PDFParser
            cls._parser_classes.append(PDFParser)
        except ImportError as e:
            logger.warning(f"PDF parser unavailable: {e}")

        try:
            from .docx_parser import DOCXParser
            cls._parser_classes.append(DOCXParser)
        except ImportError as e:
            logger.warning(f"DOCX parser unavailable: {e}")

        try:
            from .text_parser import TextParser
            cls._parser_classes.append(TextParser)
        except ImportError as e:
            logger.warning(f"Text parser unavailable: {e}")

        cls._initialized = True

    @classmethod
    def get_parser(cls, file_path: str) -> Optional[BaseParser]:
        """
        Get the appropriate parser for a given file.

        Args:
            file_path: Path to the file

        Returns:
            Parser instance if found, None otherwise
        """
        cls._ensure_initialized()
        file_extension = Path(file_path).suffix.lower()

        for parser_class in cls._parser_classes:
            # Create instance on demand
            parser = parser_class()
            if parser.supports_file_type(file_extension):
                logger.debug(f"Selected parser for {file_extension}: {parser_class.__name__}")
                return parser

        logger.warning(f"No parser found for file type: {file_extension}")
        return None

    @classmethod
    def parse_file(cls, file_path: str) -> dict:
        """
        Parse a file using the appropriate parser.

        Args:
            file_path: Path to the file

        Returns:
            Parsed content dictionary

        Raises:
            ValueError: If no parser found for file type
        """
        parser = cls.get_parser(file_path)
        if parser is None:
            file_extension = Path(file_path).suffix
            raise ValueError(f"Unsupported file type: {file_extension}")

        return parser.parse(file_path)


def get_parser(file_path: str) -> Optional[BaseParser]:
    """
    Convenience function to get a parser for a file.

    Args:
        file_path: Path to the file

    Returns:
        Parser instance if found, None otherwise
    """
    return ParserFactory.get_parser(file_path)
