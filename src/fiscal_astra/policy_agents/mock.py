"""Deterministic MCP-shaped context provider for policy-agent tests."""

from __future__ import annotations

from fiscal_astra.policy_agents.contracts import PolicyAgentContext


class MockPolicyContextProvider:
    """Replay one typed context and record every delegated tool call."""

    tool_name = "policy_context"

    def __init__(self, context: PolicyAgentContext) -> None:
        self.context = context
        self.calls: list[tuple[str, str]] = []

    def load_context(self, *, run_id: str, agent_id: str) -> PolicyAgentContext:
        self.calls.append((run_id, agent_id))
        if run_id != self.context.run_id:
            raise KeyError(f"No context for run {run_id!r}")
        return self.context


__all__ = ["MockPolicyContextProvider"]
