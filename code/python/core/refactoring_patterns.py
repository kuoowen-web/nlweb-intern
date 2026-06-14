"""
Refactoring utility functions for reducing code nesting.

This module provides helper functions that implement common patterns
for flattening deeply nested code:

1. Guard clauses (early returns)
2. Conditional extraction
3. Safe data access
4. Loop filtering

Usage:
    from core.refactoring_patterns import safe_get, filter_items, process_if_valid
"""

from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar('T')


def safe_get(data: Any, *keys: str, default: Any = None) -> Any:
    """
    Safely navigate nested dictionaries/objects.

    Replaces deeply nested if-checks:
        # BEFORE
        if data:
            if data.get("level1"):
                if data["level1"].get("level2"):
                    return data["level1"]["level2"]["value"]
        return None

        # AFTER
        return safe_get(data, "level1", "level2", "value")

    Args:
        data: Dictionary or object to navigate
        *keys: Sequence of keys to traverse
        default: Value to return if any key is missing

    Returns:
        Value at the nested path, or default if not found
    """
    current = data
    for key in keys:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(key)
        elif hasattr(current, key):
            current = getattr(current, key, None)
        else:
            return default
    return current if current is not None else default


def filter_items(
    items: List[T],
    *predicates: Callable[[T], bool],
) -> List[T]:
    """
    Filter items through multiple predicates.

    Replaces nested loop filtering:
        # BEFORE
        results = []
        for item in items:
            if item:
                if item.get("valid"):
                    if item.get("score", 0) > 0.5:
                        results.append(item)

        # AFTER
        results = filter_items(
            items,
            lambda x: x is not None,
            lambda x: x.get("valid"),
            lambda x: x.get("score", 0) > 0.5
        )

    Args:
        items: List of items to filter
        *predicates: Variable number of filter functions

    Returns:
        Filtered list where all predicates return True
    """
    result = list(items)
    for predicate in predicates:
        result = [item for item in result if predicate(item)]
    return result


def process_if_valid(
    data: Any,
    validator: Callable[[Any], bool],
    processor: Callable[[Any], T],
    default: Optional[T] = None,
) -> Optional[T]:
    """
    Process data only if it passes validation.

    Replaces:
        # BEFORE
        if data:
            if is_valid(data):
                return process(data)
        return None

        # AFTER
        return process_if_valid(data, is_valid, process)

    Args:
        data: Data to validate and process
        validator: Function that returns True if data is valid
        processor: Function to process valid data
        default: Value to return if validation fails

    Returns:
        Processed result or default
    """
    if data is None:
        return default
    if not validator(data):
        return default
    return processor(data)


def first_match(
    items: List[T],
    predicate: Callable[[T], bool],
    default: Optional[T] = None,
) -> Optional[T]:
    """
    Find first item matching predicate.

    Replaces:
        # BEFORE
        result = None
        for item in items:
            if predicate(item):
                result = item
                break
        return result

        # AFTER
        return first_match(items, predicate)

    Args:
        items: List to search
        predicate: Function returning True for matches
        default: Value if no match found

    Returns:
        First matching item or default
    """
    for item in items:
        if predicate(item):
            return item
    return default


def batch_process(
    items: List[T],
    processor: Callable[[T], Any],
    error_handler: Optional[Callable[[T, Exception], None]] = None,
) -> List[Any]:
    """
    Process items with per-item error handling.

    Replaces:
        # BEFORE
        results = []
        for item in items:
            try:
                result = process(item)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Failed: {e}")

        # AFTER
        results = batch_process(items, process, lambda i, e: logger.error(f"Failed: {e}"))

    Args:
        items: Items to process
        processor: Processing function
        error_handler: Optional function called with (item, exception) on error

    Returns:
        List of non-None results (errors are skipped)
    """
    results = []
    for item in items:
        try:
            result = processor(item)
            if result is not None:
                results.append(result)
        except Exception as e:
            if error_handler:
                error_handler(item, e)
    return results


def switch_on(
    value: Any,
    cases: Dict[Any, T],
    default: Optional[T] = None,
) -> Optional[T]:
    """
    Dictionary-based switch statement.

    Replaces:
        # BEFORE
        if mode == "strict":
            threshold = 0.9
        elif mode == "discovery":
            threshold = 0.7
        elif mode == "monitor":
            threshold = 0.8
        else:
            threshold = 0.75

        # AFTER
        threshold = switch_on(mode, {
            "strict": 0.9,
            "discovery": 0.7,
            "monitor": 0.8,
        }, default=0.75)

    Args:
        value: Value to match
        cases: Dictionary mapping values to results
        default: Value if no match found

    Returns:
        Matched result or default
    """
    return cases.get(value, default)


def chain_conditions(
    *conditions: Callable[[], bool],
    short_circuit: bool = True,
) -> bool:
    """
    Evaluate conditions in sequence.

    Replaces deeply nested conditions:
        # BEFORE
        if condition1():
            if condition2():
                if condition3():
                    return True
        return False

        # AFTER
        return chain_conditions(condition1, condition2, condition3)

    Args:
        *conditions: Callable conditions to evaluate
        short_circuit: If True, stop at first False (AND logic)
                      If False, evaluate all conditions

    Returns:
        True if all conditions pass
    """
    for condition in conditions:
        result = condition()
        if short_circuit and not result:
            return False
    return True


def validate_and_extract(
    data: Dict[str, Any],
    required_keys: List[str],
    optional_keys: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Validate required keys exist and extract subset.

    Replaces:
        # BEFORE
        if "key1" in data and "key2" in data:
            result = {"key1": data["key1"], "key2": data["key2"]}
            if "key3" in data:
                result["key3"] = data["key3"]
            return result
        return None

        # AFTER
        return validate_and_extract(data, ["key1", "key2"], ["key3"])

    Args:
        data: Dictionary to validate
        required_keys: Keys that must be present
        optional_keys: Keys to include if present

    Returns:
        Extracted dictionary or None if validation fails
    """
    if not isinstance(data, dict):
        return None

    # Check required keys
    for key in required_keys:
        if key not in data:
            return None

    # Extract subset
    result = {key: data[key] for key in required_keys}

    # Add optional keys if present
    if optional_keys:
        for key in optional_keys:
            if key in data:
                result[key] = data[key]

    return result
