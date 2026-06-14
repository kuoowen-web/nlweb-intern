"""
JSON Repair Utilities - Fix malformed, truncated, or incomplete JSON responses.

This module provides utilities to repair common JSON parsing issues from LLM responses,
including:
- Truncated JSON (missing closing braces)
- Mixed content (markdown + JSON)
- Incomplete string values
- Nested structure repair
"""

import json
import re
from typing import Dict, Any, Optional, List
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("json_repair")


def extract_json_from_text(text: str) -> Optional[str]:
    """
    Extract JSON object from text that may contain markdown or other content.

    Args:
        text: Raw text that may contain JSON

    Returns:
        Extracted JSON string or None if not found
    """
    # Remove markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()

    # Find the first '{' and last '}'
    start = text.find('{')
    if start == -1:
        return None

    # Try to find matching closing brace
    end = text.rfind('}')
    if end == -1 or end < start:
        # No closing brace found - will attempt repair later
        return text[start:]

    return text[start:end+1]


def count_braces(text: str) -> Dict[str, int]:
    """
    Count opening and closing braces/brackets in text.

    Args:
        text: JSON string to analyze

    Returns:
        Dictionary with counts of braces and brackets
    """
    return {
        'open_brace': text.count('{'),
        'close_brace': text.count('}'),
        'open_bracket': text.count('['),
        'close_bracket': text.count(']'),
        'double_quote': text.count('"')
    }


def repair_truncated_json(json_str: str) -> str:
    """
    Attempt to repair truncated JSON by closing unclosed structures.

    Strategy:
    1. Count opening vs closing braces/brackets
    2. Add missing closing characters
    3. Handle incomplete string values

    Args:
        json_str: Potentially truncated JSON string

    Returns:
        Repaired JSON string
    """
    if not json_str.strip():
        return "{}"

    # Remove trailing incomplete content after last complete structure
    json_str = json_str.rstrip()

    # Handle incomplete string values (odd number of quotes)
    quote_count = json_str.count('"')
    if quote_count % 2 == 1:
        # Odd number of quotes - close the string
        # First, check if the last quote is part of an incomplete value
        last_quote_idx = json_str.rfind('"')
        if last_quote_idx != -1:
            # Check if this quote is followed by a comma or brace
            remaining = json_str[last_quote_idx+1:].strip()
            if remaining and remaining[0] not in [',', '}', ']']:
                # Incomplete string value - close it
                json_str += '"'
                logger.debug("Repaired incomplete string value")

    # Count braces and brackets
    counts = count_braces(json_str)

    # Add missing closing brackets
    if counts['open_bracket'] > counts['close_bracket']:
        missing = counts['open_bracket'] - counts['close_bracket']
        json_str += ']' * missing
        logger.debug(f"Added {missing} closing bracket(s)")

    # Add missing closing braces
    if counts['open_brace'] > counts['close_brace']:
        missing = counts['open_brace'] - counts['close_brace']
        json_str += '}' * missing
        logger.debug(f"Added {missing} closing brace(s)")

    return json_str


