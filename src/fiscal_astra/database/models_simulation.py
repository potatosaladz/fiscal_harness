"""Simulation-related persistence models.

These tables implement the run-manifest and provenance requirements from
``04-output-audit-schema.md`` §2–§3 and the simulation-agent contract in
``02-fiscal-astra-simulation.md``. Large time-series payloads are never stored
as a single JSON column; they are normalised into ``data_series`` /
``simulation_result_values`` rows or referenced as object-storage artifacts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fiscal_astra.database.base import Base, ProvenanceMixin, TimestampMixin, json_type
from fiscal_astra.database.enums import (
    NumericalStatus,
    OrchestratorState,
    ProvenanceType,
    RunStage,
    RunStatus,
    SimulationModelStatus,
)

if TYPE_CHECKING:
    from fiscal_astra.database.models_evidence import AuditEvent, Recommendation
    from fiscal_astra.database.models_scenario import ScenarioVersion


class DataSource(Base, TimestampMixin):
    """A registered, verifiable source of input data.

    A data source is the thing being cited (a statistical agency series
    catalogue, a paper, a model documentation page). Snapshots are taken from
    it; individual observed series are attached to a snapshot.
    """

    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict
    )

    snapshots: Mapped[list[DataSnapshot]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class DataSnapshot(Base, TimestampMixin):
    """An immutable, content-addressed snapshot of input data.

    The snapshot row records the vintage and checksum of the exact bytes used
    by a run, so a later revision of the underlying source cannot silently
    change historical results. Large payloads are stored in object storage and
    referenced here; only metadata lives in the database.
    """

    __tablename__ = "data_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    vintage: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    period_start: Mapped[str | None] = mapped_column(String(50), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bucket: Mapped[str | None] = mapped_column(String(200), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict
    )
    transformation_history: Mapped[list[Any]] = mapped_column(
        json_type(), nullable=False, default=list
    )
    validation: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("source_id", "vintage", name="uq_data_snapshot_source_vintage"),
    )

    source: Mapped[DataSource] = relationship(back_populates="snapshots")
    series: Mapped[list[DataSeries]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    runs: Mapped[list[SimulationRun]] = relationship(secondary="simulation_run_snapshots")


class DataSeries(Base, ProvenanceMixin, TimestampMixin):
    """A named, typed series of observations.

    Series metadata (unit, frequency, period coverage) lives here; the
    observations themselves are stored either as normalised rows in
    ``data_series_values`` for queryable volumes, or as an object-storage
    artifact for very large arrays. Neither case stores the whole series as one
    JSON column.
    """

    __tablename__ = "data_series"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    series_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observation_count: Mapped[int | None] = mapped_column(nullable=True)
    bucket: Mapped[str | None] = mapped_column(String(200), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period_start: Mapped[str | None] = mapped_column(String(50), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(50), nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("snapshot_id", "code", name="uq_data_series_snapshot_code"),)

    snapshot: Mapped[DataSnapshot] = relationship(back_populates="series")
    values: Mapped[list[DataSeriesValue]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )


class DataSeriesValue(Base):
    """One observation of a data series, normalised for indexed access."""

    __tablename__ = "data_series_values"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_series.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)

    __table_args__ = (UniqueConstraint("series_id", "period", name="uq_data_series_value_period"),)

    series: Mapped[DataSeries] = relationship(back_populates="values")


association_simulation_run_snapshot = Table(
    "simulation_run_snapshots",
    Base.metadata,
    Column(
        "simulation_run_id",
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "data_snapshot_id",
        UUID(as_uuid=True),
        ForeignKey("data_snapshots.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class SimulationRun(Base, TimestampMixin):
    """One orchestrated simulation job for a scenario version.

    A run records *that* an engine was invoked, plus the reproducibility
    envelope (seed, code version, data snapshots, solver settings). Numeric
    payloads live in :class:`SimulationResult` and object-storage artifacts.
    """

    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenario_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStage.RECEIVED.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.PENDING.value)
    correlation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: uuid.uuid4().hex, index=True
    )
    orchestrator_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=OrchestratorState.RECEIVED.value,
        index=True,
    )
    completed_stages: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    active_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    random_seed: Mapped[int | None] = mapped_column(nullable=True)
    code_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    solver_settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    solver_diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProvenanceType.SCENARIO.value
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    scenario_version: Mapped[ScenarioVersion] = relationship(back_populates="simulation_runs")
    data_snapshots: Mapped[list[DataSnapshot]] = relationship(
        secondary=association_simulation_run_snapshot, back_populates="runs"
    )
    models: Mapped[list[SimulationModel]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    results: Mapped[list[SimulationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[SimulationArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    recommendations: Mapped[list[Recommendation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="run")


class SimulationModel(Base, TimestampMixin):
    """A model (HANK, ABM, DSGE, VAR, ...) executed inside a simulation run."""

    __tablename__ = "simulation_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_family: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SimulationModelStatus.NOT_EXECUTED.value,
    )
    random_seed: Mapped[int | None] = mapped_column(nullable=True)
    convergence_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    residual_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    limitations: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "name", "version", name="uq_simulation_model_run"),
    )

    run: Mapped[SimulationRun] = relationship(back_populates="models")
    parameters: Mapped[list[ModelParameter]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )


class ModelParameter(Base, ProvenanceMixin, TimestampMixin):
    """A calibrated or assumed parameter belonging to a simulation model.

    Every parameter carries its estimation method, source citation and a
    transferability note so cross-country reuse of literature values is
    explicit rather than silent.
    """

    __tablename__ = "model_parameters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[float] = mapped_column(nullable=False)
    unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    interval_low: Mapped[float | None] = mapped_column(nullable=True)
    interval_high: Mapped[float | None] = mapped_column(nullable=True)
    estimation_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    transferability_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("model_id", "name", name="uq_model_parameter_model_name"),)

    model: Mapped[SimulationModel] = relationship(back_populates="parameters")


class SimulationResult(Base, ProvenanceMixin, TimestampMixin):
    """A named scalar or time-series output produced by a simulation model.

    This table stores the *identity and provenance* of a result (what series,
    what model, what uncertainty). The observations themselves are kept in
    :class:`SimulationResultValue` so that large vectors are not crammed into
    one JSON column.
    """

    __tablename__ = "simulation_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    horizon: Mapped[int | None] = mapped_column(nullable=True)
    numeric_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NumericalStatus.NOT_EXECUTED.value
    )
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_verification: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uncertainty_low: Mapped[float | None] = mapped_column(nullable=True)
    uncertainty_high: Mapped[float | None] = mapped_column(nullable=True)
    uncertainty_distribution: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uncertainty_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[SimulationRun] = relationship(back_populates="results")
    model: Mapped[SimulationModel | None] = relationship()
    series_values: Mapped[list[SimulationResultValue]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class SimulationResultValue(Base):
    """One observation of a time-series simulation result.

    Normalising series into ``(result_id, period, value)`` rows keeps large
    outputs out of a single JSON column and allows indexed range queries.
    """

    __tablename__ = "simulation_result_values"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("result_id", "period", name="uq_simulation_result_value_period"),
    )

    result: Mapped[SimulationResult] = relationship(back_populates="series_values")


class SimulationArtifact(Base, TimestampMixin):
    """Pointer to a large payload stored in S3-compatible object storage.

    The database stores the bucket/key reference and checksum, never the bytes.
    """

    __tablename__ = "simulation_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    bucket: Mapped[str] = mapped_column(String(200), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("bucket", "object_key", name="uq_simulation_artifact_object"),
    )

    run: Mapped[SimulationRun] = relationship(back_populates="artifacts")
    model: Mapped[SimulationModel | None] = relationship()
    result: Mapped[SimulationResult | None] = relationship()


IMMUTABLE_SNAPSHOT_MESSAGE = (
    "Data snapshots are immutable and cannot be modified or deleted once persisted"
)


@event.listens_for(DataSnapshot, "before_update")
def _reject_data_snapshot_update(*_: Any) -> None:
    """Reject in-process mutation of a persisted data snapshot."""

    from sqlalchemy import inspect
    from sqlalchemy.orm import object_session

    target = _[-1]
    session = object_session(target)
    state = inspect(target)
    changed_persisted_fields = any(
        attribute.history.has_changes()
        for attribute in state.attrs
        if attribute.key != "updated_at"
    )
    if session is not None and target in session.identity_map.values() and changed_persisted_fields:
        raise ValueError(IMMUTABLE_SNAPSHOT_MESSAGE)


@event.listens_for(DataSnapshot, "before_delete")
def _reject_data_snapshot_delete(*_: Any) -> None:
    """Reject in-process deletion of a persisted data snapshot."""

    from sqlalchemy.orm import object_session

    target = _[-1]
    session = object_session(target)
    if session is not None and target in session.identity_map.values():
        raise ValueError(IMMUTABLE_SNAPSHOT_MESSAGE)
