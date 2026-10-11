"""Injectable protocol for MCP-shaped policy-agent context access."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fiscal_astra.policy_agents.contracts import PolicyAgentContext


@runtime_checkable
class PolicyContextProvider(Protocol):
    """Load auditable context without coupling agents to an MCP transport."""

    tool_name: str

    def load_context(self, *, run_id: str, agent_id: str) -> PolicyAgentContext:
        """Return the context authorized for one run and agent."""


__all__ = ["PolicyContextProvider"]
