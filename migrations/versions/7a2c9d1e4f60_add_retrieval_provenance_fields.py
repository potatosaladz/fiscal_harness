"""add retrieval provenance fields

Revision ID: 7a2c9d1e4f60
Revises: 35945b65a745
Create Date: 2026-10-11 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7a2c9d1e4f60"
down_revision: str | None = "7b4c6d8e9f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    """Add retrieval metadata, transformation history, and access checksums."""

    op.add_column(
        "data_sources",
        sa.Column("source_metadata", JSON_TYPE, nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "data_snapshots",
        sa.Column("source_metadata", JSON_TYPE, nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "data_snapshots",
        sa.Column(
            "transformation_history",
            JSON_TYPE,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "data_snapshots",
        sa.Column("validation", JSON_TYPE, nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "evidence_records",
        sa.Column("content_checksum_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evidence_records",
        sa.Column(
            "access_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'not_checked'"),
        ),
    )


def downgrade() -> None:
    """Remove retrieval metadata fields."""

    op.drop_column("evidence_records", "access_status")
    op.drop_column("evidence_records", "content_checksum_sha256")
    op.drop_column("data_snapshots", "validation")
    op.drop_column("data_snapshots", "transformation_history")
    op.drop_column("data_snapshots", "source_metadata")
    op.drop_column("data_sources", "source_metadata")
