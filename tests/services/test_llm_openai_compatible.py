"""Tests for the [OI]-compatible provider using a local stub endpoint.

No live model is required: a loopback HTTP server replays canned
chat-completion envelopes, so the transport, structured-output, usage, and
retry behaviour is verified deterministically.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fiscal_astra.database import AuditEvent, Base
from fiscal_astra.database.repositories import ScenarioRepository, SimulationRunRepository
from fiscal_astra.llm.contracts import LlmPurpose, LlmRequest
from fiscal_astra.llm.errors import (
    LlmConfigurationError,
    LlmInvocationFailed,
    LlmResponseError,
    LlmTimeoutError,
    LlmTransportError,
)
from fiscal_astra.llm.gateway import LlmGateway
from fiscal_astra.llm.openai_compatible import OpenAiCompatibleProvider
from fiscal_astra.llm.settings import LlmAgentSettings
from fiscal_astra.services.audit_logger import AuditLogger

AGENT_ID = "fiscal_taxation"


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_summary: str
    confidence: str


class StubEndpoint:
    """Loopback chat-completions stub with scripted responses."""

    def __init__(self) -> None:
        self.responses: list[tuple[int, dict[str, Any] | str]] = []
        self.requests: list[dict[str, Any]] = []
        self.delay_seconds = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                outer.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": json.loads(raw.decode("utf-8")),
                    }
                )
                if outer.delay_seconds:
                    time.sleep(outer.delay_seconds)
                status, payload = (
                    outer.responses.pop(0) if outer.responses else (200, {"choices": []})
                )
                body = payload if isinstance(payload, str) else json.dumps(payload)
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except BrokenPipeError:
                    # Expected when the client correctly aborts a timed-out
                    # request; the timeout itself is asserted by the caller.
                    pass

            def log_message(self, *_: Any) -> None:
                """Silence the stub's request logging."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def endpoint() -> Iterator[StubEndpoint]:
    stub = StubEndpoint()
    yield stub
    stub.close()


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
        scenario = scenarios.create_scenario("llm-http", "LLM HTTP test")
        version = scenarios.create_version(scenario, 1, {}, [])
        run = SimulationRunRepository(session).create_run(version, "llm-http")
        return run.id, f"corr-{run.id.hex}"


def completion(model: str, content: str, *, usage: bool = True) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
    }
    if usage:
        envelope["usage"] = {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "total_tokens": 42,
        }
    return envelope


def make_request() -> LlmRequest:
    return LlmRequest(
        agent_id=AGENT_ID,
        purpose=LlmPurpose.DELIBERATION,
        prompt_version="tax-v3",
        system_prompt="Give an independent position. Never compute a value.",
        user_prompt="Assess the declared tax scenario.",
        response_schema=AgentOutput,
        temperature=0.0,
        max_tokens=256,
    )


def make_gateway(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    *,
    api_key: str | None = None,
    timeout_seconds: float = 5.0,
    max_attempts: int = 3,
) -> LlmGateway:
    settings = LlmAgentSettings(
        provider="openai_compatible",
        base_url=endpoint.base_url,
        model="qwen2.5-14b-instruct",
        model_version="2025-04",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=0,
    )
    provider = OpenAiCompatibleProvider(
        base_url=settings.base_url or "",
        model_name=settings.model or "",
        model_version=settings.model_version or "unknown",
        api_key=settings.api_key.get_secret_value() if settings.api_key else None,
        timeout_seconds=settings.timeout_seconds,
    )
    return LlmGateway(provider, settings, AuditLogger(session_factory), agent_id=AGENT_ID)


def test_structured_json_completion_records_model_usage_and_hashes(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    endpoint.responses.append(
        (
            200,
            completion(
                "qwen2.5-14b-instruct",
                json.dumps(
                    {"position_summary": "Progressive base broadening.", "confidence": "low"}
                ),
            ),
        )
    )

    response = make_gateway(endpoint, session_factory).invoke(
        make_request(), run_id=run_id, trace_id=trace_id
    )

    sent = endpoint.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["body"]["model"] == "qwen2.5-14b-instruct"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert sent["body"]["max_tokens"] == 256
    assert [message["role"] for message in sent["body"]["messages"]] == ["system", "user"]
    assert sent["authorization"] is None

    assert response.output == {
        "position_summary": "Progressive base broadening.",
        "confidence": "low",
    }
    assert response.model_name == "qwen2.5-14b-instruct"
    assert response.model_version == "2025-04"
    assert response.usage.total_tokens == 42
    assert response.prompt_version == "tax-v3"
    assert response.attempts == 1
    assert response.input_hash != response.output_hash


def test_api_key_is_sent_but_never_persisted(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    endpoint.responses.append(
        (
            200,
            completion(
                "qwen2.5-14b-instruct",
                json.dumps({"position_summary": "Position.", "confidence": "high"}),
            ),
        )
    )

    make_gateway(endpoint, session_factory, api_key="local-secret-token").invoke(
        make_request(), run_id=run_id, trace_id=trace_id
    )

    assert endpoint.requests[0]["authorization"] == "Bearer local-secret-token"
    exported = AuditLogger(session_factory).export_json(run_id)
    assert "local-secret-token" not in exported
    with session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence)
            )
        )
    assert events[0].input_refs[0]["endpoint"]["key_configured"] is True


