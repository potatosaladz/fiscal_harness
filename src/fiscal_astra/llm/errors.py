"""Typed failures for the local-LLM boundary.

Every failure carries enough context for the append-only audit trail, and every
failure states whether a bounded retry is worth attempting. Retryability is a
property of the failure rather than of the caller, so one place decides it.
"""

from __future__ import annotations

from typing import Any


class LlmError(RuntimeError):
    """Base class for local-LLM failures that must reach the audit trail."""

    retryable = False

    def __init__(self, message: str, *, agent_id: str | None = None) -> None:
        super().__init__(message)
        self.agent_id = agent_id

    def as_error_info(self) -> dict[str, Any]:
        """Return a JSON-safe description for the audit trail."""

        return {
            "type": type(self).__name__,
            "message": str(self),
            "agent_id": self.agent_id,
            "retryable": self.retryable,
        }


class LlmConfigurationError(LlmError):
    """Raised when an agent's LLM configuration is missing or unusable."""


class LlmTransportError(LlmError):
    """Raised when the endpoint could not be reached or refused the request."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        agent_id: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message, agent_id=agent_id)
        self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable

    def as_error_info(self) -> dict[str, Any]:
        return {**super().as_error_info(), "status_code": self.status_code}


class LlmTimeoutError(LlmTransportError):
    """Raised when the endpoint exceeded the configured timeout."""


class LlmResponseError(LlmError):
    """Raised when the endpoint returned content that is not a JSON object."""

    retryable = True


class LlmSchemaError(LlmResponseError):
    """Raised when the completion does not satisfy the requested schema."""


class LlmBoundaryError(LlmError):
    """Raised when LLM output claims a numeric value or numeric provenance.

    Numerical work belongs to a simulation engine, so a model that asserts an
    observed, estimated, or computed number is a boundary violation and never a
    retry candidate: retrying would only invite a different fabrication.
    """


class LlmInvocationFailed(LlmError):
    """Raised after the bounded retry budget is exhausted."""

    def __init__(
        self,
        message: str,
        *,
        agent_id: str | None = None,
        attempts: int = 0,
        failures: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message, agent_id=agent_id)
        self.attempts = attempts
        self.failures = failures or []

    def as_error_info(self) -> dict[str, Any]:
        return {
            **super().as_error_info(),
            "attempts": self.attempts,
            "failures": self.failures,
        }
