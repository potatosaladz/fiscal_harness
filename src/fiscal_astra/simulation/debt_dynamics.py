"""Executable, deliberately small debt-dynamics simulation engine."""

from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path
from typing import Any

from fiscal_astra.simulation.contracts import (
    ArtifactReference,
    Diagnostics,
    ModelPlan,
    SimulationRequest,
    SimulationResult,
    ValidationResult,
)


class FiscalDebtDynamicsMvp:
    """Debt-to-GDP recurrence with deterministic and seeded stochastic modes.

    The primary balance is positive for a surplus. The recurrence is:
    ``b[t+1] = b[t] * (1 + nominal_interest) / (1 + nominal_growth) - primary_balance``.
    All values are scenario inputs or engine outputs, never fabricated evidence.
    """

    engine_name = "fiscal_debt_dynamics_mvp"
    engine_version = "0.1.0"

    def __init__(self, artifact_root: str | Path | None = None) -> None:
        self._artifact_root = Path(artifact_root) if artifact_root else None
        self._last_diagnostics = Diagnostics(status="not_executed")

    def get_engine_metadata(self) -> dict[str, Any]:
        return {
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "model_family": "debt_dynamics",
            "numeric_status": "verified_numeric",
        }

    def validate_request(self, request: SimulationRequest) -> ValidationResult:
        errors: list[str] = []
        required = ("initial_debt_to_gdp", "nominal_interest_rate", "nominal_gdp_growth_rate")
        for name in required:
            if name not in request.parameters:
                errors.append(f"Missing parameter: {name}")
        for name in required:
            value = request.parameters.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                errors.append(f"Parameter must be numeric: {name}")
        if request.frequency != "annual":
            errors.append("fiscal_debt_dynamics_mvp requires annual frequency")
        if request.engine_name != self.engine_name:
            errors.append(f"Unexpected engine_name: {request.engine_name}")
        if request.engine_version != self.engine_version:
            errors.append(f"Unexpected engine_version: {request.engine_version}")
        growth = request.parameters.get("nominal_gdp_growth_rate")
        if isinstance(growth, (int, float)) and growth <= -1:
            errors.append("nominal_gdp_growth_rate must be greater than -1")
        return ValidationResult(valid=not errors, errors=errors)

    def build_model_plan(self, request: SimulationRequest) -> ModelPlan:
        return ModelPlan(
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            objective="Project debt-to-GDP under an explicit primary balance path.",
            equations=[
                "b[t+1] = b[t] * (1 + nominal_interest_rate) / "
                "(1 + nominal_gdp_growth_rate) - primary_balance"
            ],
            state_variables=["debt_to_gdp"],
            parameter_definitions={
                "initial_debt_to_gdp": "Initial debt divided by GDP",
                "nominal_interest_rate": "Nominal effective interest rate",
                "nominal_gdp_growth_rate": "Nominal GDP growth rate",
                "primary_balance": "Primary surplus as a share of GDP; positive is surplus",
            },
            solver={"method": "closed_form_recurrence", "seed": request.random_seed},
            validation=["finite_values", "non_negative_debt", "max_debt_ratio_if_configured"],
            limitations=[
                "No exchange-rate, maturity, contingent-liability, or stock-flow channels."
            ],
        )

    def run_baseline(self, request: SimulationRequest) -> SimulationResult:
        return self._run(request, "baseline")

    def run_policy_scenario(self, request: SimulationRequest) -> SimulationResult:
        return self._run(request, "policy")

    def run_shock_scenario(self, request: SimulationRequest) -> SimulationResult:
        return self._run(request, "shock")

    def run_sensitivity(self, request: SimulationRequest) -> SimulationResult:
        return self._run(request, "sensitivity")

    def run_monte_carlo(self, request: SimulationRequest) -> SimulationResult:
        validation = self.validate_request(request)
        if not validation.valid:
            return self._failed(request, validation.errors)
        draws = int(request.solver_options.get("draws", 100))
        if draws <= 0:
            return self._failed(request, ["solver_options.draws must be positive"])
        rng = random.Random(request.random_seed)
        terminal: list[float] = []
        for _ in range(draws):
            params = dict(request.parameters)
            params["nominal_interest_rate"] = float(params["nominal_interest_rate"]) + rng.gauss(
                0.0, float(request.solver_options.get("interest_rate_std", 0.0))
            )
            params["nominal_gdp_growth_rate"] = float(
                params["nominal_gdp_growth_rate"]
            ) + rng.gauss(0.0, float(request.solver_options.get("growth_rate_std", 0.0)))
            draw_request = request.model_copy(update={"parameters": params})
            draw_result = self._run(draw_request, request.baseline_or_policy)
            if draw_result.status != "converged":
                return draw_result
            terminal.append(draw_result.time_series["debt_to_gdp"][-1]["value"])
        terminal.sort()
        low = terminal[max(0, int(len(terminal) * 0.05) - 1)]
        high = terminal[min(len(terminal) - 1, int(len(terminal) * 0.95))]
        result = self._run(request, request.baseline_or_policy)
        result.confidence_interval = {
            "metric": "terminal_debt_to_gdp",
            "low": low,
            "high": high,
            "distribution": "empirical_monte_carlo",
            "draws": draws,
        }
        return result

    def validate_result(
        self, request: SimulationRequest, result: SimulationResult
    ) -> ValidationResult:
        errors: list[str] = []
        values = result.time_series.get("debt_to_gdp", [])
        if result.status == "converged" and len(values) != request.horizon + 1:
            errors.append("debt_to_gdp series length does not match horizon")
        for point in values:
            if not math.isfinite(float(point["value"])):
                errors.append("debt_to_gdp contains a non-finite value")
        return ValidationResult(valid=not errors, errors=errors)

    def get_diagnostics(self, result: SimulationResult) -> Diagnostics:
        return result.diagnostics

    def get_artifacts(self, result: SimulationResult) -> list[ArtifactReference]:
        return result.artifacts

    def run(self, request: SimulationRequest) -> SimulationResult:
        dispatch = {
            "baseline": self.run_baseline,
            "policy": self.run_policy_scenario,
            "shock": self.run_shock_scenario,
            "sensitivity": self.run_sensitivity,
            "monte_carlo": self.run_monte_carlo,
        }
        return dispatch[request.baseline_or_policy](request)

    def _run(self, request: SimulationRequest, mode: str) -> SimulationResult:
        validation = self.validate_request(request)
        if not validation.valid:
            return self._failed(request, validation.errors)
        params = request.parameters
        debt = float(params["initial_debt_to_gdp"])
        interest = float(params["nominal_interest_rate"])
        growth = float(params["nominal_gdp_growth_rate"])
        primary = float(params.get("primary_balance", 0.0))
        if mode == "shock":
            interest += float(request.solver_options.get("shock_interest_rate_delta", 0.0))
            growth += float(request.solver_options.get("shock_growth_rate_delta", 0.0))
        if mode == "sensitivity":
            interest += float(request.solver_options.get("sensitivity_interest_rate_delta", 0.0))
            growth += float(request.solver_options.get("sensitivity_growth_rate_delta", 0.0))
        if mode == "policy":
            primary += float(request.solver_options.get("policy_primary_balance_delta", 0.0))
        if growth <= -1:
            return self._failed(request, ["effective nominal GDP growth must be greater than -1"])
        series = [{"period": 0, "value": debt, "unit": "ratio", "type": "scenario"}]
        maximum = request.solver_options.get("max_debt_to_gdp")
        for period in range(1, request.horizon + 1):
            debt = debt * (1.0 + interest) / (1.0 + growth) - primary
            if not math.isfinite(debt) or (maximum is not None and debt > float(maximum)):
                return self._failed(request, ["debt dynamics failed configured convergence bound"])
            series.append(
                {"period": period, "value": debt, "unit": "ratio", "type": "model_output"}
            )
        diagnostics = Diagnostics(
            status="converged",
            solver={"method": "closed_form_recurrence", "mode": mode, "seed": request.random_seed},
            checks={"finite_values": True, "periods": len(series)},
        )
        result = SimulationResult(
            status="converged",
            engine_metadata=self.get_engine_metadata(),
            input_provenance={
                "run_id": request.run_id,
                "scenario_id": request.scenario_id,
                "data_snapshot_ids": request.data_snapshot_ids,
                "parameters_type": "scenario",
            },
            output_metrics={
                "terminal_debt_to_gdp": {
                    "value": debt,
                    "unit": "ratio",
                    "period": request.horizon,
                    "type": "model_output",
                    "method": "debt_dynamics_recurrence",
                    "uncertainty": {"basis": "deterministic conditional on inputs"},
                }
            },
            time_series={"debt_to_gdp": series},
            diagnostics=diagnostics,
            convergence={"status": "converged", "iterations": request.horizon},
            limitations=["MVP excludes exchange rate, maturity, and contingent liabilities."],
        )
        self._last_diagnostics = diagnostics
        return self._write_artifact(request, result)

    def _failed(self, request: SimulationRequest, errors: list[str]) -> SimulationResult:
        diagnostics = Diagnostics(status="failed", messages=errors)
        self._last_diagnostics = diagnostics
        return SimulationResult(
            status="failed",
            engine_metadata=self.get_engine_metadata(),
            input_provenance={"run_id": request.run_id, "scenario_id": request.scenario_id},
            diagnostics=diagnostics,
            convergence={"status": "failed"},
            limitations=["No numeric output is available because execution failed."],
            error="; ".join(errors),
        )

    def _write_artifact(
        self, request: SimulationRequest, result: SimulationResult
    ) -> SimulationResult:
        if self._artifact_root is None:
            return result
        directory = self._artifact_root / request.run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "simulation_result.json"
        payload = result.model_dump_json(indent=2).encode("utf-8")
        path.write_bytes(payload)
        result.artifacts = [
            ArtifactReference(
                kind="output_json",
                location=str(path),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                content_type="application/json",
            )
        ]
        return result
