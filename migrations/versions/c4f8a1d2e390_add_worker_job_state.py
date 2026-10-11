"""add durable simulation worker job state

Revision ID: c4f8a1d2e390
Revises: 7a2c9d1e4f60
Create Date: 2026-11-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a1d2e390"
down_revision: str | None = "7a2c9d1e4f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add retry and broker-delivery metadata to simulation runs."""

    op.add_column(
        "simulation_runs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "simulation_runs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "simulation_runs",
        sa.Column("active_job_id", sa.String(length=128), nullable=True),
    )
    op.execute("UPDATE simulation_runs SET status = 'succeeded' WHERE status = 'completed'")


def downgrade() -> None:
    """Remove worker metadata and restore the legacy success status."""

    op.execute("UPDATE simulation_runs SET status = 'completed' WHERE status = 'succeeded'")
    op.drop_column("simulation_runs", "active_job_id")
    op.drop_column("simulation_runs", "max_attempts")
    op.drop_column("simulation_runs", "attempt_count")
