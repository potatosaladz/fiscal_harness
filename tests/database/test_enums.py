"""Unit tests for Fiscal Astra enum definitions."""

from __future__ import annotations

import pytest

from fiscal_astra.database import enums


def test_provenance_values() -> None:
    assert enums.ProvenanceType.OBSERVED == "observed"
    assert enums.ProvenanceType.ESTIMATED == "estimated"
    assert enums.ProvenanceType.MODEL_OUTPUT == "model_output"
    assert enums.ProvenanceType.ASSUMPTION == "assumption"
    assert enums.ProvenanceType.SCENARIO == "scenario"


def test_verification_values() -> None:
    assert enums.VerificationStatus.UNVERIFIED == "unverified"
    assert enums.VerificationStatus.VERIFIED == "verified"
    assert enums.VerificationStatus.NOT_FOUND == "not_found"


def test_run_stage_values() -> None:
    assert enums.RunStage.RECEIVED == "received"
    assert enums.RunStage.VALIDATING == "validating"
    assert enums.RunStage.SIMULATING == "simulating"
    assert enums.RunStage.DELIBERATING == "deliberating"
    assert enums.RunStage.REPORT_READY == "report_ready"
    assert enums.RunStage.ERROR == "error"


def test_run_status_values() -> None:
    assert enums.RunStatus.DRAFT == "draft"
    assert enums.RunStatus.PENDING == "pending"
    assert enums.RunStatus.VALIDATING == "validating"
    assert enums.RunStatus.QUEUED == "queued"
    assert enums.RunStatus.RUNNING == "running"
    assert enums.RunStatus.SUCCEEDED == "succeeded"
    assert enums.RunStatus.FAILED == "failed"
    assert enums.RunStatus.CANCELLED == "cancelled"
    assert enums.RunStatus.NOT_EXECUTED == "not_executed"


def test_numerical_status_default_not_executed() -> None:
    """The default numeric status must be NOT_EXECUTED (AGENTS.md rule 4)."""
    assert enums.NumericalStatus.NOT_EXECUTED == "not_executed"
    assert enums.NumericalStatus.VERIFIED_NUMERIC == "verified_numeric"
    assert enums.NumericalStatus.ESTIMATED == "estimated"
    assert enums.NumericalStatus.UNAVAILABLE == "unavailable"


def test_simulation_model_status_values() -> None:
    assert enums.SimulationModelStatus.NOT_EXECUTED == "not_executed"
    assert enums.SimulationModelStatus.CONVERGED == "converged"
    assert enums.SimulationModelStatus.FAILED == "failed"


def test_data_source_kind_values() -> None:
    assert enums.DataSourceKind.OFFICIAL_STATISTICS == "official_statistics"
    assert enums.DataSourceKind.PRIMARY_DATA == "primary_data"
    assert enums.DataSourceKind.PEER_REVIEWED == "peer_reviewed"
    assert enums.DataSourceKind.WORKING_PAPER == "working_paper"
    assert enums.DataSourceKind.OFFICIAL_REPORT == "official_report"
    assert enums.DataSourceKind.MODEL_DOC == "model_doc"
    assert enums.DataSourceKind.SYNTHETIC_FIXTURE == "synthetic_fixture"


def test_series_frequency_values() -> None:
    assert enums.SeriesFrequency.ANNUAL == "annual"
    assert enums.SeriesFrequency.QUARTERLY == "quarterly"
    assert enums.SeriesFrequency.MONTHLY == "monthly"
    assert enums.SeriesFrequency.WEEKLY == "weekly"
    assert enums.SeriesFrequency.DAILY == "daily"
    assert enums.SeriesFrequency.IRREGULAR == "irregular"


def test_artifact_kind_values() -> None:
    assert enums.ArtifactKind.INPUT_DATASET == "input_dataset"
    assert enums.ArtifactKind.OUTPUT_TABLE == "output_table"
    assert enums.ArtifactKind.OUTPUT_SERIES == "output_series"
    assert enums.ArtifactKind.LOG == "log"
    assert enums.ArtifactKind.FIGURE == "figure"
    assert enums.ArtifactKind.REPORT == "report"
    assert enums.ArtifactKind.DIAGNOSTIC == "diagnostic"
    assert enums.ArtifactKind.OTHER == "other"


