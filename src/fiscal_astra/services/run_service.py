"""Transactional run creation and asynchronous queue submission."""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.database.enums import RunStatus
from fiscal_astra.database.models_scenario import Scenario, ScenarioVersion
from fiscal_astra.database.models_simulation import SimulationRun
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import SimulationRequest


class JobPublisher(Protocol):
    """Minimal producer boundary used by the HTTP layer."""

    def enqueue(self, run_id: str) -> str:
        """Enqueue a run and return the broker message identifier."""


class RunSubmissionService:
    """Create a durable run before sending its identifier to the broker."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        artifact_root: str,
        publisher: JobPublisher,
        max_attempts: int,
    ) -> None:
        self._session_factory = session_factory
        self._persistence = SimulationPersistenceService(
            session_factory, artifact_root, AuditLogger(session_factory)
        )
        self._publisher = publisher
        self._max_attempts = max_attempts

    def submit(self, request: SimulationRequest, label: str) -> SimulationRun:
        """Create the run, enqueue only its id, and return without executing an engine."""

        run_id = uuid.UUID(request.run_id)
        with self._session_factory.begin() as session:
            scenario, version = self._ensure_scenario_version(session, request)
            run = SimulationRunRepository(session).create_run(
                version,
                label,
                random_seed=request.random_seed,
                code_version=request.engine_version,
                run_id=run_id,
            )
            run.max_attempts = self._max_attempts
            run.status = RunStatus.PENDING.value
            run.provenance = {
                **(run.provenance or {}),
                "request": request.model_dump(mode="json"),
            }
            session.flush()

        self._persistence.mark_submitted(request)
        try:
            job_id = self._publisher.enqueue(request.run_id)
        except Exception as error:
            self._persistence.mark_enqueue_failed(request, error)
            raise
        self._persistence.mark_enqueued(request, job_id)
        return self.get(request.run_id)

    def get(self, run_id: str) -> SimulationRun:
        with self._session_factory() as session:
            run = session.get(SimulationRun, uuid.UUID(run_id))
            if run is None:
                raise ValueError(f"SimulationRun not found: {run_id}")
            session.expunge(run)
            return run

    @staticmethod
    def _ensure_scenario_version(
        session: Session, request: SimulationRequest
    ) -> tuple[Scenario, ScenarioVersion]:
        scenario_id = uuid.UUID(request.scenario_id)
        scenario = session.get(Scenario, scenario_id)
        if scenario is None:
            scenarios = ScenarioRepository(session)
            scenario = scenarios.create_scenario(
                slug=f"api-{scenario_id}",
                title=f"API simulation scenario {scenario_id}",
                provenance={"source": "POST /runs", "type": "scenario"},
                scenario_id=scenario_id,
            )
            version = scenarios.create_version(
                scenario,
                1,
                request.parameters,
                [],
                label=request.baseline_or_policy,
                provenance={"source": "POST /runs"},
            )
            return scenario, version
        latest_version: ScenarioVersion | None = ScenarioRepository(session).latest_version(
            scenario
        )
        if latest_version is None:
            created_version = ScenarioRepository(session).create_version(
                scenario,
                1,
                request.parameters,
                [],
                label=request.baseline_or_policy,
            )
            session.flush()
            latest_version = created_version
        return scenario, latest_version
