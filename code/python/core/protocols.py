"""Protocol types for common interfaces."""

from typing import Protocol, Dict, Any, List, Optional, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> Dict[str, Any]:
        """Execute chat completion."""
        ...

    async def completion(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Execute text completion."""
        ...


@runtime_checkable
class HttpHandler(Protocol):
    """Protocol for HTTP handler interface."""

    async def send_response(
        self,
        data: Dict[str, Any],
        status: int = 200,
    ) -> None:
        """Send HTTP response."""
        ...

    async def send_sse(
        self,
        event: str,
        data: Dict[str, Any],
    ) -> None:
        """Send Server-Sent Event."""
        ...


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for vector store interface."""

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar documents."""
        ...

    async def upsert(
        self,
        documents: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        """Upsert documents with embeddings."""
        ...


@runtime_checkable
class ProgressCallback(Protocol):
    """Protocol for progress update callbacks."""

    def __call__(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Send progress event."""
        ...
