"""Typed SSE envelope models + single send helper.

See docs/in progress/plans/sse-typed-pipeline-plan.md for design.
Import from here; internal module split (models/send/registry) is an
implementation detail.
"""
from core.sse.models import (  # noqa: F401
    SseEnvelope,
    BeginNlwebResponse,
    parse_sse_envelope,
)
