"""
Pydantic schemas for Deep Research System structured outputs.

These schemas ensure LLM responses are validated and properly formatted
for the Actor-Critic loop (Analyst → Critic → Writer).
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Dict, Any


class AnalystResearchOutput(BaseModel):
    """Schema for Analyst Agent research output."""

    status: Literal["DRAFT_READY", "SEARCH_REQUIRED"]
    draft: str = Field(..., description="Research draft in Markdown format (empty if SEARCH_REQUIRED)")
    reasoning_chain: str = Field(..., description="Explanation of reasoning process")
    citations_used: List[int] = Field(
        default_factory=list,
        description="List of citation IDs used (e.g., [1, 3, 5])"
    )
    missing_information: List[str] = Field(
        default_factory=list,
        description="Critical information gaps that block conclusions"
    )
    new_queries: List[str] = Field(
        default_factory=list,
        description="Additional queries needed for SEARCH_REQUIRED status"
    )

    @field_validator('draft')
    @classmethod
    def validate_draft_length(cls, v, info):
        """
        Validate draft length based on status.
        - DRAFT_READY: Must be at least 100 characters
        - SEARCH_REQUIRED: Can be empty
        """
        status = info.data.get('status')
        if status == 'DRAFT_READY' and len(v) < 100:
            raise ValueError("Draft must be at least 100 characters when status is DRAFT_READY")
        return v

    @field_validator('citations_used')
    @classmethod
    def validate_citations(cls, v):
        """Ensure citation IDs are positive integers."""
        if not all(isinstance(x, int) and x > 0 for x in v):
            raise ValueError("Citation IDs must be positive integers")
        return v


class CriticReviewOutput(BaseModel):
    """Schema for Critic Agent review output."""

    status: Literal["PASS", "WARN", "REJECT"]
    critique: str = Field(..., min_length=50, description="Detailed review feedback")
    suggestions: List[str] = Field(
        default_factory=list,
        description="Actionable improvement suggestions"
    )
    mode_compliance: Literal["符合", "違反"] = Field(
        description="Whether draft complies with research mode rules"
    )
    logical_gaps: List[str] = Field(
        default_factory=list,
        description="Identified logical fallacies or gaps"
    )
    source_issues: List[str] = Field(
        default_factory=list,
        description="Source credibility or citation problems"
    )


class WriterComposeOutput(BaseModel):
    """Schema for Writer Agent final composition output."""

    final_report: str = Field(
        ...,
        min_length=200,
        description="Final research report in Markdown format"
    )
    sources_used: List[int] = Field(
        description="Citation IDs used (must be subset of Analyst citations)"
    )
    confidence_level: Literal["High", "Medium", "Low"] = Field(
        description="Overall confidence in the research findings"
    )
    methodology_note: str = Field(
        description="Brief note on research methodology and iterations"
    )

    @field_validator('sources_used')
    @classmethod
    def validate_sources(cls, v):
        """Ensure source IDs are positive integers."""
        if not all(isinstance(x, int) and x > 0 for x in v):
            raise ValueError("Source IDs must be positive integers")
        return v
