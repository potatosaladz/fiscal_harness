"""Strict contracts for policy-expert context, capabilities, and positions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
ClaimType = Literal["fact", "estimate", "assumption"]


class StrictModel(BaseModel):
    """Base contract that rejects undeclared model-generated fields."""

    model_config = ConfigDict(extra="forbid")


class NumericContextReference(StrictModel):
    """Opaque engine-produced numeric context made available for interpretation."""

    reference_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    numeric_status: Literal["verified_numeric"]
    provenance_type: Literal["model_output", "observed", "estimated"]
    unit: str = Field(min_length=1, max_length=100)
    period: str = Field(min_length=1, max_length=100)
    method: str = Field(min_length=1)
    source_ref: str = Field(min_length=1, max_length=512)
    uncertainty: dict[str, Any]
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceReference(StrictModel):
    """Evidence metadata supplied to an agent without granting verification authority."""

    evidence_id: str = Field(min_length=1, max_length=128)
    citation: str = Field(min_length=1)
    verification_status: Literal["verified", "unverified", "not_found"]
    finding: str | None = None
    limitations: list[str] = Field(default_factory=list)


class PolicyAgentContext(StrictModel):
    """Input loaded through the injected context-provider boundary."""

    run_id: str = Field(min_length=1, max_length=128)
    scenario: dict[str, Any]
    baseline: list[NumericContextReference] = Field(default_factory=list)
    simulation_outputs: list[NumericContextReference] = Field(default_factory=list)
    fiscal_parameters: list[NumericContextReference] = Field(default_factory=list)
    evidence_pack: list[EvidenceReference] = Field(default_factory=list)
    evaluation_criteria: list[str] = Field(min_length=1)


class PolicyClaim(StrictModel):
    claim: str = Field(min_length=1)
    type: ClaimType
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence


class CriterionEvaluation(StrictModel):
    """Qualitative reasoning score, not an economic quantity."""

    score: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class PolicyAgentOutput(StrictModel):
    """Uniform independent position emitted by every policy expert."""

    agent_id: str = Field(min_length=1, max_length=128)
    position_summary: str = Field(min_length=1)
    claims: list[PolicyClaim] = Field(default_factory=list)
    evaluation: dict[str, CriterionEvaluation]
    assumptions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    alternatives_considered: list[str] = Field(default_factory=list)
    disagreements_expected: list[str] = Field(default_factory=list)
    conditions_that_invalidate: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class PolicyAgentCapability(StrictModel):
    """Registry metadata controlling one policy expert's permitted remit."""

    agent_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1)
    expertise: list[str] = Field(min_length=1)
    tasks: list[str] = Field(min_length=1)
    evaluation_criteria: list[str] = Field(min_length=1)
    prompt_version: str = Field(min_length=1, max_length=64)
    tools: tuple[Literal["policy_context"], ...] = ("policy_context",)


__all__ = [
    "Confidence",
    "CriterionEvaluation",
    "EvidenceReference",
    "NumericContextReference",
    "PolicyAgentCapability",
    "PolicyAgentContext",
    "PolicyAgentOutput",
    "PolicyClaim",
]
