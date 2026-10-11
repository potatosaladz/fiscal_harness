"""Per-agent local-LLM configuration.

Every agent (orchestrator, policy sub-agent, simulation agent) may point at a
different endpoint, model, and credential. An agent's configuration is the
process-wide default with its own overrides layered on top, so one agent can
run a small local model while another runs a larger one.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class LlmAgentSettings(BaseModel):
    """Endpoint, credential, retry, and prompt policy for one agent."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="mock", min_length=1, max_length=64)
    base_url: str | None = None
    model: str | None = None
    model_version: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=60)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    json_mode: bool = True
    prompt_version: str = Field(default="v1", min_length=1, max_length=64)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        """Reject non-HTTP endpoints and credentials embedded in the URL."""

        if value is None or not value.strip():
            return None
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an http(s) URL with a hostname")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not embed credentials; supply the API key separately")
        return value.rstrip("/")

    def redacted(self) -> dict[str, Any]:
        """Return a log/audit-safe view: presence of a key, never its value."""

        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "model_version": self.model_version,
            "key_configured": self.api_key is not None,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "json_mode": self.json_mode,
            "prompt_version": self.prompt_version,
        }


__all__ = ["LlmAgentSettings"]
