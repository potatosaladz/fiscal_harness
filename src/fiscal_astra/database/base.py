"""Declarative base and shared column conventions for the persistence layer.

Every entity in Fiscal Astra carries:

* a stable identifier (UUID primary key, ``id``);
* ``created_at`` / ``updated_at`` timestamps where the record is mutable;
* provenance metadata so any stored value can be traced back to its origin.

The helpers here keep those conventions consistent across the schema instead of
repeating them in every model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    MetaData,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

# Naming convention: Alembic autogenerate relies on deterministic constraint
# names to emit stable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def json_type() -> TypeEngine[Any]:
    """Return a JSON column type.

    JSONB is used on PostgreSQL because it is queryable and indexable; other
    dialects (notably SQLite in unit tests) fall back to generic JSON. Large
    time series are *not* stored this way: see
    :mod:`fiscal_astra.database.models` for the normalized series tables.
    """

    return JSONB().with_variant(JSON(), "sqlite")


def uuid_pk() -> Mapped[uuid.UUID]:
    """Return a portable UUID primary-key column definition."""

    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


def created_at_column() -> Mapped[datetime]:
    """Return a server-defaulted ``created_at`` timestamp column."""

    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


def updated_at_column() -> Mapped[datetime]:
    """Return an ``updated_at`` timestamp column maintained on write."""

    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TimestampMixin:
    """Add ``created_at`` / ``updated_at`` columns to a declarative model."""

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ProvenanceMixin:
    """Add provenance-typed metadata columns to a declarative model.

    Provenance is mandatory for every numeric value (``AGENTS.md`` rule 6).
    ``provenance_type`` classifies the value; ``provenance`` carries the free
    structured detail (citation, method, retrieval time, assumptions).
    """

    provenance_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="assumption",
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        json_type(),
        nullable=False,
        default=dict,
    )
