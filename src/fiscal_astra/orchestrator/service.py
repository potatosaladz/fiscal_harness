"""Stage driver for the orchestrator pipeline.

The service walks one run through the pipeline defined in
``01-orchestrator.md``. It plans, dispatches to the simulation queue, and
interprets persisted engine output; it never computes a fiscal or macroeconomic
number itself. Every stage is resumable: the durable state plus the recorded
completed stages decide what still has to happen, so a restarted container
continues instead of repeating work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.cli.simulation import build_request_from_run
from fiscal_astra.database.enums import (
    ArtifactKind,
    AuditActionType,
    OrchestratorState,
    RunStatus,
)
from fiscal_astra.database.models_simulation import SimulationResult, SimulationRun
from fiscal_astra.orchestrator.states import (
    OrchestratorError,
    OrchestratorErrorCode,
    is_terminal,
    successor,
)
from fiscal_astra.orchestrator.store import RunStateStore
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.run_service import JobPublisher
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import ArtifactReference, SimulationRequest
from fiscal_astra.simulation.protocols import SimulationEngine
from fiscal_astra.simulation.registry import resolve_engine

logger = logging.getLogger(__name__)


class StageOutcome(StrEnum):
    """What a finished stage means for the pipeline."""

    CONTINUE = "continue"
    """The stage's work is complete; the pipeline may advance."""

    WAITING = "waiting"
    """The stage cannot progress until another process moves the run."""


_Handler = Callable[[str], StageOutcome]


