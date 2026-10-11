"""add durable orchestrator state and correlation metadata

Revision ID: d7e9f3a1b642
Revises: c4f8a1d2e390
Create Date: 2026-11-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e9f3a1b642"
down_revision: str | None = "c4f8a1d2e390"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add restart-safe orchestrator position and trace metadata."""

    op.add_column(
        "simulation_runs",
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        "UPDATE simulation_runs SET correlation_id = replace(cast(id AS text), '-', '') "
        "WHERE correlation_id IS NULL"
    )
    op.alter_column("simulation_runs", "correlation_id", nullable=False)
    op.create_index(
        op.f("ix_simulation_runs_correlation_id"),
        "simulation_runs",
        ["correlation_id"],
        unique=False,
    )
    op.add_column(
        "simulation_runs",
        sa.Column("orchestrator_state", sa.String(length=32), nullable=True),
    )
    op.execute(
        "UPDATE simulation_runs SET orchestrator_state = CASE "
        "WHEN status = 'succeeded' THEN 'completed' "
        "WHEN status IN ('failed', 'not_executed') THEN 'failed' "
        "WHEN status = 'cancelled' THEN 'cancelled' "
        "ELSE 'received' END WHERE orchestrator_state IS NULL"
    )
    op.alter_column("simulation_runs", "orchestrator_state", nullable=False)
    op.create_index(
        op.f("ix_simulation_runs_orchestrator_state"),
        "simulation_runs",
        ["orchestrator_state"],
        unique=False,
    )
    op.add_column(
        "simulation_runs",
        sa.Column("completed_stages", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE simulation_runs SET completed_stages = '[]' WHERE completed_stages IS NULL")
    op.alter_column("simulation_runs", "completed_stages", nullable=False)
    op.add_column(
        "simulation_runs",
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Remove orchestrator metadata."""

    op.drop_column("simulation_runs", "error_code")
    op.drop_column("simulation_runs", "completed_stages")
    op.drop_index(op.f("ix_simulation_runs_orchestrator_state"), table_name="simulation_runs")
    op.drop_column("simulation_runs", "orchestrator_state")
    op.drop_index(op.f("ix_simulation_runs_correlation_id"), table_name="simulation_runs")
    op.drop_column("simulation_runs", "correlation_id")
