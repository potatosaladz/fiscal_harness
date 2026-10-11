"""Tests for the common engine interface and the executable MVP engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fiscal_astra.simulation import (
    FiscalDebtDynamicsMvp,
    SimulationEngine,
    SimulationRequest,
    registered_engines,
    resolve_engine,
)
from fiscal_astra.simulation.contracts import Diagnostics, ValidationResult


def base_request(**overrides: object) -> SimulationRequest:
    payload = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "scenario_id": "22222222-2222-2222-2222-222222222222",
        "engine_name": FiscalDebtDynamicsMvp.engine_name,
        "engine_version": FiscalDebtDynamicsMvp.engine_version,
        "baseline_or_policy": "baseline",
        "horizon": 3,
        "frequency": "annual",
        "parameters": {
            "initial_debt_to_gdp": 0.60,
            "nominal_interest_rate": 0.04,
            "nominal_gdp_growth_rate": 0.03,
            "primary_balance": 0.01,
        },
        "data_snapshot_ids": ["snapshot-1"],
        "random_seed": 1234,
        "solver_options": {},
        "requested_outputs": ["debt_to_gdp"],
    }
    payload.update(overrides)
    return SimulationRequest(**payload)  # type: ignore[arg-type]


def expected_terminal(
    request: SimulationRequest,
    primary: float,
    extra_growth: float = 0.0,
    extra_interest: float = 0.0,
) -> float:
    debt = float(request.parameters["initial_debt_to_gdp"])
    interest = float(request.parameters["nominal_interest_rate"]) + extra_interest
    growth = float(request.parameters["nominal_gdp_growth_rate"]) + extra_growth
    for _ in range(request.horizon):
        debt = debt * (1.0 + interest) / (1.0 + growth) - primary
    return debt


def test_engine_implements_the_common_protocol() -> None:
    engine = FiscalDebtDynamicsMvp()
    assert isinstance(engine, SimulationEngine)
    assert engine.get_engine_metadata()["engine_name"] == "fiscal_debt_dynamics_mvp"
    assert registered_engines() == ["fiscal_debt_dynamics_mvp"]
    assert isinstance(resolve_engine("fiscal_debt_dynamics_mvp"), FiscalDebtDynamicsMvp)
    with pytest.raises(KeyError, match="Unknown simulation engine"):
        resolve_engine("not-an-engine")


def test_baseline_runs_the_recurrence_and_reports_terminal_metric() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request()
    result = engine.run_baseline(request)

    assert result.status == "converged"
    assert result.engine_metadata["engine_version"] == engine.engine_version
    assert result.input_provenance["data_snapshot_ids"] == ["snapshot-1"]
    series = result.time_series["debt_to_gdp"]
    assert [point["period"] for point in series] == [0, 1, 2, 3]
    assert series[0]["value"] == pytest.approx(0.60)
    assert series[-1]["value"] == pytest.approx(expected_terminal(request, 0.01))
    metric = result.output_metrics["terminal_debt_to_gdp"]
    assert metric["value"] == pytest.approx(expected_terminal(request, 0.01))
    assert metric["unit"] == "ratio"
    assert metric["type"] == "model_output"
    assert metric["method"] == "debt_dynamics_recurrence"
    assert engine.validate_result(request, result).valid
    assert engine.get_diagnostics(result).status == "converged"
    assert result.convergence["status"] == "converged"


def test_policy_scenario_changes_only_the_primary_balance_path() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request(baseline_or_policy="policy")
    baseline = engine.run_baseline(request)
    policy = engine.run_policy_scenario(
        request.model_copy(update={"solver_options": {"policy_primary_balance_delta": 0.02}})
    )

    assert policy.status == "converged"
    assert (
        policy.time_series["debt_to_gdp"][-1]["value"]
        < baseline.time_series["debt_to_gdp"][-1]["value"]
    )
    assert policy.time_series["debt_to_gdp"][-1]["value"] == pytest.approx(
        expected_terminal(request, 0.01 + 0.02)
    )
    assert policy.diagnostics.solver["mode"] == "policy"
    assert engine.validate_result(request, policy).valid


def test_shock_and_sensitivity_paths_shift_rates_deterministically() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request(baseline_or_policy="shock")
    shock = engine.run_shock_scenario(
        request.model_copy(update={"solver_options": {"shock_interest_rate_delta": 0.02}})
    )
    sensitivity = engine.run_sensitivity(
        request.model_copy(update={"solver_options": {"sensitivity_growth_rate_delta": 0.01}})
    )
    assert shock.time_series["debt_to_gdp"][-1]["value"] == pytest.approx(
        expected_terminal(request, 0.01, extra_interest=0.02)
    )
    assert sensitivity.time_series["debt_to_gdp"][-1]["value"] == pytest.approx(
        expected_terminal(request, 0.01, extra_growth=0.01)
    )
    assert sensitivity.diagnostics.solver["mode"] == "sensitivity"


def test_same_seed_reproduces_the_same_monte_carlo_interval() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request(
        baseline_or_policy="monte_carlo",
        solver_options={"draws": 64, "interest_rate_std": 0.01},
    )
    first = engine.run_monte_carlo(request)
    second = engine.run_monte_carlo(request)
    third = engine.run_monte_carlo(
        request.model_copy(update={"random_seed": 4321, "solver_options": {}})
    )

    assert first.status == "converged"
    assert first.confidence_interval["metric"] == "terminal_debt_to_gdp"
    assert first.confidence_interval["draws"] == 64
    assert first.confidence_interval["low"] <= first.confidence_interval["high"]
    assert first.confidence_interval == second.confidence_interval
    # An absent std makes every draw identical, so the interval collapses.
    assert third.confidence_interval["low"] == pytest.approx(third.confidence_interval["high"])
    assert engine.validate_result(request, first).valid


def test_invalid_input_is_rejected_without_fabricating_numbers() -> None:
    engine = FiscalDebtDynamicsMvp()
    invalid = base_request(
        parameters={
            "initial_debt_to_gdp": 0.6,
            "nominal_interest_rate": "not-a-number",
            "nominal_gdp_growth_rate": 0.03,
        }
    )
    validation = engine.validate_request(invalid)

    assert not validation.valid
    assert "Parameter must be numeric: nominal_interest_rate" in validation.errors
    plan = engine.build_model_plan(invalid)
    assert plan.equations
    assert plan.state_variables == ["debt_to_gdp"]
    result = engine.run_baseline(invalid)
    assert result.status == "failed"
    assert "Parameter must be numeric: nominal_interest_rate" in result.error
    assert result.time_series == {}
    assert result.output_metrics == {}
    assert result.convergence["status"] == "failed"
    assert result.artifacts == []


def test_missing_parameter_is_rejected_without_invoking_numeric_loop() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request(parameters={"initial_debt_to_gdp": 0.6})
    result = engine.run(request)
    assert result.status == "failed"
    assert "Missing parameter: nominal_interest_rate" in result.error
    assert "Missing parameter: nominal_gdp_growth_rate" in result.error
    assert result.time_series == {}


def test_request_contract_rejects_missing_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SimulationRequest(
            run_id="run",
            scenario_id="scenario",
            engine_name="fiscal_debt_dynamics_mvp",
            engine_version="0.1.0",
            baseline_or_policy="baseline",
            horizon=0,
            frequency="annual",
            parameters={},
            requested_outputs=[],
        )


def test_non_annual_frequency_is_rejected_by_the_engine() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request(frequency="monthly")
    result = engine.run(request)
    assert result.status == "failed"
    assert "requires annual frequency" in result.error


def test_failed_convergence_produces_a_failed_result_and_diagnostics() -> None:
    engine = FiscalDebtDynamicsMvp()
    request = base_request(
        solver_options={"max_debt_to_gdp": 0.7},
        parameters={
            "initial_debt_to_gdp": 0.60,
            "nominal_interest_rate": 0.20,
            "nominal_gdp_growth_rate": 0.0,
            "primary_balance": 0.0,
        },
    )
    result = engine.run_baseline(request)

    assert result.status == "failed"
    assert "convergence bound" in result.error
    assert result.convergence["status"] == "failed"
    assert engine.get_diagnostics(result).status == "failed"
    assert result.limitations


def test_engine_writes_checksummed_artifact_when_root_is_configured(
    tmp_path: Path,
) -> None:
    engine = FiscalDebtDynamicsMvp(tmp_path / "artifacts")
    result = engine.run_baseline(base_request())

    artifacts = engine.get_artifacts(result)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    import hashlib

    payload = Path(artifact.location).read_bytes()
    assert artifact.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.size_bytes == len(payload)
    assert artifact.kind == "output_json"


def test_diagnostics_defaults() -> None:
    assert Diagnostics(status="not_executed").checks == {}
    assert ValidationResult(valid=True).errors == []
