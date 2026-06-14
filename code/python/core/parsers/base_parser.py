# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Base class for file parsers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseParser(ABC):
    """Abstract base class for file parsers."""

    @abstractmethod
    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a file and extract text content.

        Args:
            file_path: Path to the file to parse

        Returns:
            Dictionary containing:
                - 'text': Extracted text content
                - 'metadata': Additional metadata (page count, etc.)
        """
        pass

    @abstractmethod
    def supports_file_type(self, file_extension: str) -> bool:
        """
        Check if this parser supports the given file type.

        Args:
            file_extension: File extension (e.g., '.pdf', '.docx')

        Returns:
            True if supported, False otherwise
        """
        pass
