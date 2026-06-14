# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Plain text file parser (TXT, MD).
"""

from typing import Dict, Any
from .base_parser import BaseParser
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("text_parser")


class TextParser(BaseParser):
    """Parser for plain text files (.txt, .md)."""

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a text file and extract content.

        Args:
            file_path: Path to the text file

        Returns:
            Dictionary containing extracted text and metadata
        """
        try:
            # Try different encodings
            encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
            text = None
            used_encoding = None

            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        text = f.read()
                    used_encoding = encoding
                    break
                except UnicodeDecodeError:
                    continue

            if text is None:
                raise ValueError(f"Could not decode file with any of: {encodings}")

            # Count lines
            lines = text.split('\n')
            non_empty_lines = [line for line in lines if line.strip()]

            metadata = {
                'num_lines': len(lines),
                'num_non_empty_lines': len(non_empty_lines),
                'total_chars': len(text),
                'encoding': used_encoding
            }

            logger.info(f"Parsed text file: {len(lines)} lines, {len(text)} characters (encoding: {used_encoding})")

            return {
                'text': text,
                'metadata': metadata
            }

        except Exception as e:
            logger.exception(f"Failed to parse text file: {str(e)}")
            raise

    def supports_file_type(self, file_extension: str) -> bool:
        """Check if this parser supports text files."""
        return file_extension.lower() in ['.txt', '.md', '.markdown']
