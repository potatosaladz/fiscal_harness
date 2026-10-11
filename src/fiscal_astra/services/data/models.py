"""Typed contracts shared by retrieval adapters and the snapshot service.

The retrieval layer never performs economic computation. It parses provider
bytes into typed, characterised observations and records where those bytes came
from, so that every downstream number can trace back to an immutable snapshot
and a verifiable origin.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from fiscal_astra.database.enums import AccessStatus, SeriesFrequency


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """A provider-independent description of the dataset to retrieve.

    ``location`` is interpreted by the adapter: a filesystem path for the local
    adapters, a URL for the remote adapter, and either an identifier or raw
    payload for the upload adapter. It is stored verbatim in snapshot metadata
    so the origin remains auditable.
    """

    location: str
    provider: str
    media_type: str | None = None
    dataset_name: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievedObservation:
    """A single typed observation recovered from provider bytes."""

    period: str
    value: float
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievedSeries:
    """A named series with characterising metadata and its observations."""

    code: str
    name: str
    observations: tuple[RetrievedObservation, ...]
    unit: str | None = None
    frequency: SeriesFrequency | str | None = None
    series_kind: str | None = None
    provenance_type: str = "observed"
    provenance: dict[str, Any] = field(default_factory=dict)

    def period_range(self) -> tuple[str | None, str | None]:
        if not self.observations:
            return None, None
        periods = [observation.period for observation in self.observations]
        return min(periods), max(periods)


@dataclass(frozen=True, slots=True)
class RetrievedDataset:
    """The parsed result of one successful retrieval.

    ``checksum_sha256`` is the checksum of the raw provider bytes, not of the
    parsed representation, so an operator can re-verify the immutable snapshot
    against the original artifact.
    """

    series: tuple[RetrievedSeries, ...]
    checksum_sha256: str
    size_bytes: int
    source_metadata: dict[str, Any] = field(default_factory=dict)
    transformation_history: tuple[dict[str, Any], ...] = ()
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def row_count(self) -> int:
        """Total observations across every series."""

        return sum(len(series.observations) for series in self.series)


@runtime_checkable
class DataAdapter(Protocol):
    """Contract every retrieval adapter must satisfy."""

    provider: str
    media_types: tuple[str, ...]

    def supports(self, request: RetrievalRequest) -> bool:
        """Return whether this adapter can service ``request``."""

    def retrieve(self, request: RetrievalRequest) -> RetrievedDataset:
        """Return parsed series or raise a :class:`RetrievalError`."""


def compute_checksum(payload: bytes) -> str:
    """Return the SHA-256 hex digest of raw provider bytes."""

    return hashlib.sha256(payload).hexdigest()


def canonical_transformation(step: str, **details: Any) -> dict[str, Any]:
    """Build one transformation-history entry in a stable shape."""

    entry: dict[str, Any] = {"step": step, "at": datetime.now(UTC).isoformat()}
    entry.update(details)
    return entry


def new_trace_id() -> str:
    """Return a correlation identifier for a retrieval attempt."""

    return str(uuid.uuid4())


def series_to_json(series: Iterable[RetrievedSeries]) -> list[dict[str, Any]]:
    """Render series as JSON-safe metadata (no numeric payload duplication)."""

    return [
        {
            "code": item.code,
            "name": item.name,
            "unit": item.unit,
            "frequency": str(item.frequency) if item.frequency is not None else None,
            "observation_count": len(item.observations),
            "provenance_type": item.provenance_type,
        }
        for item in series
    ]


__all__ = [
    "AccessStatus",
    "DataAdapter",
    "RetrievalRequest",
    "RetrievedDataset",
    "RetrievedObservation",
    "RetrievedSeries",
    "canonical_transformation",
    "compute_checksum",
    "new_trace_id",
    "series_to_json",
]
