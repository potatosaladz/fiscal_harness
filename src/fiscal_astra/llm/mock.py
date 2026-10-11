"""Deterministic in-process provider for tests and offline deployments.

The mock never invents a completion. With an empty script it fails loudly, so a
deployment that forgot to configure a real endpoint reports a failure instead of
producing fabricated agent text.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from fiscal_astra.llm.contracts import LlmCompletion, LlmRequest, LlmUsage
from fiscal_astra.llm.errors import LlmTransportError

ScriptedResponse = str | Mapping[str, Any] | Exception


class MockLlmProvider:
    """Replay scripted responses in order; no network and no API key."""

    provider_name = "mock"

    def __init__(
        self,
        responses: Iterable[ScriptedResponse] = (),
        *,
        model_name: str = "mock-model",
        model_version: str = "mock-1",
        usage: LlmUsage | None = None,
        latency_ms: int = 0,
        finish_reason: str = "stop",
    ) -> None:
        self._responses = list(responses)
        self._usage = usage
        self._latency_ms = latency_ms
        self._finish_reason = finish_reason
        self.model_name = model_name
        self.model_version = model_version
        self.requests: list[LlmRequest] = []

    @property
    def remaining(self) -> int:
        """Number of scripted responses that have not been consumed yet."""

        return len(self._responses)

    def complete(self, request: LlmRequest) -> LlmCompletion:
        """Return the next scripted response, or raise a non-retryable error."""

        self.requests.append(request)
        if not self._responses:
            raise LlmTransportError(
                "mock provider has no scripted response; configure a real LLM endpoint",
                agent_id=request.agent_id,
                retryable=False,
            )
        scripted = self._responses.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        content = scripted if isinstance(scripted, str) else json.dumps(scripted)
        return LlmCompletion(
            content=content,
            model_name=self.model_name,
            model_version=self.model_version,
            usage=self._usage or LlmUsage(),
            latency_ms=self._latency_ms,
            finish_reason=self._finish_reason,
        )


__all__ = ["MockLlmProvider", "ScriptedResponse"]
