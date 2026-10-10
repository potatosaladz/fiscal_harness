"""Explicit enumerations shared across the Fiscal Astra persistence layer.

The values mirror the machine-readable status vocabularies in
``04-output-audit-schema.md`` so that stored rows are directly usable in the
final report without translation.
"""

from __future__ import annotations

from enum import StrEnum as _StrEnum


class StrEnum(_StrEnum):
    """String enumeration whose members serialize to their value."""

    def __str__(self) -> str:
        return str(self.value)


class ProvenanceType(StrEnum):
    """Provenance classification required for every numeric value."""

    OBSERVED = "observed"
    ESTIMATED = "estimated"
    MODEL_OUTPUT = "model_output"
    ASSUMPTION = "assumption"
    SCENARIO = "scenario"


class VerificationStatus(StrEnum):
    """Verification state of an evidence record or cited source."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    NOT_FOUND = "not_found"


class RunStage(StrEnum):
    """Orchestrator run stage (``04-output-audit-schema.md`` §1)."""

    RECEIVED = "received"
    VALIDATING = "validating"
    SIMULATING = "simulating"
    DELIBERATING = "deliberating"
    REPORT_READY = "report_ready"
    ERROR = "error"


class RunStatus(StrEnum):
    """Lifecycle status of a scenario version or simulation run."""

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NumericalStatus(StrEnum):
    """Honest numeric availability status.

    ``NOT_EXECUTED`` is mandatory whenever a numerical engine has not actually
    produced the value; placeholder zeros are forbidden.
    """

    NOT_EXECUTED = "not_executed"
    VERIFIED_NUMERIC = "verified_numeric"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class SimulationModelStatus(StrEnum):
    """Execution outcome reported by a simulation model."""

    NOT_EXECUTED = "not_executed"
    CONVERGED = "converged"
    FAILED = "failed"


class DataSourceKind(StrEnum):
    """Origin category of a data source."""

    OFFICIAL_STATISTICS = "official_statistics"
    PRIMARY_DATA = "primary_data"
    PEER_REVIEWED = "peer_reviewed"
    WORKING_PAPER = "working_paper"
    OFFICIAL_REPORT = "official_report"
    MODEL_DOC = "model_doc"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class SeriesFrequency(StrEnum):
    """Observation frequency for a data series."""

    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    IRREGULAR = "irregular"


class ArtifactKind(StrEnum):
    """Kind of object stored in the artifact bucket."""

    INPUT_DATASET = "input_dataset"
    OUTPUT_TABLE = "output_table"
    OUTPUT_SERIES = "output_series"
    LOG = "log"
    FIGURE = "figure"
    REPORT = "report"
    DIAGNOSTIC = "diagnostic"
    OTHER = "other"


class EvidenceKind(StrEnum):
    """Evidence classification (``04-output-audit-schema.md`` §4)."""

    PRIMARY_DATA = "primary_data"
    PEER_REVIEWED = "peer_reviewed"
    WORKING_PAPER = "working_paper"
    OFFICIAL_REPORT = "official_report"
    MODEL_DOC = "model_doc"


class MethodQuality(StrEnum):
    """Evidence method-quality rating."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AgentRole(StrEnum):
    """Role of a participating agent."""

    ORCHESTRATOR = "orchestrator"
    SIMULATION = "simulation"
    CALIBRATION = "calibration"
    VALIDATION = "validation"
    POLICY_EXPERT = "policy_expert"


class MessageRole(StrEnum):
    """Author role of a stored agent message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AuditActionType(StrEnum):
    """Audit action vocabulary for the append-only audit trail."""

    INPUT = "input"
    TOOL_CALL = "tool_call"
    SIMULATION = "simulation"
    COMPUTATION = "computation"
    RETRIEVAL = "retrieval"
    VERIFICATION = "verification"
    DELIBERATION = "deliberation"
    DECISION = "decision"
    ERROR = "error"
    INPUT_RECEIVED = "input_received"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"
    MODEL_PLAN_CREATED = "model_plan_created"
    DATA_REQUESTED = "data_requested"
    DATA_RETRIEVED = "data_retrieved"
    DATA_VERIFIED = "data_verified"
    SIMULATION_SUBMITTED = "simulation_submitted"
    SIMULATION_STARTED = "simulation_started"
    SIMULATION_COMPLETED = "simulation_completed"
    SIMULATION_FAILED = "simulation_failed"
    COMPUTATION_COMPLETED = "computation_completed"
    AGENT_INVOKED = "agent_invoked"
    AGENT_RESPONSE_RECEIVED = "agent_response_received"
    DELIBERATION_STARTED = "deliberation_started"
    DISAGREEMENT_RECORDED = "disagreement_recorded"
    RECOMMENDATION_CREATED = "recommendation_created"
    REPORT_GENERATED = "report_generated"


class UncertaintyType(StrEnum):
    """Uncertainty registry types (``04-output-audit-schema.md`` §5)."""

    PARAMETER = "parameter"
    MODEL = "model"
    STRUCTURAL = "structural"
    DATA = "data"
    POLICY = "policy"
    POLITICAL = "political"
    EXTERNAL = "external"


class DisagreementResolution(StrEnum):
    """Disagreement resolution state (``04-output-audit-schema.md`` §6)."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ESCALATED_TO_DECISION_MAKER = "escalated_to_decision_maker"


class RiskCategory(StrEnum):
    """Risk categories (``04-output-audit-schema.md`` §8)."""

    MACRO = "macro"
    FISCAL = "fiscal"
    DISTRIBUTIONAL = "distributional"
    IMPLEMENTATION = "implementation"
    POLITICAL = "political"
    EXTERNAL = "external"
    LEGAL = "legal"


class RiskSeverity(StrEnum):
    """Risk severity rating."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendationStatus(StrEnum):
    """Lifecycle status of a recommendation.

    Recommendations are decision support only; ``ACCEPTED`` records a human
    decision and never triggers automated policy action.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
