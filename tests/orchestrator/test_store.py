"""Durability, audit, idempotency, and numeric guards for the state store."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fiscal_astra.database import (
    AuditEvent,
    Base,
    SimulationResult,
    SimulationRun,
)
from fiscal_astra.database.enums import NumericalStatus, OrchestratorState, ProvenanceType
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.orchestrator.states import OrchestratorError, OrchestratorErrorCode
from fiscal_astra.orchestrator.store import RunStateStore


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


def create_run(factory: sessionmaker[Session]) -> str:
    with factory.begin() as session:
        scenarios = ScenarioRepository(session)
        scenario = scenarios.create_scenario(f"orchestrator-{uuid.uuid4()}", "Orchestrator test")
        version = scenarios.create_version(scenario, 1, {}, [])
        run = SimulationRunRepository(session).create_run(version, "test")
        return str(run.id)


def test_transition_is_durable_audited_and_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    store = RunStateStore(session_factory)

    assert store.transition(
        run_id,
        OrchestratorState.VALIDATING,
        reason="unit test",
    )
    assert not store.transition(
        run_id,
        OrchestratorState.VALIDATING,
        reason="redelivery",
    )
    assert store.current_state(run_id) is OrchestratorState.VALIDATING

    with session_factory() as session:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.run_id == uuid.UUID(run_id))
        ).all()
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.correlation_id
        assert run.completed_stages == []
        assert len(events) == 1
        assert events[0].trace_id == run.correlation_id


def test_invalid_transition_has_code_and_does_not_write_audit(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    store = RunStateStore(session_factory)

    with pytest.raises(OrchestratorError) as error:
        store.transition(
            run_id,
            OrchestratorState.SIMULATING,
            reason="out of order",
        )
    assert error.value.code is OrchestratorErrorCode.INVALID_STATE_TRANSITION
    assert store.current_state(run_id) is OrchestratorState.RECEIVED

    with session_factory() as session:
        assert session.scalars(select(AuditEvent)).all() == []


def test_not_executed_result_cannot_enter_parameter_extraction(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    store = RunStateStore(session_factory)
    with session_factory.begin() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        run.orchestrator_state = OrchestratorState.SIMULATING.value
        session.add(
            SimulationResult(
                run_id=run.id,
                name="debt_to_gdp",
                numeric_status=NumericalStatus.NOT_EXECUTED.value,
                provenance_type=ProvenanceType.MODEL_OUTPUT.value,
                provenance={"status": "not_executed"},
            )
        )

    with pytest.raises(OrchestratorError) as error:
        store.transition(
            run_id,
            OrchestratorState.PARAMETER_EXTRACTION,
            reason="must be blocked",
        )
    assert error.value.code is OrchestratorErrorCode.NOT_EXECUTED_RESULT


def test_restart_resume_reads_persisted_position(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    first_process = RunStateStore(session_factory)
    first_process.transition(run_id, OrchestratorState.VALIDATING, reason="before restart")

    restarted_process = RunStateStore(session_factory)
    assert restarted_process.current_state(run_id) is OrchestratorState.VALIDATING
