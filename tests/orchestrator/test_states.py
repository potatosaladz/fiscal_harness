"""Exhaustive tests for the orchestrator transition graph and guards."""

from __future__ import annotations

import itertools

import pytest

from fiscal_astra.orchestrator.states import (
    ALLOWED_TRANSITIONS,
    PIPELINE_ORDER,
    OrchestratorError,
    OrchestratorErrorCode,
    OrchestratorState,
    RunStateMachine,
)


def test_every_declared_forward_transition_is_valid() -> None:
    for current, target in zip(PIPELINE_ORDER, PIPELINE_ORDER[1:], strict=False):
        assert RunStateMachine(current).validate(target, verified_result_available=True) is target


def test_every_terminal_transition_is_valid() -> None:
    for current in PIPELINE_ORDER[:-1]:
        machine = RunStateMachine(current)
        assert machine.validate(OrchestratorState.FAILED) is OrchestratorState.FAILED
        assert machine.validate(OrchestratorState.CANCELLED) is OrchestratorState.CANCELLED


def test_every_undeclared_pair_is_invalid() -> None:
    declared = {
        (current, target) for current, targets in ALLOWED_TRANSITIONS.items() for target in targets
    }
    for current, target in itertools.product(OrchestratorState, repeat=2):
        if (current, target) in declared:
            continue
        with pytest.raises(OrchestratorError) as error:
            RunStateMachine(current).validate(target, verified_result_available=True)
        assert error.value.code is OrchestratorErrorCode.INVALID_STATE_TRANSITION


def test_recommendation_path_requires_verified_numeric_result() -> None:
    with pytest.raises(OrchestratorError) as error:
        RunStateMachine(OrchestratorState.SIMULATING).validate(
            OrchestratorState.PARAMETER_EXTRACTION,
            verified_result_available=False,
        )
    assert error.value.code is OrchestratorErrorCode.MISSING_SIMULATION_RESULT


def test_not_executed_is_not_accepted_as_numeric_result() -> None:
    with pytest.raises(OrchestratorError) as error:
        RunStateMachine(OrchestratorState.SIMULATING).validate(
            OrchestratorState.PARAMETER_EXTRACTION,
            verified_result_available=False,
        )
    assert error.value.code is OrchestratorErrorCode.MISSING_SIMULATION_RESULT


def test_unknown_state_has_stable_error_code() -> None:
    with pytest.raises(OrchestratorError) as error:
        RunStateMachine(OrchestratorState.RECEIVED).validate("does-not-exist")
    assert error.value.code is OrchestratorErrorCode.UNKNOWN_STATE


def test_terminal_states_have_no_outgoing_edges() -> None:
    for terminal in (
        OrchestratorState.COMPLETED,
        OrchestratorState.FAILED,
        OrchestratorState.CANCELLED,
    ):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
