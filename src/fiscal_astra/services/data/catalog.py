"""Data catalog: registry of sources, snapshots, and retrieval intent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiscal_astra.database.models_simulation import DataSource
from fiscal_astra.database.repositories import DataSnapshotRepository
from fiscal_astra.services.data.models import RetrievalRequest


class DataCatalog:
    """Registry of data sources and retrieval metadata for audit trail.

    The catalog does not retrieve; it records intent and outcome for later
    audit and traceability. Each source entry carries publisher, URL, citation,
    and verification status so claims can be traced from evidence back to origin.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = DataSnapshotRepository(session)

    def register_source(
        self,
        code: str,
        name: str,
        kind: str,
        verification_status: str,
        *,
        publisher: str | None = None,
        url: str | None = None,
        citation: str | None = None,
        retrieval_notes: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Register a source and return its identity.

        The source will be linked to snapshots and evidence so the full chain
        from claim to origin remains auditable.
        """

        source = self.repository.register_source(
            code=code,
            name=name,
            kind=kind,
            verification_status=verification_status,
            publisher=publisher,
            url=url,
            citation=citation,
            retrieval_notes=retrieval_notes,
            source_metadata=source_metadata,
        )
        return source.id

    def lookup_source(self, code: str) -> DataSource | None:
        """Retrieve source metadata by code."""

        stmt = select(DataSource).where(DataSource.code == code)
        return self.session.execute(stmt).scalar_one_or_none()

    def record_retrieval_intent(
        self,
        source_id: uuid.UUID,
        request: RetrievalRequest,
        trace_id: str,
    ) -> dict[str, Any]:
        """Log retrieval intent for audit trail before attempting retrieval."""

        return {
            "event": "retrieval_intent",
            "at": datetime.now(UTC).isoformat(),
            "trace_id": trace_id,
            "source_id": str(source_id),
            "request": {
                "location": request.location,
                "provider": request.provider,
                "media_type": request.media_type,
                "dataset_name": request.dataset_name,
            },
        }

    def record_retrieval_failure(
        self,
        source_id: uuid.UUID,
        trace_id: str,
        error_info: dict[str, Any],
    ) -> dict[str, Any]:
        """Log retrieval failure for audit trail."""

        return {
            "event": "retrieval_failure",
            "at": datetime.now(UTC).isoformat(),
            "trace_id": trace_id,
            "source_id": str(source_id),
            "error": error_info,
        }

    def record_retrieval_success(
        self,
        source_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        trace_id: str,
        row_count: int,
        checksum: str,
    ) -> dict[str, Any]:
        """Log successful retrieval to snapshot for audit trail."""

        return {
            "event": "retrieval_success",
            "at": datetime.now(UTC).isoformat(),
            "trace_id": trace_id,
            "source_id": str(source_id),
            "snapshot_id": str(snapshot_id),
            "row_count": row_count,
            "checksum_sha256": checksum,
        }

    def source_metadata_for_audit(self, source_id: uuid.UUID) -> dict[str, Any]:
        """Return source metadata for inclusion in audit trail."""

        source = self.session.get(DataSource, source_id)
        if source is None:
            return {"status": "not_found", "source_id": str(source_id)}

        return {
            "status": "found",
            "source_id": str(source.id),
            "code": source.code,
            "name": source.name,
            "kind": source.kind,
            "publisher": source.publisher,
            "url": source.url,
            "citation": source.citation,
            "verification_status": source.verification_status,
            "created_at": source.created_at.isoformat() if source.created_at else None,
        }


__all__ = ["DataCatalog"]
