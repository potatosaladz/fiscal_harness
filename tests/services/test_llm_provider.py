"""Unit tests for the local-LLM provider boundary."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fiscal_astra.config.settings import Settings
from fiscal_astra.database import AuditEvent, Base
from fiscal_astra.database.enums import AuditActionType
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.llm.agents import (
    ORCHESTRATOR_AGENT_ID,
    POLICY_AGENT_IDS,
    SIMULATION_AGENT_IDS,
    agent_ids,
)
from fiscal_astra.llm.boundary import assert_within_numeric_boundary
from fiscal_astra.llm.contracts import LlmPurpose, LlmRequest, LlmUsage
from fiscal_astra.llm.errors import LlmBoundaryError, LlmInvocationFailed, LlmTransportError
from fiscal_astra.llm.gateway import LlmGateway
from fiscal_astra.llm.mock import MockLlmProvider
from fiscal_astra.llm.registry import build_llm_gateways, build_provider
from fiscal_astra.llm.settings import LlmAgentSettings
from fiscal_astra.services.audit_logger import AuditLogger


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_summary: str
    confidence: str


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def run_context(session_factory: sessionmaker[Session]) -> tuple[uuid.UUID, str]:
    with session_factory.begin() as session:
        scenarios = ScenarioRepository(session)
        scenario = scenarios.create_scenario("llm-test", "LLM test")
        version = scenarios.create_version(scenario, 1, {}, [])
        run = SimulationRunRepository(session).create_run(version, "llm-test")
        return run.id, f"corr-{run.id.hex}"


def make_request(agent_id: str = ORCHESTRATOR_AGENT_ID) -> LlmRequest:
    return LlmRequest(
        agent_id=agent_id,
        purpose=LlmPurpose.PLANNING,
        prompt_version="planner-v2",
        system_prompt="Plan only; do not calculate economic values.",
        user_prompt="Select a model plan from the declared scenario.",
        response_schema=AgentOutput,
    )


def make_gateway(
    session_factory: sessionmaker[Session],
    provider: MockLlmProvider,
    *,
    max_attempts: int = 3,
) -> LlmGateway:
    return LlmGateway(
        provider,
        LlmAgentSettings(max_attempts=max_attempts, retry_backoff_seconds=0),
        AuditLogger(session_factory),
        agent_id=ORCHESTRATOR_AGENT_ID,
    )


def test_mock_provider_returns_schema_validated_output_and_audit_provenance(
    session_factory: sessionmaker[Session], run_context: tuple[uuid.UUID, str]
) -> None:
    run_id, trace_id = run_context
    provider = MockLlmProvider(
        [{"position_summary": "Use the declared model plan.", "confidence": "medium"}],
        model_name="planner-local",
        model_version="planner-local-7",
        usage=LlmUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
    )

    response = make_gateway(session_factory, provider).invoke(
        make_request(), run_id=run_id, trace_id=trace_id
    )

    assert response.model_name == "planner-local"
    assert response.model_version == "planner-local-7"
    assert response.prompt_version == "planner-v2"
    assert response.usage.total_tokens == 20
    assert len(response.input_hash) == 64
    assert len(response.output_hash) == 64
    assert response.attempts == 1

    with session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence)
            )
        )
    assert [event.action_type for event in events] == [
        AuditActionType.AGENT_INVOKED,
        AuditActionType.AGENT_RESPONSE_RECEIVED,
    ]
    assert events[0].trace_id == trace_id
    assert events[1].output_refs[0]["input_hash"] == response.input_hash
    assert events[1].output_refs[0]["output_hash"] == response.output_hash
    assert AuditLogger(session_factory).verify_chain(run_id)


def test_invalid_json_then_valid_json_uses_bounded_retry(
    session_factory: sessionmaker[Session], run_context: tuple[uuid.UUID, str]
) -> None:
    run_id, trace_id = run_context
    provider = MockLlmProvider(
        ["not json", {"position_summary": "retry succeeded", "confidence": "low"}]
    )

    response = make_gateway(session_factory, provider, max_attempts=2).invoke(
        make_request(), run_id=run_id, trace_id=trace_id
    )

    assert response.attempts == 2
    assert len(provider.requests) == 2


def test_invalid_output_exhausts_retry_and_is_audited_as_failure(
    session_factory: sessionmaker[Session], run_context: tuple[uuid.UUID, str]
) -> None:
    run_id, trace_id = run_context
    provider = MockLlmProvider([{}, {}, {}])

    with pytest.raises(LlmInvocationFailed) as raised:
        make_gateway(session_factory, provider, max_attempts=2).invoke(
            make_request(), run_id=run_id, trace_id=trace_id
        )

    assert raised.value.attempts == 2
    with session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence)
            )
        )
    assert events[-1].action_type == AuditActionType.ERROR
    assert events[-1].error_info[0]["type"] == "LlmSchemaError"


def test_numerical_solver_boundary_is_terminal_and_not_retried(
    session_factory: sessionmaker[Session], run_context: tuple[uuid.UUID, str]
) -> None:
    run_id, trace_id = run_context
    provider = MockLlmProvider(
        [
            {
                "position_summary": "This is not allowed.",
                "confidence": "high",
                "gdp_growth_rate": 0.03,
            },
            {"position_summary": "would be a fabricated retry", "confidence": "low"},
        ]
    )

    with pytest.raises(LlmInvocationFailed):
        make_gateway(session_factory, provider, max_attempts=2).invoke(
            make_request(), run_id=run_id, trace_id=trace_id
        )
    assert len(provider.requests) == 1

    with pytest.raises(LlmBoundaryError):
        assert_within_numeric_boundary({"debt_to_gdp": 0.7}, agent_id="test")


def test_each_registered_agent_can_have_distinct_endpoint_settings() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_base_url="http://default.test/v1",
        llm_model="default-model",
        llm_model_version="default-1",
        llm_agents={
            ORCHESTRATOR_AGENT_ID: {
                "base_url": "http://planner.test/v1",
                "model": "planner",
                "model_version": "planner-2",
            },
            POLICY_AGENT_IDS[0]: {
                "base_url": "http://tax.test/v1",
                "model": "tax",
                "model_version": "tax-3",
            },
            SIMULATION_AGENT_IDS[0]: {
                "base_url": "http://sim.test/v1",
                "model": "sim",
                "model_version": "sim-4",
            },
        },
    )

    assert settings.llm_agent_settings(ORCHESTRATOR_AGENT_ID).base_url == "http://planner.test/v1"
    assert settings.llm_agent_settings(POLICY_AGENT_IDS[0]).base_url == "http://tax.test/v1"
    assert settings.llm_agent_settings(SIMULATION_AGENT_IDS[0]).base_url == "http://sim.test/v1"
    assert settings.llm_agent_settings(POLICY_AGENT_IDS[1]).base_url == "http://default.test/v1"


def test_openai_compatible_configuration_never_requires_an_api_key() -> None:
    provider = build_provider(
        LlmAgentSettings(
            provider="openai_compatible",
            base_url="http://localhost:8000/v1",
            model="local-model",
            model_version="local-1",
        ),
        agent_id=ORCHESTRATOR_AGENT_ID,
    )
    assert provider.provider_name == "openai_compatible"
    assert provider.model_name == "local-model"


def test_mock_empty_script_fails_instead_of_fabricating() -> None:
    with pytest.raises(LlmTransportError):
        MockLlmProvider().complete(make_request())


def test_all_required_agent_roster_members_are_configurable() -> None:
    expected = {
        ORCHESTRATOR_AGENT_ID,
        *POLICY_AGENT_IDS,
        *SIMULATION_AGENT_IDS,
        "evidence",
    }
    assert expected.issubset(set(agent_ids()))


def test_all_registered_agents_receive_independent_mock_gateways(
    session_factory: sessionmaker[Session],
) -> None:
    gateways = build_llm_gateways(session_factory, settings=Settings(_env_file=None))

    assert set(agent_ids()) == set(gateways)
    assert len({id(gateway) for gateway in gateways.values()}) == len(gateways)
    assert all(gateway.provider.provider_name == "mock" for gateway in gateways.values())
