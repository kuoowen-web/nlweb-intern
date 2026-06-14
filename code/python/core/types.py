"""Common type aliases for the codebase."""

from typing import (
    Dict, Any, List, Optional, Union, Callable, Awaitable, TypeVar
)

# Generic type variable
T = TypeVar('T')

# Query and search types
QueryParams = Dict[str, Any]
SearchResult = Dict[str, Any]
SearchResults = List[SearchResult]

# Item types
ItemDict = Dict[str, Any]
ItemList = List[ItemDict]

# Callback types
SyncCallback = Callable[..., None]
AsyncCallback = Callable[..., Awaitable[None]]
ProgressCallback = Callable[[str, Dict[str, Any]], None]

# Configuration types
ConfigDict = Dict[str, Any]

# Response types
ResponseDict = Dict[str, Any]
ErrorDict = Dict[str, Union[str, int, Dict[str, Any]]]

# Temporal types
TemporalDict = Dict[str, Any]

# Graph types
ClaimDict = Dict[str, Any]
EntityDict = Dict[str, Any]
RelationshipDict = Dict[str, Any]
ArgumentGraphDict = Dict[str, List[ClaimDict]]
KnowledgeGraphDict = Dict[str, List[Union[EntityDict, RelationshipDict]]]

# Citation types
CitationId = int
CitationIds = List[CitationId]
CitationMap = Dict[CitationId, ItemDict]
