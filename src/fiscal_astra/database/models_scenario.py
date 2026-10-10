"""Scenario and scenario-version entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fiscal_astra.database.base import (
    Base,
    TimestampMixin,
    json_type,
    uuid_pk,
)
from fiscal_astra.database.enums import ProvenanceType

if TYPE_CHECKING:
    from fiscal_astra.database.models_simulation import SimulationRun


class Scenario(Base, TimestampMixin):
    """A durable fiscal-policy scenario container.

    Identity and metadata live here; the concrete policy specification is
    captured on immutable :class:`ScenarioVersion` rows so that any result can
    be reproduced against the exact spec that produced it.
    """

    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provenance_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProvenanceType.SCENARIO.value,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        json_type(),
        nullable=False,
        default=dict,
    )

    versions: Mapped[list[ScenarioVersion]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScenarioVersion.version_number",
    )


class ScenarioVersion(Base, TimestampMixin):
    """An immutable, versioned snapshot of a scenario's policy specification."""

    __tablename__ = "scenario_versions"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id",
            "version_number",
            name="uq_scenario_versions_scenario_id_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instrument_settings: Mapped[dict[str, Any]] = mapped_column(
        json_type(),
        nullable=False,
        default=dict,
    )
    assumptions: Mapped[list[Any]] = mapped_column(
        json_type(),
        nullable=False,
        default=list,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        json_type(),
        nullable=False,
        default=dict,
    )

    scenario: Mapped[Scenario] = relationship(back_populates="versions")
    simulation_runs: Mapped[list[SimulationRun]] = relationship(
        back_populates="scenario_version",
        cascade="all, delete-orphan",
        order_by="SimulationRun.started_at",
    )
