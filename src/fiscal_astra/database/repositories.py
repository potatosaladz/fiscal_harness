"""Thin repository layer over SQLAlchemy sessions.

Repositories centralize ORM plumbing so the service layer stays free of
SQLAlchemy idioms while tests can stub the same interface. Each method keeps a
narrow responsibility and returns domain objects or primitives directly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiscal_astra.database.enums import ProvenanceType, RunStatus
from fiscal_astra.database.models_scenario import Scenario, ScenarioVersion
from fiscal_astra.database.models_simulation import (
    DataSeries,
    DataSeriesValue,
    DataSnapshot,
    DataSource,
    SimulationRun,
)


def canonical_spec_hash(instrument_settings: dict[str, Any], assumptions: list[Any]) -> str:
    """Return a deterministic SHA-256 over a scenario version's canonical spec.

    The hash is computed over sorted JSON so reordering keys/assumptions does
    not change the value for semantically identical specs.
    """

    payload = json.dumps(
        {"instrument_settings": instrument_settings, "assumptions": assumptions},
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ScenarioRepository:
    """CRUD operations for scenarios and their versions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_scenario(
        self,
        slug: str,
        title: str,
        description: str | None = None,
        jurisdiction: str | None = None,
        reference_period: str | None = None,
        cutoff_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
        scenario_id: uuid.UUID | None = None,
    ) -> Scenario:
        scenario = Scenario(
            id=scenario_id or uuid.uuid4(),
            slug=slug,
            title=title,
            description=description,
            jurisdiction=jurisdiction,
            reference_period=reference_period,
            cutoff_at=cutoff_at,
            provenance_type=ProvenanceType.SCENARIO.value,
            provenance=provenance or {},
        )
        self._session.add(scenario)
        self._session.flush()
        return scenario

    def get_scenario(self, scenario_id: Any) -> Scenario | None:
        return self._session.get(Scenario, scenario_id)

    def get_scenario_by_slug(self, slug: str) -> Scenario | None:
        stmt = select(Scenario).where(Scenario.slug == slug)
        return self._session.execute(stmt).scalar_one_or_none()

    def latest_version(self, scenario: Scenario) -> ScenarioVersion | None:
        stmt = (
            select(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == scenario.id)
            .order_by(ScenarioVersion.version_number.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def create_version(
        self,
        scenario: Scenario,
        version_number: int,
        instrument_settings: dict[str, Any],
        assumptions: list[Any],
        label: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> ScenarioVersion:
        spec_hash = canonical_spec_hash(instrument_settings, assumptions)
        version = ScenarioVersion(
            scenario_id=scenario.id,
            version_number=version_number,
            spec_hash=spec_hash,
            label=label,
            instrument_settings=instrument_settings,
            assumptions=assumptions,
            provenance=provenance or {},
        )
        self._session.add(version)
        self._session.flush()
        return version

    def next_version_number(self, scenario: Scenario) -> int:
        latest = self.latest_version(scenario)
        return (latest.version_number + 1) if latest else 1


class SimulationRunRepository:
    """Minimal repository for scheduling and tracking simulation runs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_run(
        self,
        scenario_version: ScenarioVersion,
        label: str,
        random_seed: int | None = None,
        code_version: str | None = None,
        run_id: uuid.UUID | None = None,
    ) -> SimulationRun:
        run = SimulationRun(
            id=run_id or uuid.uuid4(),
            scenario_version_id=scenario_version.id,
            label=label,
            random_seed=random_seed,
            code_version=code_version,
            status=RunStatus.PENDING.value,
            provenance={
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get_run(self, run_id: Any) -> SimulationRun | None:
        return self._session.get(SimulationRun, run_id)


class DataSnapshotRepository:
    """Repository for data source and snapshot registration."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def register_source(
        self,
        code: str,
        name: str,
        kind: str,
        verification_status: str,
        source_metadata: dict[str, Any] | None = None,
        publisher: str | None = None,
        url: str | None = None,
        citation: str | None = None,
        retrieval_notes: str | None = None,
    ) -> DataSource:
        source = DataSource(
            code=code,
            name=name,
            kind=kind,
            publisher=publisher,
            url=url,
            citation=citation,
            retrieval_notes=retrieval_notes,
            verification_status=verification_status,
            source_metadata=source_metadata or {},
        )
        self._session.add(source)
        self._session.flush()
        return source

    def create_snapshot(
        self,
        source: DataSource,
        vintage: str,
        row_count: int | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        bucket: str | None = None,
        object_key: str | None = None,
        checksum_sha256: str | None = None,
        size_bytes: int | None = None,
        description: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        transformation_history: list[Any] | None = None,
        validation: dict[str, Any] | None = None,
    ) -> DataSnapshot:
        snapshot = DataSnapshot(
            source_id=source.id,
            vintage=vintage,
            row_count=row_count,
            period_start=period_start,
            period_end=period_end,
            bucket=bucket,
            object_key=object_key,
            checksum_sha256=checksum_sha256,
            size_bytes=size_bytes,
            description=description,
            source_metadata=source_metadata or {},
            transformation_history=transformation_history or [],
            validation=validation or {},
        )
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def add_series_with_values(
        self,
        snapshot: DataSnapshot,
        code: str,
        name: str,
        observations: Sequence[tuple[str, float]],
        unit: str | None = None,
        frequency: str | None = None,
        provenance_type: str = "observed",
        provenance: dict[str, Any] | None = None,
    ) -> DataSeries:
        series = DataSeries(
            snapshot_id=snapshot.id,
            code=code,
            name=name,
            unit=unit,
            frequency=frequency,
            observation_count=len(observations),
            provenance_type=provenance_type,
            provenance=provenance or {},
        )
        self._session.add(series)
        self._session.flush()
        for period, value in observations:
            self._session.add(
                DataSeriesValue(series_id=series.id, period=period, value=float(value))
            )
        self._session.flush()
        return series

    def get_series(self, series_id: Any) -> DataSeries | None:
        return self._session.get(DataSeries, series_id)

    def get_series_values(self, series: DataSeries) -> list[tuple[str, float]]:
        stmt = (
            select(DataSeriesValue.period, DataSeriesValue.value)
            .where(DataSeriesValue.series_id == series.id)
            .order_by(DataSeriesValue.period)
        )
        return [(p, float(v)) for p, v in self._session.execute(stmt).all()]

    def snapshot_summary(self, snapshot: DataSnapshot) -> dict[str, Any]:
        """Return a lightweight count summary for audit without loading blobs."""
        stmt = select(DataSource).where(DataSource.id == snapshot.source_id)
        source = self._session.execute(stmt).scalar_one()
        return {
            "snapshot_id": str(snapshot.id),
            "source_code": source.code,
            "vintage": snapshot.vintage,
            "row_count": snapshot.row_count,
        }

    def attach_run(self, run: SimulationRun, snapshot: DataSnapshot) -> None:
        if snapshot not in run.data_snapshots:
            run.data_snapshots.append(snapshot)
            self._session.flush()

    def create_run_repository_link(
        self,
        run: SimulationRun,
        snapshot: DataSnapshot,
    ) -> None:
        """Append a snapshot to a run's snapshot set.

        The association is a many-to-many through
        ``simulation_run_snapshots``; the relationship is loaded so callers can
        also iterate ``run.data_snapshots``.
        """
        current = {s.id for s in run.data_snapshots}
        if snapshot.id not in current:
            run.data_snapshots.append(snapshot)
            self._session.flush()
