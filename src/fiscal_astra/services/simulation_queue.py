"""Redis-backed Dramatiq producer; numerical work never runs in the API process."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.queue.broker import broker  # noqa: F401
from fiscal_astra.simulation.contracts import SimulationRequest


class DramatiqSimulationPublisher:
    """Publish a run id to the dedicated simulation queue."""

    def enqueue(self, run_id: str) -> str:
        from fiscal_astra.worker.tasks import execute_simulation

        message = execute_simulation.send(run_id)
        return str(message.message_id)


class DramatiqOrchestratorPublisher:
    """Publish a run id to the dedicated orchestrator queue."""

    def enqueue(self, run_id: str) -> str:
        from fiscal_astra.orchestrator.tasks import advance_run

        message = advance_run.send(run_id)
        return str(message.message_id)


class SimulationWorker:
    """Compatibility harness for tests; production uses the container worker."""

    def __init__(self, session_factory: sessionmaker[Session], artifact_root: str | Path) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root

    def submit(self, request: SimulationRequest) -> None:
        from fiscal_astra.services.audit_logger import AuditLogger
        from fiscal_astra.services.simulation_service import SimulationPersistenceService
        from fiscal_astra.worker.tasks import execute_simulation_run

        SimulationPersistenceService(
            self._session_factory, self._artifact_root, AuditLogger(self._session_factory)
        ).mark_submitted(request)
        execute_simulation_run(
            request.run_id,
            self._session_factory,
            SimulationPersistenceService(
                self._session_factory, self._artifact_root, AuditLogger(self._session_factory)
            ),
        )

    def join(self, timeout: float | None = None) -> None:
        """The compatibility execution is synchronous and already drained."""

    def shutdown(self, timeout: float | None = None) -> None:
        """No local thread exists; production shutdown is managed by Docker."""
