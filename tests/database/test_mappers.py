"""Regression tests for SQLAlchemy mapper configuration."""

from __future__ import annotations

from sqlalchemy.orm import configure_mappers

from fiscal_astra import database


def test_database_mappers_configure() -> None:
    """All imported ORM relationships must resolve their reciprocal mappings."""
    assert database.Base.metadata.tables
    configure_mappers()
