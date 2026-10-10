"""Evidence, agent deliberation, recommendation, risk, and audit entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fiscal_astra.database.base import Base, TimestampMixin, json_type, uuid_pk
from fiscal_astra.database.enums import (
    AgentRole,
    AuditActionType,
    DisagreementResolution,
    MessageRole,
    RecommendationStatus,
    RiskCategory,
    RiskSeverity,
    UncertaintyType,
)

if TYPE_CHECKING:
    from fiscal_astra.database.models_simulation import SimulationRun


class EvidenceRecord(Base, TimestampMixin):
    """A retrieved and verified evidence record supporting claims."""

    __tablename__ = "evidence_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    url_or_doi: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    finding: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance: Mapped[str | None] = mapped_column(Text, nullable=True)
    method_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    limitations: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    applicability_to_scenario: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False, default=dict)

    claims: Mapped[list[EvidenceClaim]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan"
    )


class EvidenceClaim(Base):
    """A claim-to-evidence link preserving claim classification."""

    __tablename__ = "evidence_claims"

    id: Mapped[uuid.UUID] = uuid_pk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_records.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(String(128), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (UniqueConstraint("evidence_id", "claim_id", name="uq_evidence_claim"),)

    evidence: Mapped[EvidenceRecord] = relationship(back_populates="claims")


class AgentSession(Base, TimestampMixin):
    """A multi-agent session associated with one simulation run."""

    __tablename__ = "agent_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[AgentRole] = mapped_column(String(30), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_metadata: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict
    )

    run: Mapped[SimulationRun] = relationship()
    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AgentMessage.sequence"
    )


class AgentMessage(Base):
    """One append-only message in an agent session."""

    __tablename__ = "agent_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer(), nullable=False)
    role: Mapped[MessageRole] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_agent_message_sequence"),)

    session: Mapped[AgentSession] = relationship(back_populates="messages")


class Recommendation(Base, TimestampMixin):
    """A decision-support recommendation linked to evidence and criteria."""

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(String(20), nullable=False)
    priority: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    decision_criteria: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict
    )
    monitoring_indicators: Mapped[list[Any]] = mapped_column(
        json_type(), nullable=False, default=list
    )
    contingency_plan: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    minority_positions: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    limitations: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)

    run: Mapped[SimulationRun] = relationship(back_populates="recommendations")


class AuditEvent(Base):
    """Append-only, hash-chained event in the end-to-end audit trail."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[AuditActionType] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_refs: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    output_refs: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    assumption_refs: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    evidence_refs: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_info: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    previous_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_audit_event_run_sequence"),)

    run: Mapped[SimulationRun | None] = relationship(back_populates="audit_events")


@event.listens_for(AuditEvent, "before_update")
def _reject_audit_event_update(*_: Any) -> None:
    """Reject in-process mutation of an audit event."""

    raise ValueError("Audit events are append-only and cannot be updated")


@event.listens_for(AuditEvent, "before_delete")
def _reject_audit_event_delete(*_: Any) -> None:
    """Reject in-process deletion of an audit event."""

    raise ValueError("Audit events are append-only and cannot be deleted")


class UncertaintyRecord(Base, TimestampMixin):
    """A registered source of uncertainty and its affected outputs."""

    __tablename__ = "uncertainty_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uncertainty_type: Mapped[UncertaintyType] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    range_or_distribution: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict
    )
    sources: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    affected_outputs: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    mitigation_or_treatment: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[SimulationRun] = relationship()


class DisagreementRecord(Base, TimestampMixin):
    """A preserved disagreement including evidence conflict and minority report."""

    __tablename__ = "disagreement_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issue: Mapped[str] = mapped_column(Text, nullable=False)
    positions: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    evidence_conflict: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[DisagreementResolution] = mapped_column(String(40), nullable=False)
    decision_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    minority_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[SimulationRun] = relationship()


class RiskRecord(Base, TimestampMixin):
    """A risk-register row including tail-risk mitigation and triggers."""

    __tablename__ = "risk_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[RiskCategory] = mapped_column(String(30), nullable=False)
    probability_value: Mapped[float | None] = mapped_column(nullable=True)
    probability_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_value: Mapped[float | None] = mapped_column(nullable=True)
    impact_metric: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[RiskSeverity] = mapped_column(String(20), nullable=False)
    mitigation: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    residual_risk: Mapped[str | None] = mapped_column(Text, nullable=True)
    interactions: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    trigger_indicators: Mapped[list[Any]] = mapped_column(json_type(), nullable=False, default=list)
    is_tail_risk: Mapped[bool] = mapped_column(nullable=False, default=False)

    run: Mapped[SimulationRun] = relationship()
