"""Local LLM provider abstraction: contracts, providers, and the audited gateway."""

from fiscal_astra.llm.contracts import (
    LlmCompletion,
    LlmPurpose,
    LlmRequest,
    LlmResponse,
    LlmUsage,
)
from fiscal_astra.llm.gateway import LlmGateway
from fiscal_astra.llm.mock import MockLlmProvider
from fiscal_astra.llm.openai_compatible import OpenAiCompatibleProvider
from fiscal_astra.llm.protocols import LlmProvider

__all__ = [
    "LlmCompletion",
    "LlmGateway",
    "LlmProvider",
    "LlmPurpose",
    "LlmRequest",
    "LlmResponse",
    "LlmUsage",
    "MockLlmProvider",
    "OpenAiCompatibleProvider",
]
