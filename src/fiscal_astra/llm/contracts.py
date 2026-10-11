"""Typed contracts exchanged with a local LLM provider.

The boundary is deliberately narrow: a local model plans, interprets,
deliberates, and drafts prose. It never produces a fiscal or macroeconomic
number, so nothing in this module can carry a numerical claim. Numbers reach a
report only through a simulation-engine result (``02-fiscal-astra-simulation.md``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LlmPurpose(StrEnum):
    """The only reasons an agent may call a local model.

    This enumeration *is* the numerical boundary: it has no member for
    computing an economic value, so an invocation cannot be labelled as one.
    """

    PLANNING = "planning"
    INTERPRETATION = "interpretation"
    DELIBERATION = "deliberation"
    EVIDENCE_REVIEW = "evidence_review"
    REPORTING = "reporting"


class LlmUsage(BaseModel):
    """Token accounting reported by the endpoint; ``None`` when unavailable."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class LlmRequest(BaseModel):
    """One structured-output request for a single agent."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    agent_id: str = Field(min_length=1, max_length=128)
    purpose: LlmPurpose
    prompt_version: str = Field(min_length=1, max_length=64)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    response_schema: type[BaseModel]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    json_mode: bool = True

    def canonical_input(self) -> dict[str, Any]:
        """Return the JSON-safe fields that define this invocation's identity."""

        return {
            "agent_id": self.agent_id,
            "purpose": str(self.purpose),
            "prompt_version": self.prompt_version,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "response_schema": self.response_schema.__name__,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "json_mode": self.json_mode,
        }


class LlmCompletion(BaseModel):
    """Raw, still-unvalidated completion returned by a provider."""

    model_config = ConfigDict(extra="forbid")

    content: str
    model_name: str
    model_version: str
    usage: LlmUsage = Field(default_factory=LlmUsage)
    latency_ms: int = Field(ge=0)
    finish_reason: str | None = None


class LlmResponse(BaseModel):
    """Validated structured output of one invocation, with its provenance."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    provider: str
    purpose: LlmPurpose
    prompt_version: str
    model_name: str
    model_version: str
    schema_name: str
    input_hash: str
    output_hash: str
    attempts: int = Field(ge=1)
    usage: LlmUsage
    latency_ms: int = Field(ge=0)
    output: dict[str, Any]
    completed_at: datetime

    def audit_reference(self) -> dict[str, Any]:
        """Return the JSON-safe provenance written to the audit trail.

        Prompts are represented by their hash rather than their text so the
        audit trail records *what was asked* without storing case content.
        """

        return {
            "agent_id": self.agent_id,
            "provider": self.provider,
            "purpose": str(self.purpose),
            "prompt_version": self.prompt_version,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "schema_name": self.schema_name,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "attempts": self.attempts,
            "usage": self.usage.model_dump(),
            "latency_ms": self.latency_ms,
        }


def canonical_hash(payload: Any) -> str:
    """Return a deterministic SHA-256 over a JSON-canonical payload."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
