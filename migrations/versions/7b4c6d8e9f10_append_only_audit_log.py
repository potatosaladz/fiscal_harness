"""extend audit events with correlation, actor, references, and hash chain

Revision ID: 7b4c6d8e9f10
Revises: 35945b65a745
Create Date: 2026-10-11 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7b4c6d8e9f10"
down_revision: str | None = "35945b65a745"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_column() -> sa.types.TypeEngine[object]:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("trace_id", sa.String(length=128), nullable=True))
    op.add_column("audit_events", sa.Column("actor_type", sa.String(length=50), nullable=True))
    op.add_column("audit_events", sa.Column("actor_id", sa.String(length=128), nullable=True))
    op.add_column("audit_events", sa.Column("input_refs", _json_column(), nullable=True))
    op.add_column("audit_events", sa.Column("output_refs", _json_column(), nullable=True))
    op.add_column("audit_events", sa.Column("assumption_refs", _json_column(), nullable=True))
    op.add_column("audit_events", sa.Column("evidence_refs", _json_column(), nullable=True))
    op.add_column("audit_events", sa.Column("error_info", _json_column(), nullable=True))
    op.add_column(
        "audit_events", sa.Column("previous_event_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "audit_events", sa.Column("current_event_hash", sa.String(length=64), nullable=True)
    )

    op.execute(
        sa.text(
            """
            UPDATE audit_events
            SET trace_id = CAST(id AS TEXT),
                actor_type = 'legacy',
                actor_id = COALESCE(agent_id, 'unknown'),
                input_refs = inputs,
                output_refs = outputs,
                assumption_refs = assumptions,
                evidence_refs = evidence_ids,
                error_info = errors,
                previous_event_hash = '',
                current_event_hash = ''
            """
        )
    )
    op.alter_column("audit_events", "trace_id", nullable=False)
    op.alter_column("audit_events", "actor_type", nullable=False)
    op.alter_column("audit_events", "actor_id", nullable=False)
    op.alter_column("audit_events", "input_refs", nullable=False)
    op.alter_column("audit_events", "output_refs", nullable=False)
    op.alter_column("audit_events", "assumption_refs", nullable=False)
    op.alter_column("audit_events", "evidence_refs", nullable=False)
    op.alter_column("audit_events", "error_info", nullable=False)
    op.alter_column("audit_events", "previous_event_hash", nullable=False)
    op.alter_column("audit_events", "current_event_hash", nullable=False)
    for column in ("agent_id", "inputs", "outputs", "assumptions", "evidence_ids", "errors"):
        op.drop_column("audit_events", column)
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"], unique=False)

    # PostgreSQL-level guard: audit rows cannot be changed or removed through
    # direct SQL either. The run foreign key remains SET NULL, preserving the
    # audit trail if a run is later removed.
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_audit_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_events_append_only
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_audit_event_mutation()"))
    op.drop_index("ix_audit_events_trace_id", table_name="audit_events")
    for column in (
        "current_event_hash",
        "previous_event_hash",
        "error_info",
        "evidence_refs",
        "assumption_refs",
        "output_refs",
        "input_refs",
        "actor_id",
        "actor_type",
        "trace_id",
    ):
        op.drop_column("audit_events", column)
