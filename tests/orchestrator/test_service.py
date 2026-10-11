"""Pipeline, gating, idempotency, resume, and error-code tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fiscal_astra.database import AuditEvent, Base, SimulationRun
from fiscal_astra.database.enums import (
    AuditActionType,
    OrchestratorState,
    RunStatus,
)
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.orchestrator.service import OrchestratorService
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import SimulationRequest
from fiscal_astra.simulation.debt_dynamics import FiscalDebtDynamicsMvp
from fiscal_astra.worker.tasks import execute_simulation_run

VALID_PARAMETERS = {
    "initial_debt_to_gdp": 0.55,
    "nominal_interest_rate": 0.05,
    "nominal_gdp_growth_rate": 0.03,
    "primary_balance": 0.005,
}


class RecordingPublisher:
    """Stand-in for the broker that records job ids instead of connecting."""

    def __init__(self) -> None:
        self.jobs: list[str] = []

    def enqueue(self, run_id: str) -> str:
        self.jobs.append(run_id)
        return f"job-{len(self.jobs)}"


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


def schedule_run(
    factory: sessionmaker[Session],
    *,
    engine_name: str = FiscalDebtDynamicsMvp.engine_name,
    parameters: dict[str, object] | None = None,
) -> str:
    """Create a run whose stored manifest mirrors what POST /runs persists."""

    run_id = uuid.uuid4()
    request = SimulationRequest(
        run_id=str(run_id),
        scenario_id=str(uuid.uuid4()),
        engine_name=engine_name,
        engine_version=FiscalDebtDynamicsMvp.engine_version,
        baseline_or_policy="baseline",
        horizon=4,
        frequency="annual",
        parameters=dict(parameters or VALID_PARAMETERS),
        data_snapshot_ids=[],
        random_seed=99,
        solver_options={},
        requested_outputs=["debt_to_gdp"],
    )
    with factory.begin() as session:
        scenarios = ScenarioRepository(session)
        scenario = scenarios.create_scenario(f"orchestrator-{run_id}", "Orchestrator test")
        version = scenarios.create_version(scenario, 1, request.parameters, [])
        run = SimulationRunRepository(session).create_run(
            version,
            "orchestrator-test",
            run_id=run_id,
            correlation_id=f"corr-{run_id.hex[:12]}",
        )
        run.provenance = {**run.provenance, "request": request.model_dump(mode="json")}
    return str(run_id)


def test_pipeline_reaches_completed_with_audited_transitions(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    run_id = schedule_run(session_factory)
    simulation_jobs = RecordingPublisher()
    service = OrchestratorService(
        session_factory, artifact_root, simulation_publisher=simulation_jobs
    )

    assert service.advance(run_id) is OrchestratorState.SIMULATION_QUEUED
    assert simulation_jobs.jobs == [run_id]

    # A repeated delivery must not publish a second simulation job.
    assert service.advance(run_id) is OrchestratorState.SIMULATION_QUEUED
    assert simulation_jobs.jobs == [run_id]

    resume_signals = RecordingPublisher()
    execute_simulation_run(
        run_id,
        session_factory,
        SimulationPersistenceService(session_factory, artifact_root),
        orchestrator=service,
        publisher=resume_signals,
    )
    assert resume_signals.jobs == [run_id]

    assert service.advance(run_id) is OrchestratorState.COMPLETED

    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == RunStatus.SUCCEEDED.value
        assert run.orchestrator_state == OrchestratorState.COMPLETED.value
        assert run.error_code is None
        assert run.correlation_id.startswith("corr-")
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run.id).order_by(AuditEvent.sequence)
        ).all()
        assert all(event.trace_id == run.correlation_id for event in events)
        transitions = [
            event.input_refs[0]["to"]
            for event in events
            if event.action_type == AuditActionType.STATE_TRANSITION
        ]

    assert transitions == [
        OrchestratorState.VALIDATING.value,
        OrchestratorState.MODEL_PLANNING.value,
        OrchestratorState.DATA_PREPARATION.value,
        OrchestratorState.SIMULATION_QUEUED.value,
        OrchestratorState.SIMULATING.value,
        OrchestratorState.PARAMETER_EXTRACTION.value,
        OrchestratorState.POLICY_AGENT_ANALYSIS.value,
        OrchestratorState.DELIBERATION.value,
        OrchestratorState.EVIDENCE_VERIFICATION.value,
        OrchestratorState.REPORT_GENERATION.value,
        OrchestratorState.COMPLETED.value,
    ]
    assert AuditLogger(session_factory).verify_chain(uuid.UUID(run_id))

    report = json.loads((artifact_root / run_id / "orchestrator_report.json").read_text())
    assert report["recommendations"]["status"] == "not_executed"
    assert report["skipped_stages"] == []
    assert [item["numeric_status"] for item in report["results"]] == ["verified_numeric"]


def test_run_without_simulation_result_cannot_reach_recommendation_state(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule_run(session_factory)
    service = OrchestratorService(
        session_factory, tmp_path, simulation_publisher=RecordingPublisher()
    )
    service.advance(run_id)

    # Simulate a worker that finished without persisting any numeric result.
    service.store.transition(run_id, OrchestratorState.SIMULATING, reason="test setup")
    with session_factory.begin() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        run.status = RunStatus.SUCCEEDED.value

    assert service.advance(run_id) is OrchestratorState.FAILED
    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.error_code == "missing_simulation_result"
        assert run.orchestrator_state == OrchestratorState.FAILED.value


def test_not_executed_simulation_fails_the_run_with_a_code(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule_run(session_factory)
    simulation_jobs = RecordingPublisher()
    service = OrchestratorService(session_factory, tmp_path, simulation_publisher=simulation_jobs)
    assert service.advance(run_id) is OrchestratorState.SIMULATION_QUEUED

    with session_factory.begin() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        run.status = RunStatus.NOT_EXECUTED.value
        run.error_message = "validation failed; numerical engine was not executed"

    assert service.advance(run_id) is OrchestratorState.FAILED

    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == RunStatus.NOT_EXECUTED.value
        assert run.error_code == "simulation_not_executed"


def test_unknown_engine_and_invalid_request_get_distinct_error_codes(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    unknown = schedule_run(session_factory, engine_name="engine-that-does-not-exist")
    invalid = schedule_run(session_factory, parameters={"initial_debt_to_gdp": 0.5})
    jobs = RecordingPublisher()
    service = OrchestratorService(session_factory, tmp_path, simulation_publisher=jobs)

    assert service.advance(unknown) is OrchestratorState.FAILED
    assert service.advance(invalid) is OrchestratorState.FAILED
    assert jobs.jobs == []

    with session_factory() as session:
        unknown_run = session.get(SimulationRun, uuid.UUID(unknown))
        invalid_run = session.get(SimulationRun, uuid.UUID(invalid))
        assert unknown_run is not None and invalid_run is not None
        assert unknown_run.error_code == "unknown_engine"
        assert invalid_run.error_code == "invalid_request"


def test_reconcile_resumes_a_run_interrupted_mid_pipeline(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    run_id = schedule_run(session_factory)
    jobs = RecordingPublisher()
    service = OrchestratorService(session_factory, tmp_path, simulation_publisher=jobs)
    service.store.transition(run_id, OrchestratorState.VALIDATING, reason="crash recovery")
    service.store.transition(run_id, OrchestratorState.MODEL_PLANNING, reason="crash recovery")

    resumed = service.reconcile()

    assert (run_id, OrchestratorState.SIMULATION_QUEUED) in resumed
    assert jobs.jobs == [run_id]
    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.completed_stages == [
            OrchestratorState.MODEL_PLANNING.value,
            OrchestratorState.DATA_PREPARATION.value,
            OrchestratorState.SIMULATION_QUEUED.value,
        ]
