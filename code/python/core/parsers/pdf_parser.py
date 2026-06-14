# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
PDF file parser using PyPDF2.
"""

from typing import Dict, Any
from .base_parser import BaseParser
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("pdf_parser")

try:
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    logger.warning("pypdf not installed. PDF parsing will not be available.")


class PDFParser(BaseParser):
    """Parser for PDF files using PyPDF2."""

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a PDF file and extract text content.

        Args:
            file_path: Path to the PDF file

        Returns:
            Dictionary containing extracted text and metadata
        """
        if not PDF_AVAILABLE:
            raise ImportError("pypdf is not installed. Install it with: pip install pypdf")

        try:
            reader = PdfReader(file_path)
            num_pages = len(reader.pages)

            # Extract text from all pages
            text_parts = []
            page_metadata = []

            for page_num, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
                    page_metadata.append({
                        'page': page_num,
                        'char_count': len(page_text)
                    })

            full_text = '\n\n'.join(text_parts)

            metadata = {
                'num_pages': num_pages,
                'page_metadata': page_metadata,
                'total_chars': len(full_text)
            }

            logger.info(f"Parsed PDF: {num_pages} pages, {len(full_text)} characters")

            return {
                'text': full_text,
                'metadata': metadata
            }

        except Exception as e:
            logger.exception(f"Failed to parse PDF: {str(e)}")
            raise

    def supports_file_type(self, file_extension: str) -> bool:
        """Check if this parser supports PDF files."""
        return file_extension.lower() == '.pdf'
