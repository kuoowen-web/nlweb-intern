"""Error handling decorators and utilities."""

from functools import wraps
import logging
from typing import Callable, TypeVar, Any, Optional, Type, Union, List, Dict
import asyncio

from .exceptions import (
    NLWebError, LLMError, LLMTimeoutError, LLMRateLimitError
)

T = TypeVar('T')
logger = logging.getLogger(__name__)


def handle_errors(
    error_types: Union[Type[Exception], List[Type[Exception]]] = Exception,
    default_return: Any = None,
    reraise_as: Optional[Type[NLWebError]] = None,
    log_level: str = "error",
    include_traceback: bool = True,
) -> Callable:
    """
    Decorator for standardized error handling.

    Args:
        error_types: Exception type(s) to catch
        default_return: Value to return on error (if not reraising)
        reraise_as: NLWebError subclass to reraise as
        log_level: Logging level ("debug", "info", "warning", "error")
        include_traceback: Whether to include traceback in logs

    Example:
        @handle_errors(error_types=ValueError, default_return=[])
        def parse_items(data):
            ...
    """
    if isinstance(error_types, type):
        error_types = [error_types]

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except tuple(error_types) as e:
                _handle_exception(func, e, log_level, include_traceback, reraise_as)
                return default_return

        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except tuple(error_types) as e:
                _handle_exception(func, e, log_level, include_traceback, reraise_as)
                return default_return

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _handle_exception(
    func: Callable,
    error: Exception,
    log_level: str,
    include_traceback: bool,
    reraise_as: Optional[Type[NLWebError]],
) -> None:
    """Handle exception with logging and optional reraising."""
    log_func = getattr(logger, log_level, logger.error)

    message = f"Error in {func.__module__}.{func.__name__}: {error}"

    if include_traceback:
        log_func(message, exc_info=True)
    else:
        log_func(message)

    if reraise_as:
        raise reraise_as(
            message=str(error),
            cause=error,
            details={"function": func.__name__}
        ) from error


def handle_llm_errors(
    default_return: Any = None,
    reraise: bool = False,
    timeout_seconds: Optional[float] = None,
) -> Callable:
    """
    Decorator for handling LLM API errors.

    Args:
        default_return: Value to return on error
        reraise: Whether to reraise as LLMError
        timeout_seconds: Optional timeout for async calls

    Example:
        @handle_llm_errors(reraise=True)
        async def call_openai(prompt):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                if timeout_seconds:
                    return await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout_seconds
                    )
                return await func(*args, **kwargs)

            except asyncio.TimeoutError as e:
                model = kwargs.get('model', 'unknown')
                error_msg = f"LLM timeout in {func.__name__}"
                logger.error(error_msg)

                if reraise:
                    raise LLMTimeoutError(
                        message=error_msg,
                        model=model,
                        cause=e,
                    ) from e
                return default_return

            except Exception as e:
                error_str = str(e).lower()
                model = kwargs.get('model', 'unknown')

                # Check for rate limiting
                if 'rate' in error_str and 'limit' in error_str:
                    logger.warning(f"LLM rate limit hit: {e}")
                    if reraise:
                        raise LLMRateLimitError(
                            message=f"Rate limit exceeded for {model}",
                            model=model,
                            cause=e,
                        ) from e
                    return default_return

                # General LLM error
                error_msg = f"LLM call failed in {func.__name__}: {e}"
                logger.error(error_msg, exc_info=True)

                if reraise:
                    raise LLMError(
                        message=error_msg,
                        model=model,
                        cause=e,
                    ) from e
                return default_return

        return wrapper
    return decorator


def handle_reasoning_errors(
    fallback_response: Optional[Callable] = None,
) -> Callable:
    """
    Decorator for handling reasoning pipeline errors.

    Args:
        fallback_response: Optional function to generate fallback response.
                           Called with (self, error) arguments.

    Example:
        @handle_reasoning_errors(fallback_response=lambda self, e: self._error_result(e))
        async def run_research(self, query):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(self, *args, **kwargs) -> T:
            try:
                return await func(self, *args, **kwargs)

            except NLWebError:
                # Re-raise our own exceptions (already handled)
                raise

            except Exception as e:
                logger.error(
                    f"Reasoning error in {func.__name__}: {e}",
                    exc_info=True
                )

                if fallback_response:
                    return fallback_response(self, error=e)

                # Default error response
                return [{
                    "@type": "Item",
                    "url": "internal://error",
                    "name": "System Error",
                    "site": "System",
                    "score": 0,
                    "description": f"推理系統發生錯誤: {type(e).__name__}",
                }]

        return wrapper
    return decorator


class ErrorContext:
    """Context manager for error handling with cleanup."""

    def __init__(
        self,
        operation: str,
        reraise_as: Optional[Type[NLWebError]] = None,
        cleanup: Optional[Callable] = None,
    ):
        """
        Initialize error context.

        Args:
            operation: Description of the operation
            reraise_as: Exception type to reraise as
            cleanup: Optional cleanup function to call on error
        """
        self.operation = operation
        self.reraise_as = reraise_as
        self.cleanup = cleanup

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            logger.error(f"Error in {self.operation}: {exc_val}", exc_info=True)

            if self.cleanup:
                try:
                    self.cleanup()
                except Exception as cleanup_error:
                    logger.warning(f"Cleanup failed: {cleanup_error}")

            if self.reraise_as and not isinstance(exc_val, NLWebError):
                raise self.reraise_as(
                    message=f"{self.operation} failed: {exc_val}",
                    cause=exc_val,
                ) from exc_val

        return False  # Don't suppress the exception


def create_error_response(
    error: Union[Exception, str],
    query: Optional[str] = None,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a standardized error response dict.

    Args:
        error: Exception or error message
        query: Optional query that caused the error
        operation: Optional operation name

    Returns:
        Error response dictionary
    """
    if isinstance(error, NLWebError):
        error_type = error.__class__.__name__
        message = error.message
        details = error.details
    else:
        error_type = type(error).__name__ if isinstance(error, Exception) else "Error"
        message = str(error)
        details = {}

    description = f"**{error_type}**\n\n{message}"

    if query:
        description += f"\n\n**Query**: {query}"

    if operation:
        description += f"\n\n**Operation**: {operation}"

    if details:
        description += "\n\n**Details**:\n"
        for key, value in details.items():
            description += f"- {key}: {value}\n"

    return {
        "@type": "Item",
        "url": "internal://error",
        "name": f"Error: {error_type}",
        "site": "System",
        "siteUrl": "internal",
        "score": 0,
        "description": description,
    }
