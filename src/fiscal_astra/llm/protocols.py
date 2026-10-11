"""Structural interface implemented by every local LLM provider."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fiscal_astra.llm.contracts import LlmCompletion, LlmRequest


@runtime_checkable
class LlmProvider(Protocol):
    """Common provider boundary; transport work happens behind this interface."""

    provider_name: str
    model_name: str
    model_version: str

    def complete(self, request: LlmRequest) -> LlmCompletion:
        """Return the raw completion for one request or raise :class:`LlmError`."""
