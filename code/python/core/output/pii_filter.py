# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
PII Filter — Output PII detection and masking (P2-3).

Scans LLM-generated message content for PII (Taiwan National ID, mobile phone,
credit card, email) and masks it before the message reaches the user.

Only filters LLM-generated message types (summary, intermediate_result, nlws,
intermediate_message). NEVER filters original news cards (result type).

Kill switch: GUARDRAIL_PII_ENABLED env var (default: true).
"""

import os
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

GUARDRAIL_PII_ENABLED: bool = os.environ.get("GUARDRAIL_PII_ENABLED", "true").lower() != "false"

# ---------------------------------------------------------------------------
# Message types to filter (LLM-generated summaries / analyses)
# NEVER filter: result, begin-nlweb-response, end-nlweb-response, progress, header
# ---------------------------------------------------------------------------

PII_FILTERED_MESSAGE_TYPES: frozenset[str] = frozenset({
    "summary",
    "intermediate_result",
    "nlws",
    "intermediate_message",
})

# ---------------------------------------------------------------------------
# Taiwan National ID — letter→two-digit mapping
# Special cases per spec: I=34, O=35, W=32
# ---------------------------------------------------------------------------

_TW_ID_LETTER_MAP: dict[str, int] = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16,
    "H": 17, "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22,
    "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28,
    "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
}

# Weights: letter tens-digit×1, letter units-digit×9, digits[1-8]×8,7,6,5,4,3,2,1, digit[9]×1
_TW_ID_WEIGHTS: tuple[int, ...] = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (module load time)
# Taiwan ID is checked BEFORE credit card (more specific, avoids consuming matches)
# ---------------------------------------------------------------------------

# Taiwan National ID: one uppercase letter + 1 or 2 + 8 digits
_RE_TW_ID = re.compile(r'\b([A-Z][12]\d{8})\b')

# Mobile phone: 09xx-xxx-xxx (dashes optional)
_RE_PHONE = re.compile(r'\b(09\d{2}-?\d{3}-?\d{3})\b')

# Credit card: 4 groups of 4 digits, optional dash or space separator
_RE_CREDIT_CARD = re.compile(r'\b(\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})\b')

# Email: standard pattern
_RE_EMAIL = re.compile(
    r'\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b'
)


# ---------------------------------------------------------------------------
# Checksum validators
# ---------------------------------------------------------------------------

def _validate_tw_national_id(id_str: str) -> bool:
    """
    Validate Taiwan National ID using weighted checksum.

    The letter maps to a two-digit number.  The first digit of that two-digit
    number is multiplied by 1, the second digit by 9, then the 8 body digits
    are multiplied by 8,7,6,5,4,3,2,1, and the check digit is multiplied by 1.
    Total mod 10 must equal 0.
    """
    id_str = id_str.upper()
    if len(id_str) != 10:
        return False
    letter = id_str[0]
    if letter not in _TW_ID_LETTER_MAP:
        return False
    mapped = _TW_ID_LETTER_MAP[letter]
    # Expand the letter into its two decimal digits
    digits = [mapped // 10, mapped % 10]
    # Append the 9 remaining digits (index 1..9 in original string)
    for ch in id_str[1:]:
        if not ch.isdigit():
            return False
        digits.append(int(ch))
    # digits is now length 11; apply weights
    total = sum(d * w for d, w in zip(digits, _TW_ID_WEIGHTS))
    return total % 10 == 0


def _validate_luhn(card_str: str) -> bool:
    """
    Validate credit card number using the Luhn algorithm.
    Strips dashes and spaces before checking.
    """
    digits = card_str.replace("-", "").replace(" ", "")
    if not digits.isdigit() or len(digits) != 16:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:          # double every second digit from right
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Masking helpers
# ---------------------------------------------------------------------------

def _mask_tw_id(id_str: str) -> str:
    """A1****5678 — keep first 2 chars and last 4 chars."""
    return id_str[:2] + "****" + id_str[6:]


def _mask_phone(phone_str: str) -> str:
    """09xx-xxx-xxx (fixed format regardless of input separators)."""
    return "09xx-xxx-xxx"


def _mask_credit_card(card_str: str) -> str:
    """****-****-****-1234 — keep last 4 digits."""
    digits = card_str.replace("-", "").replace(" ", "")
    last4 = digits[-4:]
    return f"****-****-****-{last4}"


def _mask_email(email_str: str) -> str:
    """u***@domain.com — keep first char of local part, mask rest."""
    at_idx = email_str.index("@")
    local = email_str[:at_idx]
    domain = email_str[at_idx:]
    masked_local = local[0] + "***" if len(local) > 0 else "***"
    return masked_local + domain


# ---------------------------------------------------------------------------
# Core filter function (sync, pure CPU)
# ---------------------------------------------------------------------------

def filter_pii(text: str) -> tuple[str, list[dict]]:
    """
    Scan text, replace detected PII with masked versions.

    Detection order matters — Taiwan ID is processed before credit card to
    prevent a 10-digit ID from being partially consumed by the card pattern.

    Args:
        text: The raw text to scan.

    Returns:
        (filtered_text, detections) where detections is a list of dicts:
            {"pii_type": str, "original": str, "masked": str, "position": int}
    """
    detections: list[dict] = []
    result = text

    # 1. Taiwan National ID (most specific — run first)
    def _replace_tw_id(m: re.Match) -> str:
        candidate = m.group(1)
        if _validate_tw_national_id(candidate):
            masked = _mask_tw_id(candidate)
            detections.append({
                "pii_type": "tw_national_id",
                "original": candidate,
                "masked": masked,
                "position": m.start(),
            })
            return masked
        return candidate  # no match → return unchanged

    result = _RE_TW_ID.sub(_replace_tw_id, result)

    # 2. Mobile phone
    def _replace_phone(m: re.Match) -> str:
        candidate = m.group(1)
        # Validate: strip separators → must be exactly 10 digits starting with 09
        digits = candidate.replace("-", "")
        if len(digits) == 10 and digits.startswith("09"):
            masked = _mask_phone(candidate)
            detections.append({
                "pii_type": "mobile_phone",
                "original": candidate,
                "masked": masked,
                "position": m.start(),
            })
            return masked
        return candidate

    result = _RE_PHONE.sub(_replace_phone, result)

    # 3. Credit card (after Taiwan ID to avoid overlap)
    def _replace_card(m: re.Match) -> str:
        candidate = m.group(1)
        if _validate_luhn(candidate):
            masked = _mask_credit_card(candidate)
            detections.append({
                "pii_type": "credit_card",
                "original": candidate,
                "masked": masked,
                "position": m.start(),
            })
            return masked
        return candidate

    result = _RE_CREDIT_CARD.sub(_replace_card, result)

    # 4. Email (no checksum — higher FP rate, acceptable for summary-only scope)
    def _replace_email(m: re.Match) -> str:
        candidate = m.group(1)
        # Basic sanity: must have exactly one @, domain has at least one dot
        if candidate.count("@") == 1:
            masked = _mask_email(candidate)
            detections.append({
                "pii_type": "email",
                "original": candidate,
                "masked": masked,
                "position": m.start(),
            })
            return masked
        return candidate

    result = _RE_EMAIL.sub(_replace_email, result)

    return result, detections


# ---------------------------------------------------------------------------
# Async wrapper — checks message_type, calls filter_pii, logs detections
# ---------------------------------------------------------------------------

async def filter_message_pii(message: dict, user_id: str = None) -> dict:
    """
    Filter PII from a message dict if the message_type is filterable.

    Only operates on PII_FILTERED_MESSAGE_TYPES. NEVER modifies result
    messages (original news cards).

    Args:
        message:  The message dict (must have message_type set).
        user_id:  Authenticated user ID for event logging (nullable).

    Returns:
        The (possibly modified) message dict.
    """
    if not GUARDRAIL_PII_ENABLED:
        return message

    message_type = message.get("message_type", "")
    if message_type not in PII_FILTERED_MESSAGE_TYPES:
        return message

    # Identify the text field(s) to filter.
    # Most LLM-generated messages carry their text in "content".
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return message

    try:
        filtered_content, detections = filter_pii(content)
    except Exception as e:
        # Fail-open: PII filter must not break the message pipeline
        logger.error(f"pii_filter.filter_pii failed (message_type={message_type}): {e}", exc_info=True)
        return message

    if not detections:
        return message

    # Apply the filtered content
    message = dict(message)   # shallow copy — don't mutate caller's dict
    message["content"] = filtered_content

    # Fire-and-forget event logging
    try:
        from core.guardrail_logger import GuardrailLogger
        gl = GuardrailLogger.get_instance()
        await gl.log_event(
            event_type="pii_filtered",
            severity="info",
            user_id=user_id,
            details={
                "message_type": message_type,
                "pii_count": len(detections),
                "pii_types": [d["pii_type"] for d in detections],
            },
        )
    except Exception as e:
        logger.error(f"pii_filter: GuardrailLogger.log_event failed: {e}", exc_info=True)

    return message
