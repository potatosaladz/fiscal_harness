"""Durable persistence of orchestrator state transitions and stage progress.

The store is the only writer of ``simulation_runs.orchestrator_state``. Each
transition is validated, persisted, and then announced with exactly one
append-only audit event, so the audit chain is a complete record of the
pipeline. A transition to a state the run already occupies is a no-op, which is
what makes broker redelivery and restart recovery safe.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.database.enums import (
    AuditActionType,
    NumericalStatus,
    OrchestratorState,
    RunStatus,
)
from fiscal_astra.database.models_simulation import (
    SimulationResult,
    SimulationRun,
)
from fiscal_astra.orchestrator.states import (
    STAGES_REQUIRING_VERIFIED_RESULT,
    TERMINAL_STATES,
    OrchestratorError,
    OrchestratorErrorCode,
    RunStateMachine,
    is_terminal,
)
from fiscal_astra.services.audit_logger import AuditLogger


class RunStateStore:
    """Read and write the durable orchestrator state of a run."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._audit = audit_logger or AuditLogger(session_factory)

    def current_state(self, run_id: str) -> OrchestratorState:
        """Return the persisted orchestrator state of a run."""

        with self._session_factory() as session:
            run = self._load(session, run_id)
            return OrchestratorState(run.orchestrator_state)

    def snapshot(self, run_id: str) -> SimulationRun:
        """Return a detached copy of the run row."""

        with self._session_factory() as session:
            run = self._load(session, run_id)
            session.expunge(run)
            return run

    def transition(
        self,
        run_id: str,
        target: OrchestratorState,
        *,
        reason: str,
        actor_type: str = "orchestrator",
        actor_id: str = "orchestrator",
        next_action: str | None = None,
        input_refs: list[Any] | None = None,
        output_refs: list[Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Persist one validated transition and append its audit event.

        Returns ``False`` when the run already is in ``target``; returns ``True``
        when the transition was applied. Raises
        :class:`~fiscal_astra.orchestrator.states.OrchestratorError` when the
        transition is illegal, and in that case nothing is written.
        """

        with self._session_factory.begin() as session:
            run = self._lock(session, run_id)
            current = OrchestratorState(run.orchestrator_state)
            if current is target:
                return False

            machine = RunStateMachine(current)
            # Graph legality first so an out-of-order request is always reported
            # as such; the numeric gate below then reports a precise code.
            machine.validate_shape(target)
            self._require_numeric_result(session, run, target)

            run.orchestrator_state = target.value
            if target is OrchestratorState.FAILED:
                run.error_code = error_code or OrchestratorErrorCode.INTERNAL_ERROR.value
            if target in TERMINAL_STATES:
                run.finished_at = run.finished_at or datetime.now(UTC)
            session.flush()
            correlation_id = run.correlation_id
            persisted_id = run.id

        self._audit.record(
            run_id=persisted_id,
            trace_id=correlation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=AuditActionType.STATE_TRANSITION,
            description=f"{current.value} -> {target.value}: {reason}",
            input_refs=[{"from": current.value, "to": target.value}, *(input_refs or [])],
            output_refs=[{"orchestrator_state": target.value}, *(output_refs or [])],
            error_info=[{"error_code": error_code}] if error_code else [],
            next_action=next_action or target.value,
        )
        return True

    def fail(
        self,
        run_id: str,
        error: OrchestratorError,
        *,
        actor_type: str = "orchestrator",
        actor_id: str = "orchestrator",
    ) -> OrchestratorState:
        """Move a non-terminal run to ``FAILED`` with its error code preserved."""

        with self._session_factory() as session:
            state = OrchestratorState(self._load(session, run_id).orchestrator_state)
        if is_terminal(state):
            return state

        changed = self.transition(
            run_id,
            OrchestratorState.FAILED,
            reason=str(error),
            actor_type=actor_type,
            actor_id=actor_id,
            next_action="failed",
            error_code=error.code.value,
        )
        if changed:
            self._audit.record_error(
                run_id=uuid.UUID(run_id),
                trace_id=self._correlation_id(run_id),
                actor_type=actor_type,
                actor_id=actor_id,
                description=f"Orchestrator failure: {error.code.value}",
                error_info=[{"error_code": error.code.value, "message": str(error)}],
                next_action="failed",
            )
        return OrchestratorState.FAILED

    def mark_stage_completed(self, run_id: str, stage: OrchestratorState) -> None:
        """Record that a stage's work finished, once and only once."""

        with self._session_factory.begin() as session:
            run = self._lock(session, run_id)
            completed = list(run.completed_stages or [])
            if stage.value in completed:
                return
            completed.append(stage.value)
            run.completed_stages = completed
            session.flush()

    def stage_completed(self, run_id: str, stage: OrchestratorState) -> bool:
        """Return whether a stage's work already ran for this run."""

        with self._session_factory() as session:
            run = self._load(session, run_id)
            return stage.value in list(run.completed_stages or [])

    def verified_results(self, run_id: str) -> list[SimulationResult]:
        """Return persisted results whose ``numeric_status`` is verified numeric."""

        with self._session_factory() as session:
            rows = session.scalars(
                select(SimulationResult)
                .where(SimulationResult.run_id == uuid.UUID(run_id))
                .order_by(SimulationResult.name)
            ).all()
            return [
                row for row in rows if row.numeric_status == NumericalStatus.VERIFIED_NUMERIC.value
            ]

    def open_runs(self, limit: int = 200) -> list[str]:
        """Return ids of runs that have not reached a terminal state, oldest first."""

        terminal = [state.value for state in TERMINAL_STATES]
        with self._session_factory() as session:
            rows = session.execute(
                select(SimulationRun.id)
                .where(SimulationRun.orchestrator_state.not_in(terminal))
                .order_by(SimulationRun.created_at)
                .limit(limit)
            ).all()
            return [str(row[0]) for row in rows]

    @staticmethod
    def _require_numeric_result(
        session: Session, run: SimulationRun, target: OrchestratorState
    ) -> None:
        """Refuse to enter a numeric-dependent state without a real engine result."""

        if target not in STAGES_REQUIRING_VERIFIED_RESULT:
            return
        rows = session.scalars(
            select(SimulationResult).where(SimulationResult.run_id == run.id)
        ).all()
        if not rows:
            raise OrchestratorError(
                OrchestratorErrorCode.MISSING_SIMULATION_RESULT,
                (
                    f"Run {run.id} has no persisted simulation result; state "
                    f"{target.value} requires an executed numerical engine"
                ),
            )
        if not any(row.numeric_status == NumericalStatus.VERIFIED_NUMERIC.value for row in rows):
            statuses = sorted({row.numeric_status for row in rows})
            raise OrchestratorError(
                OrchestratorErrorCode.NOT_EXECUTED_RESULT,
                (
                    f"Run {run.id} has no verified numeric result (numeric_status={statuses}); "
                    f"state {target.value} must not consume not_executed output"
                ),
            )

    def _correlation_id(self, run_id: str) -> str:
        with self._session_factory() as session:
            return str(self._load(session, run_id).correlation_id)

    @staticmethod
    def _load(session: Session, run_id: str) -> SimulationRun:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        if run is None:
            raise OrchestratorError(
                OrchestratorErrorCode.RUN_NOT_FOUND, f"SimulationRun not found: {run_id}"
            )
        return run

    def _lock(self, session: Session, run_id: str) -> SimulationRun:
        run = session.scalars(
            select(SimulationRun).where(SimulationRun.id == uuid.UUID(run_id)).with_for_update()
        ).one_or_none()
        if run is None:
            raise OrchestratorError(
                OrchestratorErrorCode.RUN_NOT_FOUND, f"SimulationRun not found: {run_id}"
            )
        return run


TERMINAL_SIMULATION_STATUSES: frozenset[str] = frozenset(
    {
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.NOT_EXECUTED.value,
        RunStatus.CANCELLED.value,
    }
)