def test_evidence_kind_values() -> None:
    assert enums.EvidenceKind.PRIMARY_DATA == "primary_data"
    assert enums.EvidenceKind.PEER_REVIEWED == "peer_reviewed"
    assert enums.EvidenceKind.WORKING_PAPER == "working_paper"
    assert enums.EvidenceKind.OFFICIAL_REPORT == "official_report"
    assert enums.EvidenceKind.MODEL_DOC == "model_doc"


def test_method_quality_values() -> None:
    assert enums.MethodQuality.HIGH == "high"
    assert enums.MethodQuality.MEDIUM == "medium"
    assert enums.MethodQuality.LOW == "low"


def test_agent_role_values() -> None:
    assert enums.AgentRole.ORCHESTRATOR == "orchestrator"
    assert enums.AgentRole.SIMULATION == "simulation"
    assert enums.AgentRole.CALIBRATION == "calibration"
    assert enums.AgentRole.VALIDATION == "validation"
    assert enums.AgentRole.POLICY_EXPERT == "policy_expert"


def test_message_role_values() -> None:
    assert enums.MessageRole.SYSTEM == "system"
    assert enums.MessageRole.USER == "user"
    assert enums.MessageRole.ASSISTANT == "assistant"
    assert enums.MessageRole.TOOL == "tool"


def test_audit_action_type_values() -> None:
    expected = {
        "input",
        "tool_call",
        "computation",
        "retrieval",
        "verification",
        "deliberation",
        "decision",
        "error",
    }
    actual = {
        enums.AuditActionType.INPUT,
        enums.AuditActionType.TOOL_CALL,
        enums.AuditActionType.COMPUTATION,
        enums.AuditActionType.RETRIEVAL,
        enums.AuditActionType.VERIFICATION,
        enums.AuditActionType.DELIBERATION,
        enums.AuditActionType.DECISION,
        enums.AuditActionType.ERROR,
    }
    assert actual == expected


def test_uncertainty_type_values() -> None:
    expected = {
        "parameter",
        "model",
        "structural",
        "data",
        "policy",
        "political",
        "external",
    }
    actual = {
        enums.UncertaintyType.PARAMETER,
        enums.UncertaintyType.MODEL,
        enums.UncertaintyType.STRUCTURAL,
        enums.UncertaintyType.DATA,
        enums.UncertaintyType.POLICY,
        enums.UncertaintyType.POLITICAL,
        enums.UncertaintyType.EXTERNAL,
    }
    assert actual == expected


def test_disagreement_resolution_values() -> None:
    assert enums.DisagreementResolution.RESOLVED == "resolved"
    assert enums.DisagreementResolution.UNRESOLVED == "unresolved"
    assert enums.DisagreementResolution.ESCALATED_TO_DECISION_MAKER == (
        "escalated_to_decision_maker"
    )


def test_risk_category_values() -> None:
    expected = {
        "macro",
        "fiscal",
        "distributional",
        "implementation",
        "political",
        "external",
        "legal",
    }
    actual = {
        enums.RiskCategory.MACRO,
        enums.RiskCategory.FISCAL,
        enums.RiskCategory.DISTRIBUTIONAL,
        enums.RiskCategory.IMPLEMENTATION,
        enums.RiskCategory.POLITICAL,
        enums.RiskCategory.EXTERNAL,
        enums.RiskCategory.LEGAL,
    }
    assert actual == expected


def test_risk_severity_values() -> None:
    assert enums.RiskSeverity.LOW == "low"
    assert enums.RiskSeverity.MEDIUM == "medium"
    assert enums.RiskSeverity.HIGH == "high"
    assert enums.RiskSeverity.CRITICAL == "critical"


def test_recommendation_status_values() -> None:
    assert enums.RecommendationStatus.PROPOSED == "proposed"
    assert enums.RecommendationStatus.ACCEPTED == "accepted"
    assert enums.RecommendationStatus.REJECTED == "rejected"
    assert enums.RecommendationStatus.DEFERRED == "deferred"


@pytest.mark.parametrize(
    ("enum_cls",),
    [(enums.ProvenanceType,), (enums.VerificationStatus,), (enums.NumericalStatus,)],
)
def test_enum_is_str_enum(enum_cls: type) -> None:
    """Enums subclass ``str`` so they serialize cleanly to JSON."""
    assert issubclass(enum_cls, str)
