# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Shared async retry/backoff helper for external API calls.

Motivation: prod LR runs died when OpenRouter embedding returned HTTP 429
(engine_overloaded) because the embedding path had zero retry — a single
transient rate-limit aborted the whole Live Research run. This module provides a
small dependency-free retry wrapper (NO tenacity) used by the embedding wrapper,
the Google CSE client, and any other external call that needs transient-fault
resilience.

Design:
- Exponential backoff: 1s, 2s, 4s, ... (2 ** attempt seconds), capped at 30s,
  mirroring chat.websocket.calculate_backoff.
- Retryable faults are decided by a predicate (default: is_retryable_exception),
  so callers can opt extra exception types in.
- Retries are exhausted-then-raise: the LAST exception propagates with its
  original message (no silent fail / no error swallowing).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

import httpx

from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("retry_util")

T = TypeVar("T")

# HTTP status codes that indicate a transient, retryable upstream condition.
# 429 = rate limited (the prod engine_overloaded case), 5xx / 529 = overloaded.
RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 529)


def calculate_backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """
    Exponential backoff delay in seconds for a 0-based attempt index.

    attempt=0 -> 1s, attempt=1 -> 2s, attempt=2 -> 4s, ... capped at `cap`.
    Mirrors chat.websocket.calculate_backoff (2 ** attempt) but returns float
    and allows a configurable base/cap.
    """
    return min(base * (2 ** attempt), cap)


def is_retryable_exception(exc: BaseException) -> bool:
    """
    Default predicate: is this exception a transient fault worth retrying?

    Covers (per prod incident requirements):
    - httpx.HTTPStatusError with status in RETRYABLE_STATUS_CODES (429/5xx/529)
    - httpx.TimeoutException (any timeout flavor) / httpx.ConnectError
    - OpenRouter's "HTTP 200 but body {'error': ...}" rate-limit, which the
      provider raises as a RuntimeError whose message contains
      "OpenRouter embedding API error".
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response is not None and exc.response.status_code in RETRYABLE_STATUS_CODES

    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True

    # OpenRouter HTTP-200-with-error-body rate limit. The provider raises a plain
    # RuntimeError (see embedding_providers/openrouter_embedding.py); match on the
    # message so a transient upstream rate-limit/overload is retried rather than
    # killing the whole run.
    if isinstance(exc, RuntimeError) and "OpenRouter embedding API error" in str(exc):
        return True

    return False


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_cap: float = 30.0,
    is_retryable: Callable[[BaseException], bool] = is_retryable_exception,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    description: str = "external call",
) -> T:
    """
    Run an async, no-arg callable with retry + exponential backoff.

    Args:
        func: Zero-arg coroutine factory. Called fresh on each attempt
              (pass a lambda so a new request is issued per attempt).
        max_retries: Number of RETRIES after the first attempt. max_retries=3
                     means up to 4 total attempts (1 initial + 3 retries), with
                     backoff waits of 1s, 2s, 4s between them.
        base_delay: Base backoff in seconds (attempt 0 wait = base_delay).
        backoff_cap: Maximum backoff delay in seconds.
        is_retryable: Predicate deciding whether a raised exception is retryable.
                      Non-retryable exceptions propagate immediately.
        on_retry: Optional callback(attempt_index, exc, delay) invoked before
                  each backoff sleep (for metrics/logging hooks).
        description: Human-readable label for log lines.

    Returns:
        The successful result of `func`.

    Raises:
        The last exception once retries are exhausted (original message
        preserved — no silent fail), or any non-retryable exception immediately.
    """
    last_exc: Optional[BaseException] = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except BaseException as exc:  # noqa: BLE001 - re-raised below; predicate gates retry
            last_exc = exc

            if not is_retryable(exc):
                # Non-transient error: do not retry, surface immediately.
                raise

            if attempt >= max_retries:
                # Retries exhausted — surface the original error (no silent fail).
                logger.error(
                    f"{description}: retries exhausted after {max_retries + 1} attempts; "
                    f"last error: {type(exc).__name__}: {exc}"
                )
                raise

            delay = calculate_backoff(attempt, base=base_delay, cap=backoff_cap)
            logger.warning(
                f"{description}: attempt {attempt + 1}/{max_retries + 1} failed with "
                f"{type(exc).__name__}: {exc}; retrying in {delay:.1f}s"
            )
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            await asyncio.sleep(delay)

    # Defensive: the loop always returns or raises, but keep mypy/readers happy.
    assert last_exc is not None
    raise last_exc
