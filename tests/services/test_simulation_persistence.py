"""Tests for run persistence, detached execution, and audit events."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fiscal_astra.database import (
    AuditEvent,
    Base,
    SimulationArtifact,
    SimulationModel,
    SimulationResultValue,
    SimulationRun,
)
from fiscal_astra.database import (
    SimulationResult as SimulationResultRow,
)
from fiscal_astra.database.enums import AuditActionType, RunStatus
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.simulation_queue import SimulationWorker
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import SimulationRequest
from fiscal_astra.simulation.debt_dynamics import FiscalDebtDynamicsMvp


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def schedule(factory: sessionmaker[Session]) -> str:
    with factory.begin() as session:
        scenarios = ScenarioRepository(session)
        scenario = scenarios.create_scenario(f"debt-run-{uuid.uuid4()}", "Debt run")
        version = scenarios.create_version(scenario, 1, {}, [])
        run = SimulationRunRepository(session).create_run(version, "baseline")
        return str(run.id)


def request_for(run_id: str, **overrides: object) -> SimulationRequest:
    payload = {
        "run_id": run_id,
        "scenario_id": str(uuid.uuid4()),
        "engine_name": FiscalDebtDynamicsMvp.engine_name,
        "engine_version": FiscalDebtDynamicsMvp.engine_version,
        "baseline_or_policy": "baseline",
        "horizon": 4,
        "frequency": "annual",
        "parameters": {
            "initial_debt_to_gdp": 0.55,
            "nominal_interest_rate": 0.05,
            "nominal_gdp_growth_rate": 0.03,
            "primary_balance": 0.005,
        },
        "data_snapshot_ids": ["snapshot-a"],
        "random_seed": 99,
        "solver_options": {},
        "requested_outputs": ["debt_to_gdp"],
    }
    payload.update(overrides)
    return SimulationRequest(**payload)  # type: ignore[arg-type]


def drain_worker(
    factory: sessionmaker[Session],
    artifact_root: Path,
    request: SimulationRequest,
) -> None:
    worker = SimulationWorker(factory, artifact_root)
    try:
        worker.submit(request)
        worker.join(timeout=30)
    finally:
        worker.shutdown(timeout=10)


def test_worker_persists_completed_result_with_provenance(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule(session_factory)
    request = request_for(run_id)
    drain_worker(session_factory, tmp_path / "artifacts", request)

    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == RunStatus.COMPLETED.value
        assert run.started_at is not None and run.finished_at is not None
        assert run.provenance["request"]["engine_name"] == "fiscal_debt_dynamics_mvp"

        result = session.scalars(
            select(SimulationResultRow).where(SimulationResultRow.run_id == run.id)
        ).one()
        assert result.numeric_status == "verified_numeric"
        assert result.provenance["data_snapshot_ids"] == ["snapshot-a"]
        assert result.provenance["random_seed"] == 99
        values = session.scalars(
            select(SimulationResultValue)
            .where(SimulationResultValue.result_id == result.id)
            .order_by(SimulationResultValue.period)
        ).all()
        assert [value.period for value in values] == ["0", "1", "2", "3", "4"]
        assert len(values) == request.horizon + 1

        model = session.scalars(
            select(SimulationModel).where(SimulationModel.run_id == run.id)
        ).one()
        assert model.status == "converged"
        assert model.limitations

        artifact = session.scalars(
            select(SimulationArtifact).where(SimulationArtifact.run_id == run.id)
        ).all()
        assert artifact
        assert Path(artifact[0].object_key).exists()


def test_worker_result_survives_original_process_closing(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """The run completes from a worker even though the caller never blocks on it."""

    run_id = schedule(session_factory)
    request = request_for(run_id, horizon=25)
    worker = SimulationWorker(session_factory, tmp_path / "artifacts")
    worker.submit(request)
    worker.join(timeout=30)
    worker.shutdown(timeout=10)

    service = SimulationPersistenceService(session_factory, tmp_path / "artifacts")
    stored = service.load_result(run_id, "debt_to_gdp")
    assert stored is not None
    assert stored["horizon"] == 25
    assert len(stored["values"]) == 26
    assert service.verify_audit_chain(run_id)


def test_deterministic_seed_is_recorded_and_reproducible(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    service = SimulationPersistenceService(session_factory, tmp_path / "artifacts")
    engine = FiscalDebtDynamicsMvp(tmp_path / "artifacts")

    terminals: list[float] = []
    for seed in (1234, 1234):
        run_id = schedule(session_factory)
        request = request_for(
            run_id,
            baseline_or_policy="monte_carlo",
            random_seed=seed,
            solver_options={"draws": 32, "interest_rate_std": 0.01},
        )
        result = engine.run_monte_carlo(request)
        service.persist_result(request, result, engine.build_model_plan(request))
        stored = service.load_result(run_id, "debt_to_gdp")
        assert stored is not None
        assert stored["provenance"]["random_seed"] == seed
        terminals.append(result.output_metrics["terminal_debt_to_gdp"]["value"])

    assert terminals[0] == pytest.approx(terminals[1])


def test_invalid_input_persists_failed_run_without_numeric_output(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule(session_factory)
    request = request_for(
        run_id,
        parameters={"initial_debt_to_gdp": 0.6, "nominal_interest_rate": 0.04},
    )
    drain_worker(session_factory, tmp_path / "artifacts", request)

    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == RunStatus.FAILED.value
        assert run.error_message and "nominal_gdp_growth_rate" in run.error_message
        assert run.solver_diagnostics is None
        assert (
            session.scalars(
                select(SimulationResultRow).where(SimulationResultRow.run_id == run.id)
            ).all()
            == []
        )
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run.id).order_by(AuditEvent.sequence)
        ).all()
        assert events[-1].action_type == AuditActionType.SIMULATION_FAILED
        assert all(event.action_type != AuditActionType.SIMULATION_STARTED for event in events)


def test_failed_convergence_is_persisted_and_audited(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule(session_factory)
    request = request_for(
        run_id,
        parameters={
            "initial_debt_to_gdp": 0.6,
            "nominal_interest_rate": 0.25,
            "nominal_gdp_growth_rate": 0.0,
            "primary_balance": 0.0,
        },
        solver_options={"max_debt_to_gdp": 0.7},
    )
    drain_worker(session_factory, tmp_path / "artifacts", request)

    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == RunStatus.FAILED.value
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run.id).order_by(AuditEvent.sequence)
        ).all()
        assert events[-1].action_type == AuditActionType.SIMULATION_FAILED


def test_audit_events_record_submit_start_and_completion_in_order(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule(session_factory)
    request = request_for(run_id)
    drain_worker(session_factory, tmp_path / "artifacts", request)

    logger = AuditLogger(session_factory)
    events = logger.list_for_run(uuid.UUID(run_id))
    actions = [event.action_type for event in events]
    assert actions[0] == AuditActionType.SIMULATION_SUBMITTED
    assert AuditActionType.SIMULATION_STARTED in actions
    assert AuditActionType.SIMULATION_COMPLETED in actions
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert logger.verify_chain(uuid.UUID(run_id))

    service = SimulationPersistenceService(session_factory, tmp_path / "artifacts")
    payload = service.audit_events(run_id)
    assert payload[0]["action_type"] == "simulation_submitted"
    assert payload[-1]["action_type"] == "simulation_completed"


def test_log_artifact_captures_worker_trace(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule(session_factory)
    request = request_for(run_id)
    service = SimulationPersistenceService(session_factory, tmp_path / "artifacts")
    plan = FiscalDebtDynamicsMvp().build_model_plan(request)
    service.mark_running(request, plan)
    engine = FiscalDebtDynamicsMvp(tmp_path / "artifacts")
    result = engine.run(request)
    service.persist_result(request, result, plan)
    log_artifact = service.store_log(request, ["line-one", "line-two"])

    assert Path(log_artifact.location).exists()
    assert service.diagnostics(run_id)["status"] == RunStatus.COMPLETED.value
