"""Guard that keeps local LLM output inside the non-numerical boundary.

``01-orchestrator.md`` and ``AGENTS.md`` both state that a local LLM plans,
orchestrates, interprets, and drafts reports, while every fiscal or
macroeconomic number comes from a simulation engine or a retrieval adapter.
This module makes that rule executable: an agent payload that asserts an
economic quantity, or labels a value as ``observed``/``estimated``/
``model_output``, is rejected before it can reach a report.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from fiscal_astra.llm.errors import LlmBoundaryError

# Keys an agent may legitimately answer with a number: they describe the
# agent's own reasoning (confidence, weighting, ranking), not an economic
# quantity.
_ALLOWED_NUMERIC_KEYS = frozenset(
    {
        "confidence",
        "confidence_level",
        "score",
        "scores",
        "weight",
        "weights",
        "probability",
        "count",
        "rank",
        "index",
        "version",
        "horizon",
        "seed",
    }
)

# Economic quantities an agent must never report on its own authority.
_NUMERIC_CLAIM_KEY = re.compile(
    r"(?:^|_)(value|values|amount|amounts|percent|percentage|pct|rate|rates|ratio|level|gdp|"
    r"debt|revenue|spending|expenditure|multiplier|elasticity|balance|impact|estimate|estimates|"
    r"forecast|projection|projections|number|numeric|figure|total|sum|mean|median|cost|costs|"
    r"tax|subsidy|subsidies|deficit|surplus|inflation|unemployment|gini|poverty)(?:$|_)",
    re.IGNORECASE,
)

# Keys whose string value classifies where a number came from.
_PROVENANCE_KEYS = frozenset(
    {"provenance", "provenance_type", "numeric_status", "provenance_class"}
)
_NUMERIC_PROVENANCE = frozenset(
    {
        "observed",
        "observed_data",
        "estimated",
        "estimated_value",
        "model_output",
        "verified_numeric",
    }
)


def assert_within_numeric_boundary(payload: Any, *, agent_id: str, path: str = "$") -> None:
    """Reject output that asserts an economic number or numeric provenance.

    Violations are terminal by design: retrying a boundary breach would only
    invite a different fabrication, so the caller must fail the invocation.
    """

    if isinstance(payload, Mapping):
        for key, value in payload.items():
            name = str(key)
            lowered = name.lower()
            if (
                lowered in _PROVENANCE_KEYS
                and isinstance(value, str)
                and value.lower() in _NUMERIC_PROVENANCE
            ):
                raise LlmBoundaryError(
                    f"{path}.{name} claims numeric provenance {value!r}; only a simulation "
                    "engine or retrieval adapter may produce a numeric value",
                    agent_id=agent_id,
                )
            if (
                lowered not in _ALLOWED_NUMERIC_KEYS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and _NUMERIC_CLAIM_KEY.search(name)
            ):
                raise LlmBoundaryError(
                    f"{path}.{name} reports an economic quantity ({value!r}); a local LLM may "
                    "not produce a fiscal or macroeconomic number",
                    agent_id=agent_id,
                )
            assert_within_numeric_boundary(value, agent_id=agent_id, path=f"{path}.{name}")
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for position, item in enumerate(payload):
            assert_within_numeric_boundary(item, agent_id=agent_id, path=f"{path}[{position}]")


__all__ = ["assert_within_numeric_boundary"]