class OrchestratorService:
    """Advance runs through the durable orchestrator state machine."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        artifact_root: str | Path,
        simulation_publisher: JobPublisher | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = Path(artifact_root)
        self._audit = audit_logger or AuditLogger(session_factory)
        self._store = RunStateStore(session_factory, self._audit)
        self._persistence = SimulationPersistenceService(
            session_factory, self._artifact_root, self._audit
        )
        self._simulation_publisher = simulation_publisher
        self._handlers: dict[OrchestratorState, _Handler] = {
            OrchestratorState.RECEIVED: self._stage_received,
            OrchestratorState.VALIDATING: self._stage_validating,
            OrchestratorState.MODEL_PLANNING: self._stage_model_planning,
            OrchestratorState.DATA_PREPARATION: self._stage_data_preparation,
            OrchestratorState.SIMULATION_QUEUED: self._stage_simulation_queued,
            OrchestratorState.SIMULATING: self._stage_simulating,
            OrchestratorState.PARAMETER_EXTRACTION: self._stage_parameter_extraction,
            OrchestratorState.POLICY_AGENT_ANALYSIS: self._stage_policy_agent_analysis,
            OrchestratorState.DELIBERATION: self._stage_deliberation,
            OrchestratorState.EVIDENCE_VERIFICATION: self._stage_evidence_verification,
            OrchestratorState.REPORT_GENERATION: self._stage_report_generation,
        }

    @property
    def store(self) -> RunStateStore:
        """Expose the durable state store for callers that only need to read."""

        return self._store

    def advance(self, run_id: str) -> OrchestratorState:
        """Drive one run as far as its durable state allows.

        Safe to call repeatedly: a run already at a terminal state returns
        immediately, and each stage is guarded by the recorded completed stages.
        A domain failure moves the run to ``FAILED`` with an error code; an
        infrastructure failure is audited and re-raised so the broker retries.
        """

        while True:
            state = self._store.current_state(run_id)
            if is_terminal(state):
                return state

            handler = self._handlers.get(state)
            if handler is None:
                return self._store.fail(
                    run_id,
                    OrchestratorError(
                        OrchestratorErrorCode.INTERNAL_ERROR,
                        f"No stage handler registered for state {state.value}",
                    ),
                )

            try:
                outcome = handler(run_id)
            except OrchestratorError as error:
                return self._store.fail(run_id, error)
            except Exception as error:
                self._record_retryable_failure(run_id, error)
                raise

            self._store.mark_stage_completed(run_id, state)
            if outcome is StageOutcome.WAITING:
                return state

            target = successor(state)
            if target is None:
                return state
            try:
                self._store.transition(
                    run_id,
                    target,
                    reason=f"stage {state.value} finished",
                    next_action=target.value,
                )
            except OrchestratorError as error:
                return self._store.fail(run_id, error)

    def reconcile(self, limit: int = 200) -> list[tuple[str, OrchestratorState]]:
        """Resume every non-terminal run; used after a restart.

        Runs that are legitimately waiting for the simulation worker simply
        return their current state, so reconciliation never restarts numerical
        work that another process may still be executing.
        """

        resumed: list[tuple[str, OrchestratorState]] = []
        for run_id in self._store.open_runs(limit):
            try:
                resumed.append((run_id, self.advance(run_id)))
            except Exception:  # pragma: no cover - boot must survive one bad run
                logger.warning("orchestrator reconcile failed for run %s", run_id, exc_info=True)
        return resumed

    def claim_simulation(self, run_id: str) -> bool:
        """Move a queued run to ``SIMULATING`` when the worker picks it up.

        Returns ``False`` when the run was not waiting in ``SIMULATION_QUEUED``
        (a legacy scheduled run, or a redelivered message), which keeps the
        transition table authoritative for every writer.
        """

        if self._store.current_state(run_id) is not OrchestratorState.SIMULATION_QUEUED:
            return False
        return self._store.transition(
            run_id,
            OrchestratorState.SIMULATING,
            reason="simulation worker claimed the run",
            actor_type="simulation_worker",
            actor_id="simulation_worker",
            next_action="execute_engine",
        )

    # ------------------------------------------------------------------ stages

    def _stage_received(self, run_id: str) -> StageOutcome:
        """The run row and its request manifest already exist; nothing to do."""

        return StageOutcome.CONTINUE

    def _stage_validating(self, run_id: str) -> StageOutcome:
        request = self._request(run_id)
        self._record(
            run_id,
            AuditActionType.VALIDATION_STARTED,
            "Validating the stored simulation request and engine availability",
            next_action="validate_request",
        )
        engine = self._engine(request)
        validation = engine.validate_request(request)
        self._record(
            run_id,
            AuditActionType.VALIDATION_COMPLETED,
            f"Request validation completed valid={validation.valid}",
            output_refs=[
                {
                    "valid": validation.valid,
                    "errors": list(validation.errors),
                    "warnings": list(validation.warnings),
                }
            ],
            next_action="model_planning" if validation.valid else "failed",
        )
        if not validation.valid:
            raise OrchestratorError(
                OrchestratorErrorCode.INVALID_REQUEST, "; ".join(validation.errors)
            )
        return StageOutcome.CONTINUE

    def _stage_model_planning(self, run_id: str) -> StageOutcome:
        request = self._request(run_id)
        plan = self._engine(request).build_model_plan(request)
        self._merge_run_json(
            run_id,
            "provenance",
            {"model_plan": plan.model_dump(mode="json")},
        )
        self._record(
            run_id,
            AuditActionType.MODEL_PLAN_CREATED,
            f"Model plan created for {plan.engine_name} {plan.engine_version}",
            output_refs=[
                {
                    "objective": plan.objective,
                    "equations": plan.equations,
                    "state_variables": plan.state_variables,
                    "validation": plan.validation,
                    "limitations": plan.limitations,
                }
            ],
            next_action="data_preparation",
        )
        return StageOutcome.CONTINUE

    def _stage_data_preparation(self, run_id: str) -> StageOutcome:
        """Record the data requirements actually attached to the request.

        This stage never invents a snapshot: it reports the snapshot ids the
        request references, or states that the engine relies on its own
        calibration inputs.
        """

        request = self._request(run_id)
        if request.data_snapshot_ids:
            self._record(
                run_id,
                AuditActionType.DATA_REQUESTED,
                "Data snapshots referenced by the request were attached to the run manifest",
                output_refs=[
                    {
                        "data_snapshot_ids": request.data_snapshot_ids,
                        "verification": "deferred_to_evidence_stage",
                    }
                ],
                next_action="simulation_queued",
            )
        else:
            self._record(
                run_id,
                AuditActionType.STAGE_SKIPPED,
                (
                    "No external data snapshot was attached to the request; the engine runs "
                    "from its own declared calibration inputs"
                ),
                next_action="simulation_queued",
            )
        return StageOutcome.CONTINUE

    def _stage_simulation_queued(self, run_id: str) -> StageOutcome:
        """Publish exactly one simulation job for this run, then wait."""

        run = self._store.snapshot(run_id)
        if run.status == RunStatus.SUCCEEDED.value:
            return StageOutcome.CONTINUE
        if run.status == RunStatus.NOT_EXECUTED.value:
            raise OrchestratorError(
                OrchestratorErrorCode.SIMULATION_NOT_EXECUTED,
                self._short(run.error_message) or "Simulation was not executed",
            )
        if run.status == RunStatus.FAILED.value:
            raise OrchestratorError(
                OrchestratorErrorCode.SIMULATION_FAILED,
                self._short(run.error_message) or "Simulation failed",
            )

        # ponytail: the guard is advisory, not a distributed lock. A crash
        # between `send` and recording the job id can publish one duplicate
        # message; the worker's row-lock claim discards it, so no second
        # numerical execution happens.
        if self._store.stage_completed(run_id, OrchestratorState.SIMULATION_QUEUED):
            return StageOutcome.WAITING
        if (run.solver_settings or {}).get("simulation_job_id"):
            return StageOutcome.WAITING

        publisher = self._simulation_publisher
        if publisher is None:
            raise OrchestratorError(
                OrchestratorErrorCode.ENQUEUE_FAILED,
                "No simulation queue publisher is configured for the orchestrator",
            )
        job_id = publisher.enqueue(run_id)
        self._merge_run_json(run_id, "solver_settings", {"simulation_job_id": job_id})
        self._record(
            run_id,
            AuditActionType.SIMULATION_ENQUEUED,
            f"Simulation job enqueued with job_id={job_id}",
            output_refs=[{"job_id": job_id}],
            next_action="worker_claim",
        )
        return StageOutcome.WAITING

    def _stage_simulating(self, run_id: str) -> StageOutcome:
        """Wait for the worker, or report its terminal outcome."""

        run = self._store.snapshot(run_id)
        if run.status == RunStatus.SUCCEEDED.value:
            return StageOutcome.CONTINUE
        if run.status == RunStatus.NOT_EXECUTED.value:
            raise OrchestratorError(
                OrchestratorErrorCode.SIMULATION_NOT_EXECUTED,
                self._short(run.error_message) or "Simulation was not executed",
            )
        if run.status == RunStatus.FAILED.value:
            raise OrchestratorError(
                OrchestratorErrorCode.SIMULATION_FAILED,
                self._short(run.error_message) or "Simulation failed",
            )
        return StageOutcome.WAITING

    def _stage_parameter_extraction(self, run_id: str) -> StageOutcome:
        """Collect the fiscal parameters the engine actually produced.

        Only persisted ``verified_numeric`` output is read. Derived parameters
        that need a dedicated extraction engine (fiscal multiplier, tax
        elasticity, fiscal gap) are recorded as ``not_executed`` rather than
        estimated here.
        """

        results = self._store.verified_results(run_id)
        if not results:
            raise OrchestratorError(
                OrchestratorErrorCode.MISSING_SIMULATION_RESULT,
                "Parameter extraction requires a persisted verified_numeric simulation result",
            )
        engine_outputs = [self._result_descriptor(result) for result in results]
        payload: dict[str, Any] = {
            "status": "read_from_engine_output",
            "source": "simulation_results",
            "engine_outputs": engine_outputs,
            "derived_parameters": {
                "status": "not_executed",
                "reason": (
                    "Derived fiscal parameters (fiscal multiplier, tax elasticity, "
                    "fiscal gap, debt-stabilizing primary balance) require a "
                    "parameter-extraction engine; none is registered in this deployment."
                ),
            },
        }
        self._merge_run_json(run_id, "provenance", {"parameter_extraction": payload})
        self._record(
            run_id,
            AuditActionType.COMPUTATION_COMPLETED,
            f"Fiscal parameter extraction read {len(engine_outputs)} engine output(s)",
            output_refs=engine_outputs,
            next_action="policy_agent_analysis",
        )
        return StageOutcome.CONTINUE

    def _stage_policy_agent_analysis(self, run_id: str) -> StageOutcome:
        return self._skip_unimplemented_stage(
            run_id,
            "policy_agent_analysis",
            (
                "Policy expert sub-agent analysis was not executed: no local LLM runtime is "
                "configured, so no independent agent position is claimed"
            ),
        )

    def _stage_deliberation(self, run_id: str) -> StageOutcome:
        return self._skip_unimplemented_stage(
            run_id,
            "deliberation",
            (
                "Deliberation was not executed because no agent position was produced; "
                "no consensus and no disagreement are claimed"
            ),
        )

    def _stage_evidence_verification(self, run_id: str) -> StageOutcome:
        return self._skip_unimplemented_stage(
            run_id,
            "evidence_verification",
            (
                "Evidence verification was not executed because no claim was produced by the "
                "policy agents; the simulation output provenance remains the only verified record"
            ),
        )

    def _stage_report_generation(self, run_id: str) -> StageOutcome:
        run = self._store.snapshot(run_id)
        results = [
            self._result_descriptor(result) for result in self._store.verified_results(run_id)
        ]
        completed = list(run.completed_stages or [])
        skipped = [
            stage.value
            for stage in self._handlers
            if stage.value not in completed and stage is not OrchestratorState.REPORT_GENERATION
        ]
        report: dict[str, Any] = {
            "run_id": str(run.id),
            "correlation_id": run.correlation_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "label": run.label,
            "orchestrator_state": run.orchestrator_state,
            "run_status": run.status,
            "run_stage": run.stage,
            "completed_stages": list(run.completed_stages or []),
            "skipped_stages": sorted(skipped),
            "simulation": {
                "engine": run.code_version,
                "random_seed": run.random_seed,
                "solver_settings": run.solver_settings,
                "diagnostics": run.solver_diagnostics,
            },
            "results": results,
            "recommendations": {
                "status": "not_executed",
                "reason": (
                    "Recommendations require the policy agent, deliberation, and evidence "
                    "verification stages; this deployment did not execute them."
                ),
            },
            "limitations": [
                "Local LLM policy reasoning is not enabled in this deployment.",
                "Derived fiscal parameters are not_executed; only engine output is reported.",
                "This document is decision support, not a policy decision.",
            ],
        }
        artifact = self._write_report_artifact(run_id, report)
        self._record(
            run_id,
            AuditActionType.REPORT_GENERATED,
            "Orchestrator report artifact generated",
            output_refs=[
                {
                    "artifact": artifact.location,
                    "checksum_sha256": artifact.checksum_sha256,
                    "skipped_stages": sorted(skipped),
                }
            ],
            next_action="completed",
        )
        return StageOutcome.CONTINUE

    # ----------------------------------------------------------------- helpers

    def _skip_unimplemented_stage(self, run_id: str, key: str, description: str) -> StageOutcome:
        """Record an honest ``not_executed`` marker instead of fabricating output."""

        self._merge_run_json(
            run_id,
            "provenance",
            {key: {"status": "not_executed", "reason": description}},
        )
        self._record(
            run_id,
            AuditActionType.STAGE_SKIPPED,
            description,
            next_action="next_stage",
        )
        return StageOutcome.CONTINUE

    def _request(self, run_id: str) -> SimulationRequest:
        try:
            return build_request_from_run(self._session_factory, run_id)
        except ValueError as error:
            raise OrchestratorError(OrchestratorErrorCode.INVALID_REQUEST, str(error)) from error

    def _engine(self, request: SimulationRequest) -> SimulationEngine:
        try:
            return resolve_engine(request.engine_name, self._artifact_root)
        except KeyError as error:
            raise OrchestratorError(OrchestratorErrorCode.UNKNOWN_ENGINE, str(error)) from error

    def _record(
        self,
        run_id: str,
        action_type: AuditActionType,
        description: str,
        *,
        input_refs: list[Any] | None = None,
        output_refs: list[Any] | None = None,
        next_action: str | None = None,
    ) -> None:
        run = self._store.snapshot(run_id)
        self._audit.record(
            run_id=run.id,
            trace_id=run.correlation_id,
            actor_type="orchestrator",
            actor_id="orchestrator",
            action_type=action_type,
            description=description,
            input_refs=input_refs or [],
            output_refs=output_refs or [],
            next_action=next_action,
        )

    def _record_retryable_failure(self, run_id: str, error: Exception) -> None:
        """Audit an infrastructure failure without making the run terminal."""

        try:
            run = self._store.snapshot(run_id)
        except Exception:  # pragma: no cover - the run row itself is unavailable
            logger.warning("could not audit orchestrator failure for %s", run_id, exc_info=True)
            return
        self._audit.record_error(
            run_id=run.id,
            trace_id=run.correlation_id,
            actor_type="orchestrator",
            actor_id="orchestrator",
            description=f"Orchestrator stage raised {type(error).__name__}; retry requested",
            error_info=[
                {
                    "error_code": OrchestratorErrorCode.INTERNAL_ERROR.value,
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": self._short(traceback.format_exc(), 4000),
                }
            ],
            next_action="retry",
        )

    def _merge_run_json(self, run_id: str, column: str, payload: dict[str, Any]) -> None:
        with self._session_factory.begin() as session:
            run = self._require_run(session, run_id)
            merged = dict(getattr(run, column) or {})
            merged.update(payload)
            setattr(run, column, merged)
            session.flush()

    def _write_report_artifact(self, run_id: str, report: dict[str, Any]) -> ArtifactReference:
        directory = self._artifact_root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
        path = directory / "orchestrator_report.json"
        path.write_bytes(payload)
        artifact = ArtifactReference(
            kind=ArtifactKind.REPORT.value,
            location=str(path),
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            content_type="application/json",
        )
        self._persistence.register_local_artifact(
            run_id, artifact, description="Orchestrator report artifact"
        )
        return artifact

    @staticmethod
    def _result_descriptor(result: SimulationResult) -> dict[str, Any]:
        """Describe a persisted result without deriving a new number."""

        return {
            "name": result.name,
            "label": result.label,
            "unit": result.unit,
            "frequency": result.frequency,
            "horizon": result.horizon,
            "numeric_status": result.numeric_status,
            "method": result.method,
            "uncertainty": {
                "low": result.uncertainty_low,
                "high": result.uncertainty_high,
                "distribution": result.uncertainty_distribution,
                "basis": result.uncertainty_basis,
            },
            "provenance": result.provenance,
        }

    @staticmethod
    def _require_run(session: Session, run_id: str) -> SimulationRun:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        if run is None:
            raise OrchestratorError(
                OrchestratorErrorCode.RUN_NOT_FOUND, f"SimulationRun not found: {run_id}"
            )
        return run

    @staticmethod
    def _short(text: str | None, limit: int = 2000) -> str | None:
        if text is None:
            return None
        return text if len(text) <= limit else f"{text[:limit]}... [truncated]"


def build_orchestrator_service(
    session_factory: sessionmaker[Session] | None = None,
) -> OrchestratorService:
    """Compose the orchestrator with its durable simulation-queue publisher.

    The publisher is the only path from the orchestrator to numerical work, so
    the orchestrator process never imports or executes a simulation engine.
    """

    from fiscal_astra.config.settings import get_settings
    from fiscal_astra.database.session import create_database_engine, create_session_factory
    from fiscal_astra.services.simulation_queue import DramatiqSimulationPublisher

    settings = get_settings()
    factory = session_factory or create_session_factory(create_database_engine(settings))
    return OrchestratorService(
        factory,
        settings.artifact_root,
        simulation_publisher=DramatiqSimulationPublisher(),
    )
