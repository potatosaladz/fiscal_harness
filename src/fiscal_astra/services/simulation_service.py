"""Persistence boundary between an in-memory engine result and the schema."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.database.enums import (
    AuditActionType,
    NumericalStatus,
    ProvenanceType,
    RunStage,
    RunStatus,
    SimulationModelStatus,
)
from fiscal_astra.database.models_simulation import (
    SimulationArtifact,
    SimulationModel,
    SimulationResultValue,
    SimulationRun,
)
from fiscal_astra.database.models_simulation import (
    SimulationResult as SimulationResultRow,
)
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.simulation.contracts import (
    ArtifactReference,
    ModelPlan,
    SimulationRequest,
    SimulationResult,
)


class SimulationPersistenceService:
    """Persist engine inputs, outputs, artifacts, and audit events.

    Writes are committed incrementally so a long-running worker keeps its
    progress even if the caller process is later killed: the run row is marked
    running before the engine is invoked and the numeric output is written as
    soon as the engine returns. The full request is stored on the run so the
    computation stays reproducible without the originating HTTP request.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        artifact_root: str | Path,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = Path(artifact_root)
        self._audit_logger = audit_logger or AuditLogger(session_factory)

    def mark_running(self, request: SimulationRequest, plan: ModelPlan) -> None:
        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            run.stage = RunStage.SIMULATING.value
            run.status = RunStatus.RUNNING.value
            run.started_at = datetime.now(UTC)
            run.random_seed = request.random_seed
            run.solver_settings = {
                **(run.solver_settings or {}),
                **request.solver_options,
                "engine_name": request.engine_name,
                "engine_version": request.engine_version,
                "requested_outputs": request.requested_outputs,
            }
            run.provenance = {
                **(run.provenance or {}),
                "request": request.model_dump(mode="json"),
                "model_plan": plan.model_dump(mode="json"),
            }
            run.error_message = None
            session.flush()
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="simulation_worker",
            actor_id=request.engine_name,
            action_type=AuditActionType.SIMULATION_STARTED,
            description=f"Simulation started for {request.baseline_or_policy}",
            input_refs=[
                {
                    "scenario_id": request.scenario_id,
                    "data_snapshot_ids": request.data_snapshot_ids,
                    "parameters": request.parameters,
                }
            ],
            output_refs=[{"plan_objective": plan.objective}],
        )

    def mark_submitted(self, request: SimulationRequest) -> None:
        """Write submission audit before enqueue attempt."""

        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            run.status = RunStatus.PENDING.value
            run.provenance = {
                **(run.provenance or {}),
                "request": request.model_dump(mode="json"),
            }
            session.flush()
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="api_submission",
            actor_id="run_service",
            action_type=AuditActionType.SIMULATION_SUBMITTED,
            description="Simulation submitted by API",
            input_refs=[{"engine": request.engine_name, "mode": request.baseline_or_policy}],
            next_action="enqueue_to_broker",
        )

    def mark_enqueue_failed(self, request: SimulationRequest, error: Exception) -> None:
        """Record enqueue failure without changing run status."""

        with self._session_factory() as session:
            run = self._require_run(session, request.run_id)
            persisted_run_id = run.id
        self._audit_logger.record_error(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="api_submission",
            actor_id="run_service",
            description="Failed to enqueue simulation to broker",
            error_info=[{"type": type(error).__name__, "message": str(error)}],
        )

    def mark_enqueued(self, request: SimulationRequest, job_id: str) -> None:
        """Record successful enqueue and store the broker message id."""

        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            run.status = RunStatus.QUEUED.value
            run.active_job_id = job_id
            session.flush()
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="api_submission",
            actor_id="run_service",
            action_type=AuditActionType.SIMULATION_ENQUEUED,
            description=f"Simulation enqueued with job_id={job_id}",
            output_refs=[{"job_id": job_id}],
            next_action="worker_claim",
        )

    def claim_for_validation(self, request: SimulationRequest) -> bool:
        """Atomically claim a pending/queued run and transition to validating."""

        with self._session_factory.begin() as session:
            run = session.scalars(
                select(SimulationRun)
                .where(SimulationRun.id == uuid.UUID(request.run_id))
                .with_for_update()
            ).one_or_none()
            if run is None:
                raise ValueError(f"SimulationRun not found: {request.run_id}")
            if run.status not in (RunStatus.PENDING.value, RunStatus.QUEUED.value):
                return False
            run.attempt_count += 1
            run.status = RunStatus.VALIDATING.value
            attempt = run.attempt_count
            session.flush()
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="simulation_worker",
            actor_id=request.engine_name,
            action_type=AuditActionType.VALIDATION_STARTED,
            description=f"Worker claimed run attempt={attempt}",
            next_action="validate_request",
        )
        return True

    def record_validation(self, request: SimulationRequest, validation: Any) -> None:
        """Persist validation outcome without changing run status yet."""

        with self._session_factory() as session:
            run = self._require_run(session, request.run_id)
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="simulation_worker",
            actor_id=request.engine_name,
            action_type=AuditActionType.VALIDATION_COMPLETED,
            description=f"Validation valid={validation.valid}",
            output_refs=[{"valid": validation.valid, "errors": validation.errors}],
        )

    def attempt_count(self, run_id: str) -> int:
        with self._session_factory() as session:
            run = self._require_run(session, run_id)
            return run.attempt_count

    def mark_retry_or_failure(self, request: SimulationRequest, error: Exception, tb: str) -> bool:
        """Mark failure with retry signal if attempts remain."""

        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            retry = run.attempt_count < run.max_attempts
            if retry:
                run.status = RunStatus.QUEUED.value
            else:
                run.status = RunStatus.FAILED.value
                run.finished_at = datetime.now(UTC)
                run.error_message = tb
            session.flush()
            persisted_run_id = run.id
        if retry:
            self._audit_logger.record(
                run_id=persisted_run_id,
                trace_id=request.run_id,
                actor_type="simulation_worker",
                actor_id=request.engine_name,
                action_type=AuditActionType.SIMULATION_RETRY_SCHEDULED,
                description=f"Retryable failure attempt={run.attempt_count}/{run.max_attempts}",
                error_info=[{"type": type(error).__name__, "message": str(error), "tb": tb}],
                next_action="dramatiq_retry",
            )
        else:
            self._audit_logger.record(
                run_id=persisted_run_id,
                trace_id=request.run_id,
                actor_type="simulation_worker",
                actor_id=request.engine_name,
                action_type=AuditActionType.SIMULATION_FAILED,
                description=f"Exhausted retries attempt={run.attempt_count}/{run.max_attempts}",
                error_info=[{"type": type(error).__name__, "message": str(error), "tb": tb}],
            )
        return retry

    def mark_terminal_exception(
        self, request: SimulationRequest, error: Exception, tb: str
    ) -> None:
        """Mark terminal error without retry."""

        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
            run.error_message = tb
            session.flush()
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="simulation_worker",
            actor_id=request.engine_name,
            action_type=AuditActionType.SIMULATION_FAILED,
            description=f"Terminal exception {type(error).__name__}",
            error_info=[{"type": type(error).__name__, "message": str(error), "tb": tb}],
        )

    def persist_result(
        self,
        request: SimulationRequest,
        result: SimulationResult,
        plan: ModelPlan,
    ) -> SimulationResult:
        if result.status == "converged":
            self._persist_success(request, result, plan)
        else:
            self._persist_failure(request, result)
        return result

    def store_log(self, request: SimulationRequest, lines: list[str]) -> ArtifactReference:
        directory = self._artifact_root / request.run_id
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "run_id": request.run_id,
                "engine": request.engine_name,
                "emitted_at": datetime.now(UTC).isoformat(),
                "lines": lines,
            },
            indent=2,
        ).encode("utf-8")
        path = directory / "simulation_log.json"
        path.write_bytes(payload)
        artifact = ArtifactReference(
            kind="log",
            location=str(path),
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            content_type="application/json",
        )
        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            model = session.execute(
                select(SimulationModel)
                .where(SimulationModel.run_id == run.id)
                .where(SimulationModel.name == request.engine_name)
            ).scalar_one_or_none()
            self._register_artifact(session, run, model, artifact)
        return artifact

    def load_result(self, run_id: str, name: str) -> dict[str, Any] | None:
        """Return a persisted series and its provenance for audit queries."""

        with self._session_factory() as session:
            statement = (
                select(SimulationResultRow)
                .where(SimulationResultRow.run_id == uuid.UUID(run_id))
                .where(SimulationResultRow.name == name)
            )
            persisted = session.execute(statement).scalar_one_or_none()
            if persisted is None:
                return None
            values = session.execute(
                select(SimulationResultValue.period, SimulationResultValue.value)
                .where(SimulationResultValue.result_id == persisted.id)
                .order_by(SimulationResultValue.period)
            ).all()
            return {
                "name": persisted.name,
                "unit": persisted.unit,
                "frequency": persisted.frequency,
                "horizon": persisted.horizon,
                "numeric_status": persisted.numeric_status,
                "provenance": persisted.provenance,
                "values": [(period, float(value)) for period, value in values],
            }

    def diagnostics(self, run_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            run = session.get(SimulationRun, uuid.UUID(run_id))
            if run is None:
                return None
            return {
                "stage": run.stage,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error_message": run.error_message,
                "solver_settings": run.solver_settings,
                "solver_diagnostics": run.solver_diagnostics,
            }

    def audit_events(self, run_id: str) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = json.loads(self._audit_logger.export_json(uuid.UUID(run_id)))
        return parsed

    def verify_audit_chain(self, run_id: str) -> bool:
        return self._audit_logger.verify_chain(uuid.UUID(run_id))

    def _persist_success(
        self,
        request: SimulationRequest,
        result: SimulationResult,
        plan: ModelPlan,
    ) -> None:
        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            run.stage = RunStage.REPORT_READY.value
            run.status = RunStatus.SUCCEEDED.value
            run.finished_at = datetime.now(UTC)
            run.solver_diagnostics = {
                "convergence": result.convergence,
                "diagnostics": result.diagnostics.model_dump(mode="json"),
            }
            run.error_message = None
            session.flush()

            model = SimulationModel(
                run_id=run.id,
                name=request.engine_name,
                version=request.engine_version,
                model_family=str(result.engine_metadata.get("model_family", "unknown")),
                status=SimulationModelStatus.CONVERGED.value,
                random_seed=request.random_seed,
                convergence_report=result.convergence,
                residual_report=result.diagnostics.solver,
                limitations=result.limitations,
            )
            session.add(model)
            session.flush()

            for name in request.requested_outputs:
                self._persist_series(session, run, model, request, result, name)
            for artifact in result.artifacts:
                self._register_artifact(session, run, model, artifact)
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="simulation_worker",
            actor_id=request.engine_name,
            action_type=AuditActionType.SIMULATION_COMPLETED,
            description="Simulation completed and results persisted",
            output_refs=[
                {
                    "status": result.status,
                    "metrics": sorted(result.output_metrics),
                    "series": sorted(result.time_series),
                }
            ],
            evidence_refs=[{"plan_equations": plan.equations}],
        )

    def _persist_series(
        self,
        session: Session,
        run: SimulationRun,
        model: SimulationModel,
        request: SimulationRequest,
        result: SimulationResult,
        name: str,
    ) -> None:
        points = result.time_series.get(name)
        if not points:
            return
        persisted = SimulationResultRow(
            run_id=run.id,
            model_id=model.id,
            name=name,
            label=name.replace("_", " ").title(),
            unit=points[0].get("unit"),
            frequency=request.frequency,
            horizon=request.horizon,
            numeric_status=NumericalStatus.VERIFIED_NUMERIC.value,
            method=str(result.engine_metadata.get("model_family", "unknown")),
            source_verification=NumericalStatus.VERIFIED_NUMERIC.value,
            uncertainty_low=result.confidence_interval.get("low"),
            uncertainty_high=result.confidence_interval.get("high"),
            uncertainty_distribution=result.confidence_interval.get("distribution"),
            uncertainty_basis=result.confidence_interval.get("basis"),
            as_of=datetime.now(UTC),
            provenance_type=ProvenanceType.MODEL_OUTPUT.value,
            provenance={
                "engine": request.engine_name,
                "engine_version": request.engine_version,
                "run_id": request.run_id,
                "scenario_id": request.scenario_id,
                "data_snapshot_ids": request.data_snapshot_ids,
                "random_seed": request.random_seed,
                "method": "computed_by_engine",
            },
        )
        session.add(persisted)
        session.flush()
        for point in points:
            session.add(
                SimulationResultValue(
                    result_id=persisted.id,
                    period=str(point["period"]),
                    value=float(point["value"]),
                )
            )
        session.flush()

    def _persist_failure(self, request: SimulationRequest, result: SimulationResult) -> None:
        with self._session_factory.begin() as session:
            run = self._require_run(session, request.run_id)
            not_executed = result.status == "not_executed"
            run.stage = RunStage.ERROR.value
            run.status = RunStatus.NOT_EXECUTED.value if not_executed else RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
            run.error_message = result.error or "Simulation failed without a message"
            session.flush()
            persisted_run_id = run.id
        self._audit_logger.record(
            run_id=persisted_run_id,
            trace_id=request.run_id,
            actor_type="simulation_worker",
            actor_id=request.engine_name,
            action_type=AuditActionType.SIMULATION_FAILED,
            description=(
                "Simulation not executed because validation failed"
                if result.status == "not_executed"
                else "Simulation failed"
            ),
            error_info=[{"type": "ValidationError", "message": result.error or "unknown"}],
        )

    def _register_artifact(
        self,
        session: Session,
        run: SimulationRun,
        model: SimulationModel | None,
        artifact: ArtifactReference,
    ) -> None:
        existing = session.execute(
            select(SimulationArtifact).where(
                SimulationArtifact.bucket == "local",
                SimulationArtifact.object_key == artifact.location,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        session.add(
            SimulationArtifact(
                run_id=run.id,
                model_id=model.id if model is not None else None,
                artifact_kind=artifact.kind,
                bucket="local",
                object_key=artifact.location,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                checksum_sha256=artifact.checksum_sha256,
                description="Engine-written simulation output",
            )
        )
        session.flush()

    def _require_run(self, session: Session, run_id: str) -> SimulationRun:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"SimulationRun not found: {run_id}")
        return run
