"""Audited, schema-validated invocation boundary for local LLM agents.

Every agent reaches a model through this gateway, so one place owns the
guarantees the specification demands: structured output validated against a
schema, bounded retry on an invalid completion, model name/version and prompt
version recorded, input and output hashes, token usage, and one append-only
audit event pair per invocation. The gateway never turns a failed completion
into a plausible one: when the retry budget runs out it fails the invocation.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from fiscal_astra.database.enums import AuditActionType
from fiscal_astra.llm.boundary import assert_within_numeric_boundary
from fiscal_astra.llm.contracts import (
    LlmRequest,
    LlmResponse,
    canonical_hash,
)
from fiscal_astra.llm.errors import (
    LlmConfigurationError,
    LlmError,
    LlmInvocationFailed,
    LlmResponseError,
    LlmSchemaError,
)
from fiscal_astra.llm.protocols import LlmProvider
from fiscal_astra.llm.settings import LlmAgentSettings
from fiscal_astra.services.audit_logger import AuditLogger

_MAX_ERROR_CHARS = 500


def _strip_code_fence(content: str) -> str:
    """Remove a markdown fence that a local model may wrap JSON in."""

    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    body = lines[1:] if len(lines) > 1 else []
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _parse_object(content: str, request: LlmRequest) -> dict[str, Any]:
    """Parse a completion into a JSON object or raise a retryable error."""

    try:
        decoded = json.loads(_strip_code_fence(content))
    except json.JSONDecodeError as error:
        raise LlmResponseError(
            f"completion is not valid JSON: {error.msg}", agent_id=request.agent_id
        ) from error
    if not isinstance(decoded, dict):
        raise LlmResponseError("completion JSON is not an object", agent_id=request.agent_id)
    return decoded


def _validate(payload: dict[str, Any], request: LlmRequest) -> dict[str, Any]:
    """Validate a parsed completion against the requested schema."""

    try:
        validated = request.response_schema.model_validate(payload)
    except ValidationError as error:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()[:5]
        )
        raise LlmSchemaError(
            f"completion does not satisfy {request.response_schema.__name__}: "
            f"{summary[:_MAX_ERROR_CHARS]}",
            agent_id=request.agent_id,
        ) from error
    return validated.model_dump(mode="json")


class LlmGateway:
    """Invoke one agent's model with validation, retry, and audit."""

    def __init__(
        self,
        provider: LlmProvider,
        settings: LlmAgentSettings,
        audit_logger: AuditLogger,
        *,
        agent_id: str,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._audit = audit_logger
        self._agent_id = agent_id

    @property
    def provider(self) -> LlmProvider:
        """Expose the configured provider for inspection and diagnostics."""

        return self._provider

    @property
    def settings(self) -> LlmAgentSettings:
        """Expose the effective per-agent configuration."""

        return self._settings

    def invoke(
        self,
        request: LlmRequest,
        *,
        run_id: uuid.UUID,
        trace_id: str,
    ) -> LlmResponse:
        """Return a schema-valid response or fail after a bounded retry.

        The request must name this gateway's agent: one agent's endpoint and
        credential may never be used to answer another agent's prompt.
        """

        if request.agent_id != self._agent_id:
            raise LlmConfigurationError(
                f"gateway for agent {self._agent_id!r} refused a request from "
                f"agent {request.agent_id!r}",
                agent_id=request.agent_id,
            )

        input_hash = canonical_hash(request.canonical_input())
        self._audit.record(
            run_id=run_id,
            trace_id=trace_id,
            actor_type="llm_agent",
            actor_id=self._agent_id,
            action_type=AuditActionType.AGENT_INVOKED,
            description=(
                f"{request.purpose} request to {self._provider.provider_name} "
                f"model {self._provider.model_name} ({request.prompt_version})"
            ),
            input_refs=[
                {
                    "agent_id": self._agent_id,
                    "purpose": str(request.purpose),
                    "prompt_version": request.prompt_version,
                    "input_hash": input_hash,
                    "schema_name": request.response_schema.__name__,
                    "endpoint": self._settings.redacted(),
                }
            ],
            next_action="llm_completion",
        )

        attempts = 0
        failures: list[dict[str, Any]] = []
        backoff = self._settings.retry_backoff_seconds
        while attempts < self._settings.max_attempts:
            attempts += 1
            try:
                completion = self._provider.complete(request)
                payload = _parse_object(completion.content, request)
                # Check the numerical boundary before schema validation. A
                # schema error is safe to retry; an economic number is a
                # terminal policy violation and must never be retried.
                assert_within_numeric_boundary(payload, agent_id=self._agent_id)
                output = _validate(payload, request)
            except LlmError as error:
                failures.append({**error.as_error_info(), "attempt": attempts})
                if not error.retryable or attempts >= self._settings.max_attempts:
                    self._audit.record_error(
                        run_id=run_id,
                        trace_id=trace_id,
                        actor_type="llm_agent",
                        actor_id=self._agent_id,
                        description=(
                            f"{request.purpose} invocation failed after {attempts} attempt(s)"
                        ),
                        error_info=failures,
                        next_action="fail_stage",
                    )
                    raise LlmInvocationFailed(
                        f"LLM invocation for agent {self._agent_id!r} failed after "
                        f"{attempts} attempt(s): {error}",
                        agent_id=self._agent_id,
                        attempts=attempts,
                        failures=failures,
                    ) from error
                if backoff:
                    # ponytail: a blocking sleep inside the worker thread. Bounded
                    # by max_attempts and retry_backoff_seconds; move to a
                    # scheduled retry message if agents ever run concurrently.
                    time.sleep(backoff)
                    backoff *= 2
                continue

            response = LlmResponse(
                agent_id=self._agent_id,
                provider=self._provider.provider_name,
                purpose=request.purpose,
                prompt_version=request.prompt_version,
                model_name=completion.model_name,
                model_version=completion.model_version,
                schema_name=request.response_schema.__name__,
                input_hash=input_hash,
                output_hash=canonical_hash(output),
                attempts=attempts,
                usage=completion.usage,
                latency_ms=completion.latency_ms,
                output=output,
                completed_at=datetime.now(UTC),
            )
            self._audit.record(
                run_id=run_id,
                trace_id=trace_id,
                actor_type="llm_agent",
                actor_id=self._agent_id,
                action_type=AuditActionType.AGENT_RESPONSE_RECEIVED,
                description=(
                    f"{request.purpose} response accepted from "
                    f"{completion.model_name} in {attempts} attempt(s)"
                ),
                output_refs=[response.audit_reference(), {"retry_failures": failures}],
                next_action="continue_pipeline",
            )
            return response

        raise LlmInvocationFailed(  # pragma: no cover - max_attempts is >= 1
            f"LLM invocation for agent {self._agent_id!r} produced no attempt",
            agent_id=self._agent_id,
            attempts=attempts,
            failures=failures,
        )


__all__ = ["LlmGateway"]
