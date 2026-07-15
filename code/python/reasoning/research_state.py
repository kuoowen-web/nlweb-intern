"""
ResearchState dataclass - explicit state container for research pipeline phases.

Replaces implicit instance attributes (self.formatted_context, self.source_map)
and local variables scattered across run_research().
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class ResearchState:
    """
    Explicit state container for research pipeline phases.

    Replaces implicit instance attributes (self.formatted_context, self.source_map)
    and local variables scattered across run_research().

    Usage:
        state = ResearchState(query=query, mode=mode, items=items, ...)
        # Each phase reads from state, writes results back to state
        state = await self._phase_filter_and_prepare(state)
    """

    # === Input (immutable after creation) ===
    query: str
    mode: str
    items: List[Dict[str, Any]]
    temporal_context: Optional[Dict[str, Any]] = None
    enable_kg: bool = False
    enable_web_search: bool = False
    query_id: str = ""

    # === Phase 1 output: Filter + Prepare ===
    current_context: List[Dict[str, Any]] = field(default_factory=list)

    # === Phase 1.5 output: Format Context ===
    formatted_context: str = ""
    source_map: Dict[int, Dict[str, Any]] = field(default_factory=dict)

    # === Phase 2 output: Actor-Critic Loop ===
    draft: Optional[str] = None
    review: Optional[Any] = None
    response: Optional[Any] = None
    iteration: int = 0
    reject_count: int = 0
    seen_citation_ids: set = field(default_factory=set)
    analyst_citations: List[int] = field(default_factory=list)
    pending_web_formatted: Optional[str] = None  # SF1: web gap re-format 暫存，loop 內消費即清

    # === Phase 3 output: Writer ===
    final_report: Optional[Any] = None
    plan: Optional[Any] = None

    # === Phase 3.5 output: Chain Analysis ===
    chain_analysis: Optional[Any] = None

    # === Phase 4 output: Format Result ===
    result: Optional[List[Dict[str, Any]]] = None

    # === Infrastructure ===
    iteration_logger: Optional[Any] = None
    tracer: Optional[Any] = None
    enable_isolation: bool = False
    max_iterations: int = 3

    # === Rerun metadata ===
    is_rerun: bool = False

    # === Live Research ===
    enable_live_research: bool = False
    context_map: Optional[Any] = None  # ContextMap instance (set by loop engine)
    initial_context_map: Optional[Any] = None  # Version 0 ContextMap (for Consistency Monitor)
    style_features: Optional[Any] = None  # StyleAnalysisOutput (set by Stage 3)

    # === Error / Early Return ===
    error: Optional[str] = None
    early_return: Optional[List[Dict[str, Any]]] = None
