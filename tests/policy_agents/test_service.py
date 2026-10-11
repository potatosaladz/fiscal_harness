"""Invocation, provenance, and persistence tests for policy expert agents."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fiscal_astra.database import AgentMessage, AgentSession, Base
from fiscal_astra.database.enums import MessageRole
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.llm.errors import LlmBoundaryError, LlmInvocationFailed
from fiscal_astra.llm.gateway import LlmGateway
from fiscal_astra.llm.mock import MockLlmProvider
from fiscal_astra.llm.settings import LlmAgentSettings
from fiscal_astra.policy_agents.contracts import (
    EvidenceReference,
    PolicyAgentContext,
    PolicyAgentOutput,
)
from fiscal_astra.policy_agents.mock import MockPolicyContextProvider
from fiscal_astra.policy_agents.service import PolicyAgentService
from fiscal_astra.services.audit_logger import AuditLogger

AGENT_ID = "fiscal_taxation"


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
def run_id(session_factory: sessionmaker[Session]) -> str:
    with session_factory.begin() as session:
        scenarios = ScenarioRepository(session)
        scenario = scenarios.create_scenario("policy-test", "Policy test")
        version = scenarios.create_version(scenario, 1, {}, [])
        run = SimulationRunRepository(session).create_run(version, "policy-test")
        return str(run.id)


def build_context(run_id: str, *, verification: str = "verified") -> PolicyAgentContext:
    return PolicyAgentContext(
        run_id=run_id,
        scenario={"label": "policy-test"},
        evidence_pack=[
            EvidenceReference(
                evidence_id="ev-1",
                citation="Source A",
                verification_status=verification,  # type: ignore[arg-type]
            )
        ],
        evaluation_criteria=["revenue_adequacy"],
    )


def build_service(
    session_factory: sessionmaker[Session],
    context: PolicyAgentContext,
    responses: list[object],
) -> tuple[PolicyAgentService, MockLlmProvider, MockPolicyContextProvider]:
    provider = MockLlmProvider(responses)  # type: ignore[arg-type]
    gateway = LlmGateway(
        provider,
        LlmAgentSettings(provider="mock", model="mock-model", model_version="mock-1"),
        AuditLogger(session_factory),
        agent_id=AGENT_ID,
    )
    context_provider = MockPolicyContextProvider(context)
    service = PolicyAgentService(session_factory, {AGENT_ID: gateway}, context_provider)
    return service, provider, context_provider


def successful_output(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent_id": AGENT_ID,
        "position_summary": "Raise revenue through a broadened base.",
        "claims": [
            {
                "claim": "Broadening the base is administratively simpler.",
                "type": "fact",
                "evidence_ids": ["ev-1"],
                "confidence": "medium",
            }
        ],
        "evaluation": {"revenue_adequacy": {"score": 60, "rationale": "Adequate"}},
    }
    payload.update(overrides)
    return payload


def test_delegates_context_loading_to_injected_provider(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    service, _, context_provider = build_service(
        session_factory, build_context(run_id), [successful_output()]
    )
    service.invoke(run_id=run_id, agent_id=AGENT_ID)
    assert context_provider.calls == [(run_id, AGENT_ID)]


def test_successful_invocation_persists_session_and_messages(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    service, _, _ = build_service(session_factory, build_context(run_id), [successful_output()])
    output = service.invoke(run_id=run_id, agent_id=AGENT_ID)
    assert isinstance(output, PolicyAgentOutput)

    with session_factory() as session:
        stored = session.scalars(select(AgentSession)).all()
        assert len(stored) == 1
        assert stored[0].agent_id == AGENT_ID
        assert stored[0].finished_at is not None
        roles = [
            row.role
            for row in session.scalars(select(AgentMessage).order_by(AgentMessage.sequence)).all()
        ]
        assert roles == [
            MessageRole.SYSTEM.value,
            MessageRole.TOOL.value,
            MessageRole.ASSISTANT.value,
        ]


def test_claim_without_verified_evidence_is_rejected(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    payload = successful_output(
        claims=[
            {
                "claim": "Unsupported",
                "type": "fact",
                "evidence_ids": ["ev-missing"],
                "confidence": "low",
            }
        ]
    )
    service, _, _ = build_service(session_factory, build_context(run_id), [payload])
    with pytest.raises(LlmInvocationFailed) as excinfo:
        service.invoke(run_id=run_id, agent_id=AGENT_ID)
    failure_types = [item["type"] for item in excinfo.value.failures]
    assert "LlmBoundaryError" in failure_types


def test_assumption_without_evidence_is_allowed(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    payload = successful_output(
        claims=[
            {
                "claim": "Assume stable administration.",
                "type": "assumption",
                "evidence_ids": [],
                "confidence": "low",
            }
        ]
    )
    service, _, _ = build_service(session_factory, build_context(run_id), [payload])
    output = service.invoke(run_id=run_id, agent_id=AGENT_ID)
    assert output.claims[0].type == "assumption"


def test_economic_number_is_a_terminal_boundary_failure(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    """The numeric boundary fails the first attempt and is never retried."""

    payload = successful_output(estimated_revenue=123.4)
    service, provider, _ = build_service(session_factory, build_context(run_id), [payload])
    with pytest.raises(LlmInvocationFailed):
        service.invoke(run_id=run_id, agent_id=AGENT_ID)
    assert len(provider.requests) == 1


def test_agent_identity_mismatch_is_rejected(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    payload = successful_output(agent_id="fiscal_debt")
    service, _, _ = build_service(session_factory, build_context(run_id), [payload])
    with pytest.raises(LlmInvocationFailed) as excinfo:
        service.invoke(run_id=run_id, agent_id=AGENT_ID)
    assert any(item["type"] == LlmBoundaryError.__name__ for item in excinfo.value.failures)


def test_failed_invocation_still_records_the_session(
    session_factory: sessionmaker[Session], run_id: str
) -> None:
    service, _, _ = build_service(
        session_factory, build_context(run_id), [successful_output(estimated_revenue=1.0)]
    )
    with pytest.raises(LlmInvocationFailed):
        service.invoke(run_id=run_id, agent_id=AGENT_ID)
    with session_factory() as session:
        stored = session.scalars(select(AgentSession)).all()
        assert len(stored) == 1
        assert stored[0].finished_at is not None
