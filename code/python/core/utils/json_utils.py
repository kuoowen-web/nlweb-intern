"""
Shared JSON utility functions.

Provides jsonify() for safely parsing JSON strings to dict/list,
and trim_json/trim_json_hard aliases for backward compatibility.
"""

import json


def jsonify(obj):
    """Parse JSON string to dict/list; return as-is if already parsed.

    Args:
        obj: A JSON string, dict, or list.

    Returns:
        Parsed dict/list if *obj* was a valid JSON string,
        the original string if parsing fails, or *obj* unchanged
        if it was already a dict/list.
    """
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except json.JSONDecodeError:
            return obj
    return obj


# trim_json / trim_json_hard were domain-specific trimmers for Recipe/Movie
# schemas. For news articles they are no-ops, so they alias jsonify.
trim_json = jsonify
trim_json_hard = jsonify
