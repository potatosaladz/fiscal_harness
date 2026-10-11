"""Tests for retrieval adapters, provenance, snapshots, and evidence checks."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.config.settings import Settings
from fiscal_astra.database import (
    AuditEvent,
    Base,
    DataSnapshot,
    DataSource,
    SimulationRun,
)
from fiscal_astra.database.enums import AuditActionType
from fiscal_astra.database.repositories import ScenarioRepository
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.data.adapters import (
    AllowlistedUrlAdapter,
    CsvAdapter,
    ParquetAdapter,
    UploadAdapter,
)
from fiscal_astra.services.data.errors import (
    EvidenceVerificationError,
    SnapshotConflictError,
    UrlNotAllowlistedError,
)
from fiscal_astra.services.data.evidence import EvidenceService
from fiscal_astra.services.data.models import RetrievalRequest, compute_checksum
from fiscal_astra.services.data.retrieval import RetrievalService
from fiscal_astra.services.data.snapshots import SnapshotService


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    """Provide an isolated database for retrieval integration tests."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _create_run(factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    with factory.begin() as session:
        scenarios = ScenarioRepository(session)
        scenario = scenarios.create_scenario("retrieval-test", "Retrieval test")
        version = scenarios.create_version(scenario, 1, {}, [])
        from fiscal_astra.database.repositories import SimulationRunRepository

        simulation_run = SimulationRunRepository(session).create_run(version, "baseline")
        data_source = DataSource(
            code="test-source",
            name="Test source",
            kind="synthetic_fixture",
            verification_status="verified",
        )
        session.add(data_source)
        session.flush()
        return simulation_run.id, data_source.id


def test_csv_adapter_parses_long_rows_and_checksum(tmp_path: Path) -> None:
    path = tmp_path / "series.csv"
    payload = b"series,period,value,unit\ngdp,2024,10,local_currency\ngdp,2025,11,local_currency\n"
    path.write_bytes(payload)

    settings = Settings(_env_file=None, data_root=str(tmp_path))
    dataset = CsvAdapter(settings).retrieve(
        RetrievalRequest(location="series.csv", provider="csv", dataset_name="series.csv")
    )

    assert dataset.checksum_sha256 == compute_checksum(payload)
    assert dataset.row_count == 2
    assert dataset.series[0].code == "gdp"
    assert dataset.series[0].observations[1].value == 11.0


def test_parquet_adapter_parses_payload(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    table = pa.table({"period": ["2024", "2025"], "gdp": [10.0, 11.0]})
    parquet.write_table(table, buffer)
    payload = buffer.getvalue()
    path = tmp_path / "fixture.parquet"
    path.write_bytes(payload)

    dataset = ParquetAdapter(Settings(_env_file=None, data_root=str(tmp_path))).retrieve(
        RetrievalRequest(
            location=str(path),
            provider="parquet",
            media_type="application/parquet",
        )
    )

    assert dataset.checksum_sha256 == compute_checksum(payload)
    assert dataset.series[0].code == "gdp"


def test_upload_adapter_preserves_raw_checksum() -> None:
    payload = b"period,value\n2025,4.5\n"
    dataset = UploadAdapter().retrieve(
        RetrievalRequest(
            location="upload.csv",
            provider="upload",
            media_type="text/csv",
            parameters={"payload": payload},
        )
    )

    assert dataset.checksum_sha256 == compute_checksum(payload)
    assert dataset.row_count == 1


def test_url_adapter_rejects_non_allowlisted_host() -> None:
    adapter = AllowlistedUrlAdapter(
        Settings(_env_file=None, retrieval_url_allowlist=["allowed.example"])
    )

    with pytest.raises(UrlNotAllowlistedError):
        adapter.retrieve(
            RetrievalRequest(location="https://blocked.example/data.csv", provider="url")
        )


def test_snapshot_checksum_and_immutability(session_factory: sessionmaker[Session]) -> None:
    _, source_id = _create_run(session_factory)
    payload = b"period,value\n2025,4.5\n"
    dataset = UploadAdapter().retrieve(
        RetrievalRequest(
            location="upload.csv",
            provider="upload",
            media_type="text/csv",
            parameters={"payload": payload},
        )
    )

    with session_factory.begin() as session:
        snapshot = SnapshotService(session).record_snapshot(source_id, dataset, trace_id="trace")
        snapshot_id = snapshot.id
        assert SnapshotService(session).verify_snapshot(snapshot_id, payload)

    with pytest.raises(SnapshotConflictError):
        with session_factory.begin() as session:
            SnapshotService(session).verify_snapshot(snapshot_id, b"different")

    with pytest.raises(ValueError, match="immutable"):
        with session_factory.begin() as session:
            persisted = session.get(DataSnapshot, snapshot_id)
            assert persisted is not None
            persisted.description = "mutated"
            session.flush()


def test_evidence_checksum_and_unverified_link_rejection(
    session_factory: sessionmaker[Session],
) -> None:
    payload = b"verified evidence"
    with session_factory.begin() as session:
        service = EvidenceService(session)
        evidence_id = service.record_evidence(
            "Verified fixture",
            "primary_data",
            "verified",
            access_status="not_checked",
        )
        result = service.verify_evidence_access(evidence_id, payload)
        assert result["access_status"] == "accessible"
        service.link_claim_to_evidence(evidence_id, "claim-1", "Observed claim", "fact", "high")

        unverified_id = service.record_evidence("Unverified", "primary_data", "unverified")
        with pytest.raises(EvidenceVerificationError):
            service.link_claim_to_evidence(
                unverified_id, "claim-2", "Unsupported claim", "fact", "low"
            )


def test_failed_retrieval_writes_audit_error_and_no_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    run_id, source_id = _create_run(session_factory)
    with session_factory() as session:
        service = RetrievalService(
            session,
            Settings(_env_file=None),
            AuditLogger(session_factory),
            run_id,
        )
        with pytest.raises(UrlNotAllowlistedError):
            service.retrieve_and_snapshot(
                source_id,
                RetrievalRequest(
                    location="https://blocked.example/data.csv",
                    provider="url",
                ),
                trace_id="failed-trace",
            )
        session.rollback()

    with session_factory() as session:
        assert session.scalars(select(DataSnapshot)).all() == []
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence)
        ).all()
        assert events[-1].action_type == AuditActionType.ERROR
        assert events[-1].trace_id == "failed-trace"


def test_successful_retrieval_associates_snapshot_with_simulation_run(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    run_id, source_id = _create_run(session_factory)
    payload = b"period,value\n2025,4.5\n"
    path = tmp_path / "input.csv"
    path.write_bytes(payload)

    with session_factory.begin() as session:
        service = RetrievalService(
            session,
            Settings(_env_file=None, data_root=str(tmp_path)),
            AuditLogger(session_factory),
            run_id,
        )
        outcome = service.retrieve_and_snapshot(
            source_id,
            RetrievalRequest(location="input.csv", provider="csv"),
            trace_id="success-trace",
        )

        run = session.get(SimulationRun, run_id)
        assert run is not None
        assert [snapshot.id for snapshot in run.data_snapshots] == [outcome.snapshot_id]
        assert outcome.dataset.checksum_sha256 == compute_checksum(payload)
