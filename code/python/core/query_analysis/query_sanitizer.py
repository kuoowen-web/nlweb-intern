"""
QuerySanitizer — P1-2: Query length and format defense.

Checks query length (rejecting over-long queries) and strips dangerous
formatting (template variables, control characters).

This is a SYNC/static module — pure string operations, no async needed.
Called from _init_core_params() (sync) and runQuery() (async, for logging).
"""

import re
import logging

logger = logging.getLogger(__name__)

# Maximum allowed query length. Temporary value; will be adjusted based on analytics
# after launch (track query length P95/P99, raise if P95 > 400).
MAX_QUERY_LENGTH = 500

# Template variable pattern: {anything_here}
_RE_TEMPLATE_VAR = re.compile(r'\{[^}]*\}')

# Control characters to strip: ASCII 0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f
# Preserve 0x0a (\n, newline) and 0x0d (\r, carriage return) for readability.
_RE_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


class QuerySanitizer:
    """
    Stateless sanitizer for user queries.

    Separates two concerns:
    - Rejection: query too long → caller returns HTTP 400
    - Sanitization: template vars / control chars → strip and continue

    Per spec D2 (docs/specs/guardrail-spec.md): Phase 1 does NOT block
    template-var or control-char queries; it sanitizes and logs them.
    """

    @staticmethod
    def sanitize(query: str) -> dict:
        """
        Validate and clean a query string.

        Returns a dict:
            {
                'rejected': bool,       # True if query exceeds MAX_QUERY_LENGTH
                'reason': str,          # rejection reason ('query_too_long') or ''
                'sanitized': bool,      # True if any modifications were made
                'cleaned_query': str,   # the cleaned query (same as input if unchanged)
                'changes': list[str],   # human-readable list of changes made
            }

        Length check happens BEFORE cleaning so we measure the raw length.
        """
        changes: list = []

        # ── 1. Length check (rejection, not sanitization) ─────────────────────
        if len(query) > MAX_QUERY_LENGTH:
            return {
                'rejected': True,
                'reason': 'query_too_long',
                'sanitized': False,
                'cleaned_query': query,
                'changes': [],
            }

        # ── 2. Strip template variables ────────────────────────────────────────
        template_vars = _RE_TEMPLATE_VAR.findall(query)
        if template_vars:
            for var in template_vars:
                changes.append(f'stripped template variable: {var}')
            query = _RE_TEMPLATE_VAR.sub('', query)

        # ── 3. Strip control characters ────────────────────────────────────────
        ctrl_match = _RE_CONTROL_CHARS.search(query)
        if ctrl_match:
            changes.append('stripped control characters')
            query = _RE_CONTROL_CHARS.sub('', query)

        return {
            'rejected': False,
            'reason': '',
            'sanitized': bool(changes),
            'cleaned_query': query,
            'changes': changes,
        }
