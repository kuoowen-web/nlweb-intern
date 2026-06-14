"""
Enhanced schemas for structured reasoning features (Phase 1, 2, 3, KG, Stage 5).

This module extends base reasoning schemas with optional fields for:
- Phase 1: User-friendly SSE progress messages
- Phase 2: Argument graphs and structured critique
- Phase 3: Plan-and-Write for long-form reports
- Phase KG: Entity-Relationship Knowledge Graph generation
- Stage 5: Gap Detection with LLM Knowledge and Web Search

All enhanced fields are optional to maintain backward compatibility.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from enum import Enum
import uuid


# ============================================================================
# Stage 5: Gap Detection Knowledge Enrichment
# ============================================================================

class GapResolutionType(str, Enum):
    """Types of gap resolution for knowledge enrichment."""
    # Core types
    LLM_KNOWLEDGE = "llm_knowledge"      # Static facts (definitions, principles, history)
    WEB_SEARCH = "web_search"            # Dynamic data (current positions, prices, recent events)
    INTERNAL_SEARCH = "internal_search"  # Existing vector DB search

    # Tier 6 API types - Phase 1: 股價
    STOCK_TW = "stock_tw"                # 台股 (TWSE/TPEX)
    STOCK_GLOBAL = "stock_global"        # 全球股價 (yfinance)

    # Tier 6 API types - Phase 2: 天氣/公司 (placeholder)
    WEATHER_TW = "weather_tw"            # 台灣天氣 (CWB)
    WEATHER_GLOBAL = "weather_global"    # 全球天氣 (OpenWeatherMap)
    COMPANY_TW = "company_tw"            # 台灣公司登記
    COMPANY_GLOBAL = "company_global"    # Wikidata

    # Wikipedia (already implemented)
    WIKIPEDIA = "wikipedia"              # Wikipedia API


class GapResolution(BaseModel):
    """Resolution for a detected knowledge gap."""
    gap_type: str = Field(..., description="Type of gap: definition, current_data, context, etc.")
    resolution: GapResolutionType = Field(..., description="How this gap should be resolved")
    reason: str = Field(default="", description="Explanation for resolution choice (for Critic/debug)")
    search_query: Optional[str] = Field(default=None, description="Query for web/internal search")
    llm_answer: Optional[str] = Field(default=None, description="Answer from LLM knowledge")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="Confidence level")
    requires_web_search: bool = Field(default=False, description="True if web search needed but toggle is off")
    topic: Optional[str] = Field(default=None, description="Topic for URN generation: urn:llm:knowledge:{topic}")
    api_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="API-specific parameters (e.g., {'symbol': '2330'} for STOCK_TW)"
    )

# Import base schemas
from reasoning.schemas import AnalystResearchOutput, CriticReviewOutput, WriterComposeOutput


# ============================================================================
# Phase 1: User-Friendly SSE Progress Messages
# ============================================================================

class ProcessUpdate(BaseModel):
    """User-friendly progress message for SSE streaming."""
    stage: str = Field(..., description="Technical stage name (for backend)")
    user_message: str = Field(..., description="User-friendly Chinese message")
    progress: Optional[int] = Field(None, ge=0, le=100, description="Progress percentage")


# ============================================================================
# Phase 2: Argument Graphs and Structured Critique
# ============================================================================

class LogicType(str, Enum):
    """Types of logical reasoning."""
    DEDUCTION = "deduction"  # 演繹:從普遍原則推導
    INDUCTION = "induction"  # 歸納:從多個案例總結
    ABDUCTION = "abduction"  # 溯因:從結果推測原因


class WeaknessType(str, Enum):
    """Fixed vocabulary for logical weakness detection."""
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BIASED_SAMPLE = "biased_sample"
    CORRELATION_NOT_CAUSATION = "correlation_not_causation"
    HASTY_GENERALIZATION = "hasty_generalization"
    MISSING_ALTERNATIVES = "missing_alternatives"
    INVALID_DEDUCTION = "invalid_deduction"
    SOURCE_TIER_VIOLATION = "source_tier_violation"
    LOGICAL_LEAP = "logical_leap"


class ArgumentNode(BaseModel):
    """Single logical unit in reasoning chain with dependency tracking."""
    node_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim: str = Field(..., description="The logical claim being made")
    evidence_ids: List[int] = Field(
        default_factory=list,
        description="Citation IDs supporting this claim (e.g., [1, 3])"
    )
    reasoning_type: LogicType = LogicType.INDUCTION
    confidence: Literal["high", "medium", "low"] = "medium"

    # Phase 4: Reasoning Chain Visualization
    depends_on: List[str] = Field(
        default_factory=list,
        description="List of node_ids this argument depends on"
    )
    confidence_score: Optional[float] = Field(
        None, ge=0.0, le=10.0,
        description="Numerical confidence score (0-10)"
    )
    logic_warnings: List[str] = Field(
        default_factory=list,
        description="Logic consistency warnings (e.g., 'Confidence inflated')"
    )


class StructuredWeakness(BaseModel):
    """Critic's structured weakness detection."""
    node_id: str = Field(..., description="UUID of affected ArgumentNode")
    weakness_type: WeaknessType
    severity: Literal["critical", "moderate", "minor"] = "moderate"
    explanation: str = Field(..., min_length=20, description="Why this is a weakness")


