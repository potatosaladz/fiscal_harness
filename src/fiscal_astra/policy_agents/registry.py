"""Capability registry for implemented fiscal policy experts."""

from __future__ import annotations

from fiscal_astra.policy_agents.contracts import PolicyAgentCapability

_CAPABILITIES = (
    PolicyAgentCapability(
        agent_id="fiscal_taxation",
        title="Fiscal Taxation Agent",
        expertise=["tax incidence", "excess burden", "compliance", "administrative cost"],
        tasks=[
            "Assess incidence, revenue adequacy, equity, efficiency, and compliance feasibility",
            "Identify sensitivity needs for avoidance, migration, and behavioural responses",
        ],
        evaluation_criteria=[
            "revenue_adequacy",
            "progressivity",
            "efficiency_cost",
            "compliance_feasibility",
            "international_competitiveness",
        ],
        prompt_version="taxation-v1",
    ),
    PolicyAgentCapability(
        agent_id="fiscal_debt",
        title="Fiscal Debt Agent",
        expertise=["debt sustainability", "refinancing risk", "contingent liabilities"],
        tasks=[
            "Interpret engine-produced debt paths and stress scenarios",
            "Assess refinancing, currency, creditor-composition, and implicit-liability risks",
        ],
        evaluation_criteria=[
            "debt_stability",
            "refinancing_risk",
            "interest_burden",
            "currency_risk",
            "implicit_liability_risk",
        ],
        prompt_version="debt-v1",
    ),
    PolicyAgentCapability(
        agent_id="macro_stabilization",
        title="Macro Stabilization Agent",
        expertise=["business cycle", "automatic stabilizers", "inflation", "external balance"],
        tasks=[
            "Assess policy timing, state contingency, and pro-cyclicality risk",
            "Interpret interactions with monetary policy and exchange-rate arrangements",
        ],
        evaluation_criteria=[
            "output_stabilization",
            "inflation_stability",
            "external_sustainability",
            "policy_credibility",
        ],
        prompt_version="macro-stabilization-v1",
    ),
)

POLICY_AGENT_CAPABILITIES = {item.agent_id: item for item in _CAPABILITIES}


def policy_agent_ids() -> list[str]:
    return sorted(POLICY_AGENT_CAPABILITIES)


def resolve_policy_agent(agent_id: str) -> PolicyAgentCapability:
    try:
        return POLICY_AGENT_CAPABILITIES[agent_id]
    except KeyError as error:
        available = ", ".join(policy_agent_ids())
        raise KeyError(f"Unknown policy agent: {agent_id} (available: {available})") from error


__all__ = ["POLICY_AGENT_CAPABILITIES", "policy_agent_ids", "resolve_policy_agent"]
