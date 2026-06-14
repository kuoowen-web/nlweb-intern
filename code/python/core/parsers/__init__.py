# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
File parsers for extracting text from various document formats.
"""

from .parser_factory import get_parser, ParserFactory

__all__ = ['get_parser', 'ParserFactory']