# ============================================================================
# Phase 4: Reasoning Chain Analysis
# ============================================================================

class NodeImpactAnalysis(BaseModel):
    """Impact analysis for a single node."""
    node_id: str
    affects_count: int = Field(..., ge=0)
    affected_node_ids: List[str] = Field(default_factory=list)
    is_critical: bool = False
    criticality_reason: Optional[str] = None


class ReasoningChainAnalysis(BaseModel):
    """Complete reasoning chain analysis with impact propagation."""
    total_nodes: int
    max_depth: int
    topological_order: List[str] = Field(
        default_factory=list,
        description="Node IDs in topological order (for rendering)"
    )
    critical_nodes: List[NodeImpactAnalysis] = Field(default_factory=list)
    has_cycles: bool = False
    cycle_details: Optional[str] = None
    logic_inconsistencies: int = Field(
        0,
        description="Count of logic inflation warnings"
    )


# ============================================================================
# Phase 2: Enhanced Output Classes (using Phase 4 schemas)
# ============================================================================

class AnalystResearchOutputEnhanced(AnalystResearchOutput):
    """Analyst output with optional argument graph."""
    argument_graph: Optional[List[ArgumentNode]] = Field(
        default=None,
        description="Structured argument decomposition (Phase 2)"
    )
    reasoning_chain_analysis: Optional[ReasoningChainAnalysis] = Field(
        default=None,
        description="Reasoning chain analysis with impact propagation (Phase 4)"
    )
    gap_resolutions: List[GapResolution] = Field(
        default_factory=list,
        description="Knowledge gap resolutions (Stage 5)"
    )

    @field_validator('draft')
    @classmethod
    def validate_draft_with_gaps(cls, v, info):
        """
        Validate draft length based on status and gap resolutions.
        Stage 5: Allow shorter drafts when gap_resolutions are present,
        as the response is delegating to web search or LLM knowledge.
        """
        status = info.data.get('status')
        gap_resolutions = info.data.get('gap_resolutions', [])

        # If there are gap resolutions (Stage 5), allow shorter drafts
        if gap_resolutions and len(gap_resolutions) > 0:
            # Minimum 30 characters when using gap resolutions
            if status == 'DRAFT_READY' and len(v) < 30:
                raise ValueError(
                    "Draft must be at least 30 characters when using gap_resolutions"
                )
        else:
            # Original validation: 100 characters for DRAFT_READY
            if status == 'DRAFT_READY' and len(v) < 100:
                raise ValueError(
                    "Draft must be at least 100 characters when status is DRAFT_READY"
                )
        return v


class CriticReviewOutputEnhanced(CriticReviewOutput):
    """Critic output with optional structured weaknesses."""
    structured_weaknesses: Optional[List[StructuredWeakness]] = Field(
        default=None,
        description="Structured weakness analysis (Phase 2)"
    )


# ============================================================================
# Phase 3: Plan-and-Write for Long Reports
# ============================================================================

class WriterPlanOutput(BaseModel):
    """Writer's outline plan before composition."""
    outline: str = Field(..., description="Markdown outline with section headers")
    estimated_length: int = Field(..., ge=1000, description="Target word count")
    key_arguments: List[str] = Field(
        default_factory=list,
        description="Core arguments to develop in each section"
    )


class WriterComposeOutputEnhanced(WriterComposeOutput):
    """Enhanced Writer output with optional plan metadata."""
    plan: Optional[WriterPlanOutput] = Field(
        default=None,
        description="Planning phase output (Phase 3 only)"
    )


# ============================================================================
# Phase KG: Knowledge Graph Generation
# ============================================================================

class EntityType(str, Enum):
    """Entity types for knowledge graph nodes."""
    PERSON = "person"
    ORGANIZATION = "organization"
    EVENT = "event"
    LOCATION = "location"
    METRIC = "metric"
    TECHNOLOGY = "technology"
    CONCEPT = "concept"
    PRODUCT = "product"
    FACILITY = "facility"  # 設施、廠房、基礎設施
    SERVICE = "service"    # 服務、業務


class RelationType(str, Enum):
    """Relationship types for knowledge graph edges."""
    # Causal
    CAUSES = "causes"
    ENABLES = "enables"
    PREVENTS = "prevents"
    # Temporal
    PRECEDES = "precedes"
    CONCURRENT = "concurrent"
    # Hierarchical
    PART_OF = "part_of"
    OWNS = "owns"
    # Associative
    RELATED_TO = "related_to"


class Entity(BaseModel):
    """Single entity node in knowledge graph."""
    entity_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., description="Entity name")
    entity_type: EntityType
    description: Optional[str] = None
    evidence_ids: List[int] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    attributes: Dict[str, Any] = Field(default_factory=dict)
    supporting_claims: List[str] = Field(default_factory=list)  # Future: link to ArgumentNode


