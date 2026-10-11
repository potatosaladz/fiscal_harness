"""Top-level retrieval coordinator and immutable snapshot workflow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from fiscal_astra.config.settings import Settings
from fiscal_astra.database.enums import AuditActionType
from fiscal_astra.database.models_simulation import SimulationRun
from fiscal_astra.database.repositories import DataSnapshotRepository
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.data.adapters import (
    AllowlistedUrlAdapter,
    ApiAdapter,
    CsvAdapter,
    ParquetAdapter,
    UploadAdapter,
)
from fiscal_astra.services.data.catalog import DataCatalog
from fiscal_astra.services.data.errors import RetrievalError, UnsupportedFormatError
from fiscal_astra.services.data.models import (
    DataAdapter,
    RetrievalRequest,
    RetrievedDataset,
    new_trace_id,
)
from fiscal_astra.services.data.snapshots import SnapshotService


@dataclass(slots=True)
class RetrievalOutcome:
    """Successful retrieval result and its immutable snapshot identity."""

    trace_id: str
    source_id: uuid.UUID
    snapshot_id: uuid.UUID
    dataset: RetrievedDataset


class RetrievalService:
    """Dispatch retrieval, persist snapshots, and audit every outcome."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        audit: AuditLogger,
        run_id: uuid.UUID,
        *,
        adapters: list[DataAdapter] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.audit = audit
        self.run_id = run_id
        self.catalog = DataCatalog(session)
        self.snapshots = SnapshotService(session)
        self.adapters = adapters or [
            CsvAdapter(settings),
            ParquetAdapter(settings),
            UploadAdapter(),
            ApiAdapter(),
            AllowlistedUrlAdapter(settings),
        ]

    def retrieve_and_snapshot(
        self,
        source_id: uuid.UUID,
        request: RetrievalRequest,
        *,
        actor: str = "retrieval-service",
        trace_id: str | None = None,
    ) -> RetrievalOutcome:
        """Retrieve data, persist an immutable snapshot, and write audit events.

        Retrieval failures are recorded as audit events and re-raised. No
        snapshot is created for a failed or rejected retrieval.
        """

        trace = trace_id or new_trace_id()
        self._record_audit(
            actor=actor,
            action_type=AuditActionType.DATA_REQUESTED,
            description="retrieval intent",
            inputs=[self.catalog.record_retrieval_intent(source_id, request, trace)],
            outputs=[],
            trace_id=trace,
        )
        try:
            adapter = self._select_adapter(request)
            dataset = adapter.retrieve(request)
            snapshot = self.snapshots.record_snapshot(source_id, dataset, trace_id=trace)
            run = self.session.get(SimulationRun, self.run_id)
            if run is None:
                raise ValueError(f"SimulationRun not found: {self.run_id}")
            DataSnapshotRepository(self.session).attach_run(run, snapshot)
            success = self.catalog.record_retrieval_success(
                source_id,
                snapshot.id,
                trace,
                dataset.row_count,
                dataset.checksum_sha256,
            )
            self._record_audit(
                actor=actor,
                action_type=AuditActionType.DATA_RETRIEVED,
                description="retrieval completed and immutable snapshot created",
                inputs=[],
                outputs=[success, self.snapshots.with_snapshot_metadata(snapshot.id)],
                trace_id=trace,
            )
            return RetrievalOutcome(
                trace_id=trace,
                source_id=source_id,
                snapshot_id=snapshot.id,
                dataset=dataset,
            )
        except Exception as error:
            error_info = (
                error.as_error_info()
                if isinstance(error, RetrievalError)
                else {
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
            failure = self.catalog.record_retrieval_failure(source_id, trace, error_info)
            self._record_audit(
                actor=actor,
                action_type=AuditActionType.ERROR,
                description="retrieval failed; no snapshot created",
                inputs=[],
                outputs=[failure],
                trace_id=trace,
                errors=[error_info],
            )
            raise

    def _select_adapter(self, request: RetrievalRequest) -> DataAdapter:
        for adapter in self.adapters:
            if adapter.supports(request):
                return adapter
        raise UnsupportedFormatError(
            "no retrieval adapter supports "
            f"provider={request.provider!r}, location={request.location!r}",
            origin=request.location,
        )

    def _record_audit(
        self,
        *,
        actor: str,
        action_type: AuditActionType | str,
        description: str,
        inputs: list[Any],
        outputs: list[Any],
        trace_id: str,
        errors: list[Any] | None = None,
    ) -> None:
        self.audit.record(
            run_id=self.run_id,
            trace_id=trace_id,
            actor_type="retrieval",
            actor_id=actor,
            action_type=action_type,
            description=f"[{trace_id}] {description}",
            input_refs=inputs,
            output_refs=outputs,
            error_info=errors or [],
        )


__all__ = ["RetrievalOutcome", "RetrievalService"]
