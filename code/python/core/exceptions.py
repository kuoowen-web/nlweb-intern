"""Custom exception hierarchy for NLWeb."""

from typing import Dict, Any, Optional


class NLWebError(Exception):
    """
    Base exception for all NLWeb errors.

    All custom exceptions should inherit from this class.
    """

    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ):
        """
        Initialize NLWebError.

        Args:
            message: Human-readable error message
            details: Optional dictionary with error context
            cause: Optional original exception that caused this error
        """
        self.message = message
        self.details = details or {}
        self.cause = cause
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/API responses."""
        result = {
            "error_type": self.__class__.__name__,
            "message": self.message,
        }

        if self.details:
            result["details"] = self.details

        if self.cause:
            result["cause"] = str(self.cause)

        return result

    def __str__(self) -> str:
        """String representation with details."""
        base = f"{self.__class__.__name__}: {self.message}"
        if self.details:
            base += f" | Details: {self.details}"
        if self.cause:
            base += f" | Caused by: {self.cause}"
        return base


# Retrieval Errors

class RetrievalError(NLWebError):
    """Error during document retrieval."""
    pass


class VectorSearchError(RetrievalError):
    """Error during vector similarity search."""

    def __init__(
        self,
        message: str,
        query: Optional[str] = None,
        collection: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        if query:
            details["query"] = query
        if collection:
            details["collection"] = collection
        super().__init__(message, details=details, **kwargs)


class BM25SearchError(RetrievalError):
    """Error during BM25 search."""
    pass


# Ranking Errors

class RankingError(NLWebError):
    """Error during result ranking."""
    pass


class LLMRankingError(RankingError):
    """Error during LLM-based ranking."""

    def __init__(
        self,
        message: str,
        model: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        if model:
            details["model"] = model
        super().__init__(message, details=details, **kwargs)


class XGBoostRankingError(RankingError):
    """Error during XGBoost ranking."""
    pass


# Reasoning Errors

class ReasoningError(NLWebError):
    """Error during reasoning/research process."""
    pass


class AnalystError(ReasoningError):
    """Error in analyst agent."""
    pass


class CriticError(ReasoningError):
    """Error in critic agent."""
    pass


class WriterError(ReasoningError):
    """Error in writer agent."""
    pass


class GapEnrichmentError(ReasoningError):
    """Error during gap detection or enrichment."""
    pass


# LLM Errors

class LLMError(NLWebError):
    """Error from LLM API calls."""

    def __init__(
        self,
        message: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        if model:
            details["model"] = model
        if provider:
            details["provider"] = provider
        if status_code:
            details["status_code"] = status_code
        super().__init__(message, details=details, **kwargs)


class LLMTimeoutError(LLMError):
    """LLM request timed out."""
    pass


class LLMRateLimitError(LLMError):
    """LLM rate limit exceeded."""
    pass


class LLMResponseParseError(LLMError):
    """Failed to parse LLM response."""

    def __init__(
        self,
        message: str,
        raw_response: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        if raw_response:
            # Truncate for logging
            details["raw_response"] = raw_response[:500] + "..." if len(raw_response) > 500 else raw_response
        super().__init__(message, details=details, **kwargs)


# Validation Errors

class ValidationError(NLWebError):
    """Data validation error."""
    pass


class QueryValidationError(ValidationError):
    """Query validation failed."""
    pass


class ResponseValidationError(ValidationError):
    """Response validation failed."""
    pass


# Configuration Errors

class ConfigurationError(NLWebError):
    """Configuration or setup error."""
    pass


class MissingConfigError(ConfigurationError):
    """Required configuration is missing."""

    def __init__(
        self,
        config_name: str,
        key: Optional[str] = None,
        **kwargs
    ):
        message = f"Missing configuration: {config_name}"
        if key:
            message += f".{key}"
        super().__init__(message, **kwargs)


# Source Errors

class SourceError(NLWebError):
    """Error related to source handling."""
    pass


class NoValidSourcesError(SourceError):
    """No valid sources available after filtering."""

    def __init__(
        self,
        mode: str,
        original_count: int,
        filtered_count: int = 0,
        **kwargs
    ):
        message = f"No valid sources for mode '{mode}'"
        details = {
            "mode": mode,
            "original_count": original_count,
            "filtered_count": filtered_count,
        }
        super().__init__(message, details=details, **kwargs)