class Relationship(BaseModel):
    """Edge between two entities in knowledge graph."""
    relationship_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_entity_id: str
    target_entity_id: str
    relation_type: RelationType
    description: Optional[str] = None
    evidence_ids: List[int] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    temporal_context: Optional[Dict[str, Any]] = None


class KnowledgeGraph(BaseModel):
    """Complete knowledge graph with entities and relationships."""
    entities: List[Entity] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)

    @field_validator('relationships')
    @classmethod
    def validate_relationships(cls, v, info):
        """Ensure all relationship entity_ids reference existing entities."""
        entities = info.data.get('entities', [])
        if not entities:
            # No entities to validate against, skip validation
            return v

        entity_ids = {e.entity_id for e in entities}

        # Filter out invalid relationships instead of raising error
        valid_relationships = []
        for rel in v:
            if rel.source_entity_id in entity_ids and rel.target_entity_id in entity_ids:
                valid_relationships.append(rel)
            else:
                # Log warning but don't fail validation
                from misc.logger.logging_config_helper import get_configured_logger
                logger = get_configured_logger("reasoning.schemas")
                logger.warning(
                    f"Skipping invalid relationship: {rel.source_entity_id} -> {rel.target_entity_id} "
                    f"(entity_ids not found in entities list)"
                )

        return valid_relationships


class AnalystResearchOutputEnhancedKG(AnalystResearchOutputEnhanced):
    """Analyst output with argument graph AND knowledge graph."""
    knowledge_graph: Optional[KnowledgeGraph] = Field(
        default=None,
        description="Entity-relationship knowledge graph"
    )
    # gap_resolutions is inherited from AnalystResearchOutputEnhanced


# ============================================================================
# Phase 2 CoV: Chain of Verification - Claim Extraction and Verification
# ============================================================================

class ClaimType(str, Enum):
    """Types of verifiable claims."""
    NUMBER = "number"           # 數字（金額、數量、百分比）
    DATE = "date"               # 日期（具體日期、時間點）
    PERSON = "person"           # 人名
    ORGANIZATION = "organization"  # 機構名
    EVENT = "event"             # 具體事件
    STATISTIC = "statistic"     # 統計數據
    QUOTE = "quote"             # 引述


class VerifiableClaim(BaseModel):
    """A single verifiable claim extracted from draft."""
    claim: str = Field(..., description="The specific factual claim")
    claim_type: ClaimType = Field(..., description="Type of claim")
    source_reference: Optional[int] = Field(
        default=None,
        description="Citation ID mentioned in the claim (e.g., [1] → 1)"
    )
    context: Optional[str] = Field(
        default=None,
        description="Surrounding context for better verification"
    )
    subject_entity: Optional[str] = Field(
        default=None,
        description=(
            "宣稱把此數字/事實/行為歸屬給哪個實體（公司/機構/人/地名）。"
            "無明確主詞時為 null。驗證階段據此逐實體比對，防張冠李戴。"
        )
    )


class ClaimsList(BaseModel):
    """List of verifiable claims extracted from a draft."""
    claims: List[VerifiableClaim] = Field(
        default_factory=list,
        description="List of extracted verifiable claims"
    )
    extraction_notes: Optional[str] = Field(
        default=None,
        description="Notes about the extraction process"
    )


class VerificationStatus(str, Enum):
    """Status of claim verification."""
    VERIFIED = "verified"           # 來源明確支持此宣稱
    UNVERIFIED = "unverified"       # 來源中找不到支持證據
    CONTRADICTED = "contradicted"   # 來源與宣稱矛盾
    PARTIALLY_VERIFIED = "partially_verified"  # 部分正確


class ClaimVerificationResult(BaseModel):
    """Result of verifying a single claim against sources."""
    claim: str = Field(..., description="The original claim being verified")
    status: VerificationStatus = Field(..., description="Verification status")
    evidence: Optional[str] = Field(
        default=None,
        description="Supporting or contradicting evidence from sources"
    )
    source_id: Optional[int] = Field(
        default=None,
        description="Citation ID of the source used for verification"
    )
    explanation: str = Field(
        ...,
        description="Explanation of verification result"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="Confidence in verification result"
    )


class CoVVerificationOutput(BaseModel):
    """Complete CoV verification output for Critic."""
    results: List[ClaimVerificationResult] = Field(
        default_factory=list,
        description="Verification results for each claim"
    )
    summary: str = Field(
        ...,
        description="Summary of verification findings"
    )
    verified_count: int = Field(default=0, description="Number of verified claims")
    unverified_count: int = Field(default=0, description="Number of unverified claims")
    contradicted_count: int = Field(default=0, description="Number of contradicted claims")

    @property
    def has_critical_issues(self) -> bool:
        """Check if there are critical verification issues."""
        return self.contradicted_count > 0 or self.unverified_count >= 3

    @property
    def verification_rate(self) -> float:
        """Calculate verification success rate."""
        total = len(self.results)
        if total == 0:
            return 1.0
        return self.verified_count / total


class CriticReviewOutputEnhancedCoV(CriticReviewOutputEnhanced):
    """Critic output with CoV verification results."""
    cov_verification: Optional[CoVVerificationOutput] = Field(
        default=None,
        description="Chain of Verification results (Phase 2 CoV)"
    )
