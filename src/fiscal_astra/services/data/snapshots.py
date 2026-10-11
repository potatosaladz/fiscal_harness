"""Immutable snapshot service with checksum verification and audit trail."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from fiscal_astra.database.models_simulation import DataSnapshot, DataSource
from fiscal_astra.database.repositories import DataSnapshotRepository
from fiscal_astra.services.data.errors import SnapshotConflictError
from fiscal_astra.services.data.models import (
    RetrievedDataset,
    RetrievedSeries,
    series_to_json,
)


class SnapshotService:
    """Persist and verify immutable snapshots with full provenance.

    Every snapshot embeds its derivation from a specific source, retrieval
    timestamp, raw checksum, size, and transformation steps. Once persisted,
    snapshots cannot be updated or deleted (enforced by ORM event listeners),
    ensuring that every later query can trust the immutable base.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = DataSnapshotRepository(session)

    def record_snapshot(
        self,
        source_id: uuid.UUID,
        retrieved_dataset: RetrievedDataset,
        *,
        trace_id: str | None = None,
    ) -> DataSnapshot:
        """Persist a snapshot with full provenance and return its identity.

        The snapshot captures the raw checksum, size, and transformation history
        from the retrieval engine. Series are normalised into rows for queryable
        access. Once committed, the snapshot cannot be modified or deleted.
        """

        source = self.session.get(DataSource, source_id)
        if source is None:
            raise ValueError(f"source {source_id} not found")

        period_start, period_end = _infer_coverage(retrieved_dataset)
        vintage = (
            f"{retrieved_dataset.retrieved_at.isoformat()}:{retrieved_dataset.checksum_sha256[:12]}"
        )
        snapshot = self.repository.create_snapshot(
            source=source,
            vintage=vintage,
            row_count=retrieved_dataset.row_count,
            period_start=period_start,
            period_end=period_end,
            checksum_sha256=retrieved_dataset.checksum_sha256,
            size_bytes=retrieved_dataset.size_bytes,
            description="Immutable retrieved dataset snapshot",
            source_metadata={
                "trace_id": trace_id,
                "series_count": len(retrieved_dataset.series),
                "row_count": retrieved_dataset.row_count,
                "source_metadata": retrieved_dataset.source_metadata,
                "data_series": series_to_json(retrieved_dataset.series),
            },
            transformation_history=[
                dict(step) for step in retrieved_dataset.transformation_history
            ],
            validation={
                "status": "pending",
                "access_status": "accessible",
                "recorded_at": datetime.now(UTC).isoformat(),
            },
        )

        for retrieved_series in retrieved_dataset.series:
            series = self.repository.add_series_with_values(
                snapshot=snapshot,
                code=retrieved_series.code,
                name=retrieved_series.name,
                observations=[
                    (observation.period, observation.value)
                    for observation in retrieved_series.observations
                ],
                unit=retrieved_series.unit,
                frequency=(str(retrieved_series.frequency) if retrieved_series.frequency else None),
                provenance_type=retrieved_series.provenance_type,
                provenance={
                    **retrieved_series.provenance,
                    "checksum_sha256": _series_checksum(retrieved_series),
                    "period_start": _first_period(retrieved_series),
                    "period_end": _last_period(retrieved_series),
                    "as_of": retrieved_dataset.retrieved_at.isoformat(),
                },
            )

            series.checksum_sha256 = _series_checksum(retrieved_series)
            series.period_start = _first_period(retrieved_series)
            series.period_end = _last_period(retrieved_series)
            series.as_of = retrieved_dataset.retrieved_at

        self.session.flush()
        return snapshot

    def verify_snapshot(self, snapshot_id: uuid.UUID, payload: bytes) -> bool:
        """Verify that a snapshot's checksum matches a recomputed checksum.

        Returns True if the checksums match. Raises SnapshotConflictError if
        the snapshot cannot be found or the checksums do not match.
        """

        snapshot = self.session.get(DataSnapshot, snapshot_id)
        if snapshot is None:
            raise SnapshotConflictError(
                f"snapshot {snapshot_id} not found", snapshot_id=str(snapshot_id)
            )

        from fiscal_astra.services.data.models import compute_checksum

        expected = compute_checksum(payload)
        if snapshot.checksum_sha256 != expected:
            raise SnapshotConflictError(
                "snapshot checksum mismatch: "
                f"stored={snapshot.checksum_sha256}, computed={expected}",
                snapshot_id=str(snapshot_id),
            )
        return True

    def with_snapshot_metadata(self, snapshot_id: uuid.UUID) -> dict[str, Any]:
        """Return immutable metadata for audit trail provenance."""

        snapshot = self.session.get(DataSnapshot, snapshot_id)
        if snapshot is None:
            return {"status": "not_found", "snapshot_id": str(snapshot_id)}

        return {
            "status": "found",
            "snapshot_id": str(snapshot.id),
            "source_id": str(snapshot.source_id),
            "vintage": snapshot.vintage,
            "checksum_sha256": snapshot.checksum_sha256,
            "size_bytes": snapshot.size_bytes,
            "series_count": len(snapshot.series),
            "row_count": snapshot.row_count,
            "period_coverage": {"start": snapshot.period_start, "end": snapshot.period_end},
            "validation": snapshot.validation,
        }


def _infer_coverage(dataset: RetrievedDataset) -> tuple[str | None, str | None]:
    """Infer period range across all series."""

    starts = []
    ends = []
    for series in dataset.series:
        start, end = series.period_range()
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    return (min(starts) if starts else None, max(ends) if ends else None)


def _series_checksum(series: RetrievedSeries) -> str:
    """Return a deterministic checksum for normalised series observations."""

    payload = json.dumps(
        {
            "code": series.code,
            "unit": series.unit,
            "frequency": str(series.frequency) if series.frequency else None,
            "observations": [
                {"period": observation.period, "value": observation.value}
                for observation in series.observations
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _first_period(series: RetrievedSeries) -> str | None:
    """Return the first period in a series, or None."""

    if not series.observations:
        return None
    return series.observations[0].period


def _last_period(series: RetrievedSeries) -> str | None:
    """Return the last period in a series, or None."""

    if not series.observations:
        return None
    return series.observations[-1].period


__all__ = ["SnapshotService"]
