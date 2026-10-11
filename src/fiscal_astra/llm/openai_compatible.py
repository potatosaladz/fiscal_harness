"""OpenAI-compatible chat-completions provider.

Talks to any endpoint that implements ``POST {base_url}/chat/completions``
(llama.cpp, vLLM, Ollama, LM Studio, LiteLLM, ...). The standard library is
sufficient here, so no HTTP client dependency is added.

The API key is read from the environment at construction time and is held only
in memory: it is never logged, never placed in the audit trail, and never
written to an artifact. Only a redacted fingerprint could ever be surfaced.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from fiscal_astra.llm.contracts import LlmCompletion, LlmRequest, LlmUsage
from fiscal_astra.llm.errors import (
    LlmConfigurationError,
    LlmResponseError,
    LlmTimeoutError,
    LlmTransportError,
)

# Status codes that describe a transient condition worth retrying.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class OpenAiCompatibleProvider:
    """Provider for an OpenAI-compatible ``/chat/completions`` endpoint."""

    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        model_version: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not base_url:
            raise LlmConfigurationError("an OpenAI-compatible endpoint requires a base URL")
        if not model_name:
            raise LlmConfigurationError("an OpenAI-compatible endpoint requires a model name")
        if not model_version:
            raise LlmConfigurationError(
                "an OpenAI-compatible endpoint requires an explicit model version"
            )
        self._base_url = base_url.rstrip("/")
        self._endpoint = f"{self._base_url}/chat/completions"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self.model_name = model_name
        self.model_version = model_version

    def complete(self, request: LlmRequest) -> LlmCompletion:
        """Send one chat completion and map HTTP failures to typed errors."""

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            # Sent per request; never stored on an object that gets logged.
            headers["Authorization"] = f"Bearer {self._api_key}"

        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint, data=body, headers=headers, method="POST"
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = self._short(error.read())
            raise LlmTransportError(
                f"endpoint returned HTTP {error.code}: {detail}",
                agent_id=request.agent_id,
                status_code=error.code,
                retryable=error.code in _RETRYABLE_STATUS,
            ) from error
        except TimeoutError as error:
            raise LlmTimeoutError(
                f"endpoint exceeded {self._timeout_seconds}s timeout",
                agent_id=request.agent_id,
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise LlmTransportError(
                f"endpoint unreachable: {error}", agent_id=request.agent_id
            ) from error

        return self._parse(raw, request, latency_ms=round((time.monotonic() - started) * 1000))

    def _parse(self, raw: bytes, request: LlmRequest, *, latency_ms: int) -> LlmCompletion:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LlmResponseError(
                f"endpoint returned a non-JSON body: {error}", agent_id=request.agent_id
            ) from error
        if not isinstance(document, dict):
            raise LlmResponseError(
                "endpoint returned a JSON value that is not an object", agent_id=request.agent_id
            )

        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlmResponseError("completion has no choices", agent_id=request.agent_id)
        first = choices[0]
        if not isinstance(first, dict):
            raise LlmResponseError("completion choice is not an object", agent_id=request.agent_id)
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LlmResponseError(
                "completion choice carries no message content", agent_id=request.agent_id
            )

        usage_document = document.get("usage")
        usage = (
            LlmUsage.model_validate(usage_document)
            if isinstance(usage_document, dict)
            else LlmUsage()
        )
        # The endpoint's own model identifier wins over the configured alias:
        # the audit trail must record what actually answered.
        reported_model = document.get("model")
        model_name = reported_model if isinstance(reported_model, str) else self.model_name
        finish_reason = first.get("finish_reason")
        return LlmCompletion(
            content=content,
            model_name=model_name,
            model_version=self.model_version,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )

    @staticmethod
    def _short(payload: bytes, limit: int = 300) -> str:
        text = payload.decode("utf-8", errors="replace").strip()
        return text if len(text) <= limit else f"{text[:limit]}..."


__all__ = ["OpenAiCompatibleProvider"]