def test_transient_http_failure_is_retried_within_the_bound(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    endpoint.responses.extend(
        [
            (503, {"error": "model loading"}),
            (
                200,
                completion(
                    "qwen2.5-14b-instruct",
                    json.dumps({"position_summary": "Recovered.", "confidence": "medium"}),
                ),
            ),
        ]
    )

    response = make_gateway(endpoint, session_factory, max_attempts=2).invoke(
        make_request(), run_id=run_id, trace_id=trace_id
    )

    assert response.attempts == 2
    assert len(endpoint.requests) == 2


def test_client_error_is_not_retried(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    endpoint.responses.extend([(400, {"error": "bad request"}), (400, {"error": "bad request"})])

    with pytest.raises(LlmInvocationFailed) as raised:
        make_gateway(endpoint, session_factory, max_attempts=3).invoke(
            make_request(), run_id=run_id, trace_id=trace_id
        )

    assert raised.value.attempts == 1
    assert len(endpoint.requests) == 1


def test_non_json_body_is_a_retryable_response_error(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    endpoint.responses.append((200, "<html>gateway error</html>"))

    with pytest.raises(LlmInvocationFailed) as raised:
        make_gateway(endpoint, session_factory, max_attempts=1).invoke(
            make_request(), run_id=run_id, trace_id=trace_id
        )

    assert raised.value.failures[0]["type"] == LlmResponseError.__name__


def test_timeout_is_reported_as_a_retryable_transport_failure(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    endpoint.delay_seconds = 0.5
    endpoint.responses.append(
        (
            200,
            completion(
                "qwen2.5-14b-instruct",
                json.dumps({"position_summary": "Late.", "confidence": "low"}),
            ),
        )
    )

    with pytest.raises(LlmInvocationFailed) as raised:
        make_gateway(endpoint, session_factory, timeout_seconds=0.1, max_attempts=1).invoke(
            make_request(), run_id=run_id, trace_id=trace_id
        )

    assert raised.value.failures[0]["type"] == LlmTimeoutError.__name__
    assert raised.value.failures[0]["retryable"] is True


def test_missing_endpoint_configuration_fails_fast() -> None:
    with pytest.raises(LlmConfigurationError):
        OpenAiCompatibleProvider(base_url="", model_name="m", model_version="1")
    with pytest.raises(LlmConfigurationError):
        OpenAiCompatibleProvider(base_url="http://x.test/v1", model_name="", model_version="1")
    with pytest.raises(LlmConfigurationError):
        OpenAiCompatibleProvider(base_url="http://x.test/v1", model_name="m", model_version="")


def test_gateway_refuses_a_request_from_another_agent(
    endpoint: StubEndpoint,
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    request = make_request().model_copy(update={"agent_id": "fiscal_debt"})

    with pytest.raises(LlmConfigurationError):
        make_gateway(endpoint, session_factory).invoke(request, run_id=run_id, trace_id=trace_id)

    assert endpoint.requests == []


def test_unreachable_endpoint_raises_a_transport_error(
    session_factory: sessionmaker[Session],
    run_context: tuple[uuid.UUID, str],
) -> None:
    run_id, trace_id = run_context
    provider = OpenAiCompatibleProvider(
        base_url="http://127.0.0.1:9/v1",
        model_name="m",
        model_version="1",
        timeout_seconds=0.5,
    )
    gateway = LlmGateway(
        provider,
        LlmAgentSettings(max_attempts=1, retry_backoff_seconds=0),
        AuditLogger(session_factory),
        agent_id=AGENT_ID,
    )

    with pytest.raises(LlmInvocationFailed) as raised:
        gateway.invoke(make_request(), run_id=run_id, trace_id=trace_id)

    assert raised.value.failures[0]["type"] == LlmTransportError.__name__
