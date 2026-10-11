"""Detached FIFO worker queue for simulation jobs.

The queue owns its own daemon thread and database sessions, so a simulation
scheduled through it continues to completion after the HTTP request that
submitted it has returned and even if the browser or web process is closed.
Each state transition is committed and audited by the worker, not the caller.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import Diagnostics, SimulationRequest, SimulationResult
from fiscal_astra.simulation.registry import resolve_engine


@dataclass(frozen=True)
class SimulationJob:
    request: SimulationRequest


class SimulationWorker:
    """Background worker that drains a queue of simulation jobs."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        artifact_root: str | Path,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = Path(artifact_root)
        self._queue: queue.Queue[SimulationJob | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop,
            name="fiscal-astra-simulation-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, request: SimulationRequest) -> None:
        SimulationPersistenceService(
            self._session_factory,
            self._artifact_root,
            AuditLogger(self._session_factory),
        ).mark_submitted(request)
        self._queue.put(SimulationJob(request=request))

    def shutdown(self, timeout: float | None = None) -> None:
        self._queue.put(None)
        self._thread.join(timeout=timeout)

    def join(self, timeout: float | None = None) -> None:
        """Block until the queue is fully drained (used by tests)."""

        self._queue.join()

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._execute(job.request)
            finally:
                self._queue.task_done()

    def _execute(self, request: SimulationRequest) -> None:
        persistence = SimulationPersistenceService(
            self._session_factory,
            self._artifact_root,
            AuditLogger(self._session_factory),
        )
        engine = resolve_engine(request.engine_name, self._artifact_root)
        logs = [f"worker thread={threading.current_thread().name} picked up run"]
        try:
            validation = engine.validate_request(request)
            logs.append(f"validate_request valid={validation.valid}")
            if not validation.valid:
                failed = SimulationResult(
                    status="failed",
                    engine_metadata=engine.get_engine_metadata(),
                    input_provenance={
                        "run_id": request.run_id,
                        "scenario_id": request.scenario_id,
                    },
                    diagnostics=Diagnostics(
                        status="failed",
                        messages=validation.errors,
                    ),
                    convergence={"status": "failed"},
                    limitations=["Request validation failed; engine was not executed."],
                    error="; ".join(validation.errors),
                )
                persistence.persist_result(
                    request,
                    failed,
                    engine.build_model_plan(request),
                )
                return
            plan = engine.build_model_plan(request)
            persistence.mark_running(request, plan)
            result = engine.run(request)
            persistence.persist_result(request, result, plan)
            logs.append(f"status={result.status}")
        finally:
            try:
                persistence.store_log(request, logs)
            except OSError:
                pass


_worker: SimulationWorker | None = None
_lock = threading.Lock()


def get_worker(
    session_factory: sessionmaker[Session],
    artifact_root: str | Path,
) -> SimulationWorker:
    """Return the process-wide worker, creating it on first use."""

    global _worker
    with _lock:
        if _worker is None:
            _worker = SimulationWorker(session_factory, artifact_root)
        return _worker


def reset_worker_for_tests() -> None:
    """Stop and clear the process-wide worker (test helper)."""

    global _worker
    with _lock:
        if _worker is not None:
            _worker.shutdown(timeout=5)
            _worker = None
