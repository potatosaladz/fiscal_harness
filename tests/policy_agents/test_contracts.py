"""Contract and registry tests for policy expert agents."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fiscal_astra.policy_agents.contracts import (
    PolicyAgentCapability,
    PolicyAgentOutput,
)
from fiscal_astra.policy_agents.registry import (
    POLICY_AGENT_CAPABILITIES,
    policy_agent_ids,
    resolve_policy_agent,
)


def _output(**overrides: object) -> PolicyAgentOutput:
    payload = {
        "agent_id": "fiscal_taxation",
        "position_summary": "Position",
        "claims": [],
        "evaluation": {"revenue_adequacy": {"score": 70, "rationale": "Ok"}},
    }
    payload.update(overrides)
    return PolicyAgentOutput(**payload)  # type: ignore[arg-type]


def test_registry_exposes_the_three_required_agents() -> None:
    assert policy_agent_ids() == ["fiscal_debt", "fiscal_taxation", "macro_stabilization"]
    capability = resolve_policy_agent("fiscal_debt")
    assert isinstance(capability, PolicyAgentCapability)
    assert capability.tools == ("policy_context",)


def test_registry_metadata_includes_criteria_and_prompt_version() -> None:
    for capability in POLICY_AGENT_CAPABILITIES.values():
        assert capability.evaluation_criteria
        assert capability.prompt_version


def test_unknown_agent_is_rejected() -> None:
    with pytest.raises(KeyError):
        resolve_policy_agent("fiscal_does_not_exist")


def test_output_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _output(unexpected_field="nope")


def test_output_contract_declares_no_economic_number_field() -> None:
    """The schema itself carries no fiscal or macroeconomic quantity."""

    fields = set(PolicyAgentOutput.model_fields)
    assert not fields & {"estimated_revenue", "debt_ratio", "gdp", "multiplier"}
    with pytest.raises(ValidationError):
        _output(estimated_revenue=123.4)


def test_evaluation_score_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _output(evaluation={"revenue_adequacy": {"score": 101, "rationale": "Too high"}})


def test_capability_requires_expertise_and_tasks() -> None:
    with pytest.raises(ValidationError):
        PolicyAgentCapability(
            agent_id="x",
            title="X",
            expertise=[],
            tasks=["t"],
            evaluation_criteria=["c"],
            prompt_version="v1",
        )
