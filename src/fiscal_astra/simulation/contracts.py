"""Typed contracts exchanged by simulation engines and the worker boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BaselineOrPolicy = Literal["baseline", "policy", "shock", "sensitivity", "monte_carlo"]


class SimulationRequest(BaseModel):
    """Immutable-enough input envelope for one reproducible engine invocation."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    scenario_id: str
    engine_name: str
    engine_version: str
    baseline_or_policy: BaselineOrPolicy
    horizon: int = Field(gt=0, le=10_000)
    frequency: str = Field(min_length=1, max_length=32)
    parameters: dict[str, Any]
    data_snapshot_ids: list[str] = Field(default_factory=list)
    random_seed: int | None = None
    solver_options: dict[str, Any] = Field(default_factory=dict)
    requested_outputs: list[str] = Field(min_length=1)


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelPlan(BaseModel):
    engine_name: str
    engine_version: str
    objective: str
    equations: list[str]
    state_variables: list[str]
    parameter_definitions: dict[str, str]
    solver: dict[str, Any]
    validation: list[str]
    limitations: list[str] = Field(default_factory=list)


class Diagnostics(BaseModel):
    status: str
    solver: dict[str, Any] = Field(default_factory=dict)
    checks: dict[str, Any] = Field(default_factory=dict)
    messages: list[str] = Field(default_factory=list)


class ArtifactReference(BaseModel):
    kind: str
    location: str
    checksum_sha256: str
    size_bytes: int = Field(ge=0)
    content_type: str


class SimulationResult(BaseModel):
    status: Literal["converged", "failed", "not_executed"]
    engine_metadata: dict[str, Any]
    input_provenance: dict[str, Any]
    output_metrics: dict[str, Any] = Field(default_factory=dict)
    time_series: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    distributional_outputs: dict[str, Any] = Field(default_factory=dict)
    confidence_interval: dict[str, Any] = Field(default_factory=dict)
    diagnostics: Diagnostics
    convergence: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    audit_references: list[str] = Field(default_factory=list)
    error: str | None = None
