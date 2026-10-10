"""Unit and integration tests for the append-only audit logger."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.database import AuditEvent, Base
from fiscal_astra.database.enums import AuditActionType
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.services.audit_logger import AuditLogger, calculate_event_hash


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def create_run(factory: sessionmaker[Session]) -> uuid.UUID:
    with factory.begin() as session:
        scenario_repository = ScenarioRepository(session)
        scenario = scenario_repository.create_scenario("audit-run", "Audit run")
        version = scenario_repository.create_version(scenario, 1, {}, [])
        run = SimulationRunRepository(session).create_run(version, "baseline")
        return run.id


def test_logger_appends_hash_chained_events_and_queries_by_run(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    logger = AuditLogger(session_factory)
    trace_id = str(uuid.uuid4())

    first = logger.record(
        run_id=run_id,
        trace_id=trace_id,
        actor_type="orchestrator",
        actor_id="astra",
        action_type=AuditActionType.INPUT_RECEIVED,
        description="Input received",
        input_refs=[{"scenario_id": str(run_id)}],
        assumption_refs=[{"id": "assumption-1"}],
        confidence="high",
    )
    second = logger.record(
        run_id=run_id,
        trace_id=trace_id,
        actor_type="validator",
        actor_id="validator-1",
        action_type=AuditActionType.VALIDATION_COMPLETED,
        description="Validation completed",
        output_refs=[{"validation": "passed"}],
        evidence_refs=[{"evidence_id": "evidence-1"}],
    )

    events = logger.list_for_run(run_id)
    assert [event.sequence for event in events] == [1, 2]
    assert first.trace_id == second.trace_id == trace_id
    assert first.previous_event_hash == ""
    assert second.previous_event_hash == first.current_event_hash
    assert first.current_event_hash == calculate_event_hash(first)
    assert second.current_event_hash == calculate_event_hash(second)
    assert logger.verify_chain(run_id)


def test_logger_redacts_secrets_and_exports_json(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    logger = AuditLogger(session_factory)

    logger.record(
        run_id=run_id,
        trace_id="trace-secret-test",
        actor_type="tool",
        actor_id="api_key=do-not-store",
        action_type=AuditActionType.DATA_RETRIEVED,
        description="Authorization: Bearer super-secret-token",
        input_refs=[{"api_key": "do-not-store", "url": "https://example.test"}],
        error_info=[{"message": "password=hunter2"}],
    )

    exported = json.loads(logger.export_json(run_id))
    assert exported[0]["actor_id"] == "[REDACTED]"
    assert "super-secret-token" not in exported[0]["description"]
    assert exported[0]["input_refs"][0]["api_key"] == "[REDACTED]"
    assert exported[0]["error_info"][0]["message"] == "[REDACTED]"


def test_error_event_survives_failed_domain_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    logger = AuditLogger(session_factory)

    with pytest.raises(RuntimeError, match="worker failed"):
        try:
            with session_factory.begin() as session:
                session.execute(select(AuditEvent))
                raise RuntimeError("worker failed")
        except RuntimeError as error:
            logger.record_error(
                run_id=run_id,
                trace_id="trace-failure",
                actor_type="simulation_worker",
                actor_id="worker-1",
                description="Simulation failed",
                error_info=[{"type": type(error).__name__, "message": str(error)}],
            )
            raise

    events = logger.list_for_run(run_id)
    assert len(events) == 1
    assert events[0].action_type == AuditActionType.ERROR
    assert events[0].error_info == [{"type": "RuntimeError", "message": "worker failed"}]


def test_audit_events_cannot_be_updated_or_deleted(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = create_run(session_factory)
    logger = AuditLogger(session_factory)
    logger.record(
        run_id=run_id,
        trace_id="trace-append-only",
        actor_type="test",
        actor_id="test",
        action_type=AuditActionType.INPUT_RECEIVED,
        description="immutable",
    )

    with pytest.raises(ValueError, match="append-only"):
        with session_factory.begin() as session:
            event = session.scalars(select(AuditEvent)).one()
            event.description = "mutated"
            session.flush()

    with pytest.raises(ValueError, match="append-only"):
        with session_factory.begin() as session:
            event = session.scalars(select(AuditEvent)).one()
            session.delete(event)
            session.flush()

    assert logger.verify_chain(run_id)
