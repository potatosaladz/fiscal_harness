"""Composition of providers and gateways from environment configuration.

One agent, one gateway, one configuration. Building a gateway is the only way
an agent obtains a model, so per-agent endpoints, credentials, timeouts, and
retry budgets cannot be bypassed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.config.settings import Settings, get_settings
from fiscal_astra.llm.agents import agent_ids, resolve_agent
from fiscal_astra.llm.errors import LlmConfigurationError
from fiscal_astra.llm.gateway import LlmGateway
from fiscal_astra.llm.mock import MockLlmProvider
from fiscal_astra.llm.openai_compatible import OpenAiCompatibleProvider
from fiscal_astra.llm.protocols import LlmProvider
from fiscal_astra.llm.settings import LlmAgentSettings
from fiscal_astra.services.audit_logger import AuditLogger

_MOCK_PROVIDERS = frozenset({"mock", "none", "disabled"})
_OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {"openai_compatible", "openai", "oai", "local", "vllm", "ollama"}
)


def build_provider(settings: LlmAgentSettings, *, agent_id: str) -> LlmProvider:
    """Instantiate the provider named by one agent's configuration."""

    name = settings.provider.strip().lower()
    if name in _MOCK_PROVIDERS:
        # The mock holds no script here: an unconfigured deployment must fail
        # loudly rather than produce invented agent text.
        return MockLlmProvider()
    if name in _OPENAI_COMPATIBLE_PROVIDERS:
        return OpenAiCompatibleProvider(
            base_url=settings.base_url or "",
            model_name=settings.model or "",
            model_version=settings.model_version or "",
            api_key=settings.api_key.get_secret_value() if settings.api_key else None,
            timeout_seconds=settings.timeout_seconds,
        )
    raise LlmConfigurationError(
        f"unknown LLM provider {settings.provider!r} for agent {agent_id!r}",
        agent_id=agent_id,
    )


def build_llm_gateway(
    session_factory: sessionmaker[Session],
    agent_id: str,
    *,
    settings: Settings | None = None,
) -> LlmGateway:
    """Build the audited gateway for one agent using its own configuration."""

    resolved = settings or get_settings()
    agent = resolve_agent(agent_id)
    agent_settings = resolved.llm_agent_settings(agent.agent_id)
    provider = build_provider(agent_settings, agent_id=agent.agent_id)
    return LlmGateway(
        provider,
        agent_settings,
        AuditLogger(session_factory),
        agent_id=agent.agent_id,
    )


def build_llm_gateways(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings | None = None,
) -> dict[str, LlmGateway]:
    """Build one gateway per registered agent.

    A provider that cannot be constructed is skipped rather than faked, so a
    deployment with only some endpoints configured still starts and reports the
    remaining agents as unavailable.
    """

    gateways: dict[str, LlmGateway] = {}
    for agent_id in agent_ids():
        try:
            gateways[agent_id] = build_llm_gateway(session_factory, agent_id, settings=settings)
        except LlmConfigurationError:
            continue
    return gateways


__all__ = ["build_llm_gateway", "build_llm_gateways", "build_provider"]
