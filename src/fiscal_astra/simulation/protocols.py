"""Structural interface implemented by every local numerical engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fiscal_astra.simulation.contracts import (
    ArtifactReference,
    Diagnostics,
    ModelPlan,
    SimulationRequest,
    SimulationResult,
    ValidationResult,
)


@runtime_checkable
class SimulationEngine(Protocol):
    """Common engine boundary; numerical work happens behind this interface."""

    engine_name: str
    engine_version: str

    def get_engine_metadata(self) -> dict[str, Any]: ...

    def validate_request(self, request: SimulationRequest) -> ValidationResult: ...

    def build_model_plan(self, request: SimulationRequest) -> ModelPlan: ...

    def run_baseline(self, request: SimulationRequest) -> SimulationResult: ...

    def run_policy_scenario(self, request: SimulationRequest) -> SimulationResult: ...

    def run_shock_scenario(self, request: SimulationRequest) -> SimulationResult: ...

    def run_sensitivity(self, request: SimulationRequest) -> SimulationResult: ...

    def run_monte_carlo(self, request: SimulationRequest) -> SimulationResult: ...

    def validate_result(
        self, request: SimulationRequest, result: SimulationResult
    ) -> ValidationResult: ...

    def get_diagnostics(self, result: SimulationResult) -> Diagnostics: ...

    def get_artifacts(self, result: SimulationResult) -> list[ArtifactReference]: ...

    def run(self, request: SimulationRequest) -> SimulationResult: ...


class EngineFactory(Protocol):
    """Constructor signature used by the engine registry."""

    def __call__(self, artifact_root: str | Path | None = None) -> SimulationEngine: ...
