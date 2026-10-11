"""Roster of agents that may call a local model.

The orchestrator, every policy sub-agent (``03-policy-experts.md``), and every
simulation agent (``02-simulation-agents.md``) share one provider abstraction
and one gateway, while each keeps its own endpoint configuration.

The roster also declares *why* an agent may call a model. No entry exists for
producing an economic quantity, which keeps the numerical boundary explicit
even before a request is built.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fiscal_astra.database.enums import AgentRole
from fiscal_astra.llm.contracts import LlmPurpose
from fiscal_astra.llm.errors import LlmConfigurationError


class LlmAgent(BaseModel):
    """A participating agent and the LLM work it is allowed to request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    role: AgentRole
    purpose: LlmPurpose
    description: str


ORCHESTRATOR_AGENT_ID = "orchestrator"

POLICY_AGENT_IDS: tuple[str, ...] = (
    "fiscal_taxation",
    "fiscal_spending",
    "fiscal_debt",
    "macro_stabilization",
    "distribution_equity",
    "sustainability_intergenerational",
    "institutional_implementation",
    "international_coordination",
)

SIMULATION_AGENT_IDS: tuple[str, ...] = (
    "sim_master",
    "sim_hank",
    "sim_abm",
    "sim_dsge",
    "sim_var",
    "calibration",
    "validation",
)

EVIDENCE_AGENT_ID = "evidence"

_POLICY_AGENT_DESCRIPTIONS: dict[str, str] = {
    "fiscal_taxation": "Tax incidence, excess burden, revenue adequacy, compliance feasibility",
    "fiscal_spending": "Expenditure composition, effectiveness, targeting, crowding out",
    "fiscal_debt": "Debt sustainability, refinancing risk, debt-stabilizing primary balance",
    "macro_stabilization": "Timing, size, automatic stabilizers, inflation and external balance",
    "distribution_equity": "Distributional, poverty, gender, and vulnerable-group impact",
    "sustainability_intergenerational": "Long-horizon fiscal gap, pension/health, climate cost",
    "institutional_implementation": "PFM capacity, legal and political feasibility, sequencing",
    "international_coordination": "Spillovers, competitiveness, international compliance",
}

_SIMULATION_AGENT_DESCRIPTIONS: dict[str, str] = {
    "sim_master": "Simulation cluster coordination and cross-model disagreement reporting",
    "sim_hank": "Heterogeneous-household analysis planning, MPC and welfare interpretation",
    "sim_abm": "Agent-based scenario design and emergent-behaviour interpretation",
    "sim_dsge": "Structural New Keynesian model setup and welfare interpretation",
    "sim_var": "Empirical identification scheme and forecast validation planning",
    "calibration": "Parameter sourcing, transferability notes, sensitivity planning",
    "validation": "Validation, benchmark, robustness, and falsification planning",
}

_ROSTER: tuple[LlmAgent, ...] = (
    LlmAgent(
        agent_id=ORCHESTRATOR_AGENT_ID,
        role=AgentRole.ORCHESTRATOR,
        purpose=LlmPurpose.PLANNING,
        description="Pipeline planning, sub-agent summoning, adjudication, report drafting",
    ),
    *(
        LlmAgent(
            agent_id=agent_id,
            role=AgentRole.POLICY_EXPERT,
            purpose=LlmPurpose.DELIBERATION,
            description=_POLICY_AGENT_DESCRIPTIONS[agent_id],
        )
        for agent_id in POLICY_AGENT_IDS
    ),
    *(
        LlmAgent(
            agent_id=agent_id,
            role=AgentRole.CALIBRATION
            if agent_id == "calibration"
            else (AgentRole.VALIDATION if agent_id == "validation" else AgentRole.SIMULATION),
            purpose=LlmPurpose.INTERPRETATION,
            description=_SIMULATION_AGENT_DESCRIPTIONS[agent_id],
        )
        for agent_id in SIMULATION_AGENT_IDS
    ),
    LlmAgent(
        agent_id=EVIDENCE_AGENT_ID,
        role=AgentRole.VALIDATION,
        purpose=LlmPurpose.EVIDENCE_REVIEW,
        description="Evidence retrieval planning and reference verification review",
    ),
)

AGENT_ROSTER: dict[str, LlmAgent] = {agent.agent_id: agent for agent in _ROSTER}


def agent_ids() -> list[str]:
    """Return every agent id that may call a local model."""

    return sorted(AGENT_ROSTER)


def resolve_agent(agent_id: str) -> LlmAgent:
    """Return the roster entry for an agent id or raise a configuration error."""

    try:
        return AGENT_ROSTER[agent_id]
    except KeyError as error:
        known = ", ".join(agent_ids())
        raise LlmConfigurationError(
            f"unknown LLM agent {agent_id!r}; registered agents: {known}", agent_id=agent_id
        ) from error


__all__ = [
    "AGENT_ROSTER",
    "EVIDENCE_AGENT_ID",
    "ORCHESTRATOR_AGENT_ID",
    "POLICY_AGENT_IDS",
    "SIMULATION_AGENT_IDS",
    "LlmAgent",
    "agent_ids",
    "resolve_agent",
]
