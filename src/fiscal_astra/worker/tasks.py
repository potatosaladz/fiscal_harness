"""Dramatiq actor that executes registered simulation engines outside HTTP."""

from __future__ import annotations

import traceback
from pathlib import Path

import dramatiq
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.cli.simulation import build_request_from_run
from fiscal_astra.config.settings import get_settings
from fiscal_astra.database.enums import RunStatus
from fiscal_astra.database.session import create_database_engine, create_session_factory
from fiscal_astra.queue.broker import broker  # noqa: F401
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import Diagnostics, SimulationResult
from fiscal_astra.simulation.registry import resolve_engine

settings = get_settings()


class RetryableSimulationError(RuntimeError):
    """Infrastructure failure that Dramatiq should retry."""


def _runtime() -> tuple[sessionmaker[Session], SimulationPersistenceService]:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    return factory, SimulationPersistenceService(
        factory,
        Path(settings.artifact_root),
        AuditLogger(factory),
    )


@dramatiq.actor(
    queue_name="simulations",
    max_retries=settings.simulation_max_attempts - 1,
    min_backoff=settings.simulation_retry_min_backoff_ms,
    max_backoff=settings.simulation_retry_max_backoff_ms,
    throws=(ValueError, KeyError),
)
def execute_simulation(run_id: str) -> None:
    """Claim, validate, execute, and persist one run idempotently."""

    factory, persistence = _runtime()
    execute_simulation_run(run_id, factory, persistence)


def execute_simulation_run(
    run_id: str,
    factory: sessionmaker[Session],
    persistence: SimulationPersistenceService,
) -> None:
    """Execute a run with injected persistence, used by the test harness."""

    request = build_request_from_run(factory, run_id)
    if not persistence.claim_for_validation(request):
        return
    logs = [f"worker claimed run attempt={persistence.attempt_count(run_id)}"]
    try:
        engine = resolve_engine(request.engine_name, settings.artifact_root)
        validation = engine.validate_request(request)
        persistence.record_validation(request, validation)
        logs.append(f"validation valid={validation.valid} errors={validation.errors}")
        if not validation.valid:
            failed = SimulationResult(
                status="not_executed",
                engine_metadata=engine.get_engine_metadata(),
                input_provenance={"run_id": run_id, "scenario_id": request.scenario_id},
                diagnostics=Diagnostics(status="not_executed", messages=validation.errors),
                convergence={"status": "not_executed"},
                limitations=["Request validation failed; numerical engine was not executed."],
                error="; ".join(validation.errors),
            )
            persistence.persist_result(request, failed, engine.build_model_plan(request))
            return

        plan = engine.build_model_plan(request)
        persistence.mark_running(request, plan)
        result = engine.run(request)
        if result.status == "converged":
            result_validation = engine.validate_result(request, result)
            if not result_validation.valid:
                result.status = "failed"
                result.error = "; ".join(result_validation.errors)
                result.diagnostics.messages.extend(result_validation.errors)
        persistence.persist_result(request, result, plan)
        logs.append(f"engine status={result.status}")
    except (ValueError, KeyError) as error:
        persistence.mark_terminal_exception(request, error, traceback.format_exc())
    except Exception as error:
        retry = persistence.mark_retry_or_failure(request, error, traceback.format_exc())
        if retry:
            raise RetryableSimulationError(str(error)) from error
    finally:
        try:
            persistence.store_log(request, logs)
        except OSError:
            pass


def terminal_statuses() -> set[str]:
    """Expose terminal states for tests and operational tooling."""

    return {
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.NOT_EXECUTED.value,
    }
