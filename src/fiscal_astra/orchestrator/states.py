"""The orchestrator state machine: states, transitions, guards, error codes.

The state machine describes *policy pipeline* progress only. Numerical work stays
behind the simulation worker boundary: the orchestrator plans, dispatches, and
interprets, and it never computes a fiscal or macroeconomic number.

Every transition is validated against an explicit table before it is persisted,
so an out-of-order request (a broker redelivery, a restarted process, a retried
job) is rejected or ignored instead of silently skipping pipeline stages.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from fiscal_astra.database.enums import OrchestratorState

__all__ = [
    "ALLOWED_TRANSITIONS",
    "PIPELINE_ORDER",
    "STAGES_REQUIRING_VERIFIED_RESULT",
    "TERMINAL_STATES",
    "OrchestratorError",
    "OrchestratorErrorCode",
    "OrchestratorState",
    "RunStateMachine",
    "can_transition",
    "is_terminal",
    "next_states",
    "successor",
]


class OrchestratorErrorCode(StrEnum):
    """Stable error codes attached to every orchestrator failure."""

    INVALID_STATE_TRANSITION = "invalid_state_transition"
    UNKNOWN_STATE = "unknown_state"
    RUN_NOT_FOUND = "run_not_found"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN_ENGINE = "unknown_engine"
    MISSING_SIMULATION_RESULT = "missing_simulation_result"
    NOT_EXECUTED_RESULT = "not_executed_result"
    SIMULATION_FAILED = "simulation_failed"
    SIMULATION_NOT_EXECUTED = "simulation_not_executed"
    ENQUEUE_FAILED = "enqueue_failed"
    INTERNAL_ERROR = "internal_error"


class OrchestratorError(RuntimeError):
    """A terminal orchestrator failure carrying a machine-readable code."""

    def __init__(self, code: OrchestratorErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


# The happy path. ``COMPLETED`` is only reachable through the whole pipeline, so
# no stage can be skipped by jumping ahead.
PIPELINE_ORDER: tuple[OrchestratorState, ...] = (
    OrchestratorState.RECEIVED,
    OrchestratorState.VALIDATING,
    OrchestratorState.MODEL_PLANNING,
    OrchestratorState.DATA_PREPARATION,
    OrchestratorState.SIMULATION_QUEUED,
    OrchestratorState.SIMULATING,
    OrchestratorState.PARAMETER_EXTRACTION,
    OrchestratorState.POLICY_AGENT_ANALYSIS,
    OrchestratorState.DELIBERATION,
    OrchestratorState.EVIDENCE_VERIFICATION,
    OrchestratorState.REPORT_GENERATION,
    OrchestratorState.COMPLETED,
)

TERMINAL_STATES: frozenset[OrchestratorState] = frozenset(
    {
        OrchestratorState.COMPLETED,
        OrchestratorState.FAILED,
        OrchestratorState.CANCELLED,
    }
)

# States whose work may only start once a verified numeric simulation result is
# persisted. ``POLICY_AGENT_ANALYSIS`` is the first recommendation-forming state.
STAGES_REQUIRING_VERIFIED_RESULT: frozenset[OrchestratorState] = frozenset(
    {
        OrchestratorState.PARAMETER_EXTRACTION,
        OrchestratorState.POLICY_AGENT_ANALYSIS,
    }
)

# Only the orchestrator itself may enter the forward path, and only the
# simulation worker may move a run out of ``SIMULATION_QUEUED``.
_FORWARD_EDGES: dict[OrchestratorState, OrchestratorState] = {
    PIPELINE_ORDER[index]: PIPELINE_ORDER[index + 1] for index in range(len(PIPELINE_ORDER) - 1)
}

ALLOWED_TRANSITIONS: Mapping[OrchestratorState, frozenset[OrchestratorState]] = MappingProxyType(
    {
        state: (
            frozenset(
                {_FORWARD_EDGES[state], OrchestratorState.FAILED, OrchestratorState.CANCELLED}
            )
            if state in _FORWARD_EDGES
            else frozenset()
        )
        for state in OrchestratorState
    }
)


def successor(state: OrchestratorState) -> OrchestratorState | None:
    """Return the next state on the happy path, or ``None`` for terminal states."""

    return _FORWARD_EDGES.get(state)


def next_states(state: OrchestratorState) -> frozenset[OrchestratorState]:
    """Return every state reachable from ``state`` in one transition."""

    return ALLOWED_TRANSITIONS[state]


def is_terminal(state: OrchestratorState) -> bool:
    """Return whether the state machine has stopped for this run."""

    return state in TERMINAL_STATES


def can_transition(current: OrchestratorState, target: OrchestratorState) -> bool:
    """Return whether ``current -> target`` is a declared transition."""

    return target in ALLOWED_TRANSITIONS[current]


class RunStateMachine:
    """Pure, side-effect-free transition validation for one run.

    Keeping validation free of I/O lets the whole transition table be unit
    tested, while :class:`fiscal_astra.orchestrator.store.RunStateStore` remains
    the only place that persists a transition and appends its audit event.
    """

    def __init__(self, state: OrchestratorState) -> None:
        self._state = state

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self._state)

    def validate(
        self,
        target: OrchestratorState | str,
        *,
        verified_result_available: bool = False,
    ) -> OrchestratorState:
        """Return ``target`` when the transition and its guards are legal, else raise."""

        target_state = self.validate_shape(target)

        if target_state in STAGES_REQUIRING_VERIFIED_RESULT and not verified_result_available:
            raise OrchestratorError(
                OrchestratorErrorCode.MISSING_SIMULATION_RESULT,
                (
                    f"State {target_state.value} requires a persisted simulation result with "
                    "numeric_status=verified_numeric"
                ),
            )
        return target_state

    def validate_shape(self, target: OrchestratorState | str) -> OrchestratorState:
        """Return ``target`` when only the transition graph allows it, else raise."""

        try:
            target_state = OrchestratorState(target)
        except ValueError as error:
            raise OrchestratorError(OrchestratorErrorCode.UNKNOWN_STATE, str(error)) from error

        if not can_transition(self._state, target_state):
            raise OrchestratorError(
                OrchestratorErrorCode.INVALID_STATE_TRANSITION,
                f"Transition {self._state.value} -> {target_state.value} is not allowed",
            )
        return target_state