def parse_json_with_repair(text: str, max_attempts: int = 3) -> Optional[Dict[str, Any]]:
    """
    Attempt to parse JSON with progressive repair strategies.

    Strategy cascade:
    1. Direct parse (text as-is)
    2. Extract JSON from markdown/mixed content
    3. Repair truncated JSON
    4. Remove trailing incomplete fields

    Args:
        text: Raw text containing JSON
        max_attempts: Maximum repair attempts

    Returns:
        Parsed dictionary or None if all attempts fail
    """
    if not text or not text.strip():
        logger.warning("Empty text provided to JSON parser")
        return None

    # Attempt 1: Direct parse
    try:
        result = json.loads(text)
        logger.debug("JSON parsed successfully without repair")
        return result
    except json.JSONDecodeError as e:
        logger.debug(f"Direct parse failed: {e}")

    # Attempt 2: Extract JSON from mixed content
    extracted = extract_json_from_text(text)
    if extracted:
        try:
            result = json.loads(extracted)
            logger.debug("JSON parsed after extraction from mixed content")
            return result
        except json.JSONDecodeError as e:
            logger.debug(f"Extraction parse failed: {e}")
            # Continue with repair
            text = extracted

    # Attempt 3: Repair truncated JSON
    repaired = repair_truncated_json(text)
    try:
        result = json.loads(repaired)
        logger.info("JSON parsed successfully after repair")
        return result
    except json.JSONDecodeError as e:
        logger.debug(f"Repair parse failed: {e}")

    # Attempt 4: Progressive truncation (remove incomplete fields)
    if repaired.strip().startswith('{'):
        # Try removing content after last complete comma
        last_comma_idx = repaired.rfind(',')
        if last_comma_idx > 0:
            truncated = repaired[:last_comma_idx] + '}'
            try:
                result = json.loads(truncated)
                logger.warning("JSON parsed after aggressive truncation - some fields may be missing")
                return result
            except json.JSONDecodeError:
                pass

        # Last resort: try to salvage at least the first complete field
        # Look for the first complete "key": "value" or "key": {...} pattern
        match = re.search(r'\{\s*"([^"]+)"\s*:\s*(\{[^}]*\}|"[^"]*"|\[[^\]]*\]|[^,}]+)', repaired)
        if match:
            salvaged = '{' + match.group(0)[1:] + '}'
            try:
                result = json.loads(salvaged)
                logger.warning("JSON parsed from salvaged partial content - most fields missing")
                return result
            except json.JSONDecodeError:
                pass

    logger.error(f"All JSON repair attempts failed. Text preview: {text[:200]}...")
    return None


def validate_required_fields(parsed_json: Dict[str, Any], required_fields: List[str]) -> bool:
    """
    Validate that required fields are present in parsed JSON.

    Args:
        parsed_json: Parsed JSON dictionary
        required_fields: List of required field names

    Returns:
        True if all required fields present, False otherwise
    """
    if not parsed_json:
        return False

    missing = [field for field in required_fields if field not in parsed_json]
    if missing:
        logger.warning(f"Parsed JSON missing required fields: {missing}")
        return False

    return True


def safe_parse_llm_json(
    content: str,
    required_fields: Optional[List[str]] = None,
    default_on_failure: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Safely parse JSON from LLM response with comprehensive error handling.

    This is the main entry point for parsing LLM JSON responses.

    Args:
        content: Raw LLM response content
        required_fields: Optional list of required field names for validation
        default_on_failure: Default dict to return if all parsing fails

    Returns:
        Parsed dictionary or default_on_failure (empty dict if not specified)
    """
    if default_on_failure is None:
        default_on_failure = {}

    # Attempt parsing with repair
    parsed = parse_json_with_repair(content)

    if parsed is None:
        logger.error("Failed to parse JSON from LLM response")
        return default_on_failure

    # Validate required fields if specified
    if required_fields and not validate_required_fields(parsed, required_fields):
        logger.error(f"Parsed JSON missing required fields: {required_fields}")
        return default_on_failure

    return parsed


# Convenience function for extracting specific schema fields
def extract_schema_fields(
    parsed_json: Dict[str, Any],
    schema_fields: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract only the fields defined in schema, with type coercion.

    Args:
        parsed_json: Parsed JSON dictionary
        schema_fields: Dict mapping field names to expected types

    Returns:
        Dictionary with only schema-defined fields
    """
    result = {}
    for field_name, expected_type in schema_fields.items():
        if field_name in parsed_json:
            value = parsed_json[field_name]
            # Basic type coercion
            if expected_type == str and not isinstance(value, str):
                value = str(value)
            elif expected_type == int and not isinstance(value, int):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    logger.warning(f"Failed to convert {field_name} to int")
                    continue
            elif expected_type == list and not isinstance(value, list):
                value = [value]

            result[field_name] = value
        else:
            logger.debug(f"Field {field_name} not found in parsed JSON")

    return result
