"""Persistence tests for repositories and application services."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fiscal_astra.database import Base
from fiscal_astra.database.repositories import (
    DataSnapshotRepository,
    ScenarioRepository,
    canonical_spec_hash,
)
from fiscal_astra.services.artifact_service import ArtifactService
from fiscal_astra.services.scenario_service import ScenarioService


@pytest.fixture
def session() -> Iterator[Session]:
    """Provide an isolated SQLite schema for each persistence test."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_scenario_versions_are_numbered_and_hashed(session: Session) -> None:
    repository = ScenarioRepository(session)
    scenario = repository.create_scenario(
        slug="vat-reform",
        title="VAT reform",
        jurisdiction="XX",
        reference_period="2025",
    )
    settings = {"rate": 0.12, "coverage": "broad"}
    assumptions = [{"name": "compliance", "value": "unchanged"}]

    first = repository.create_version(
        scenario,
        repository.next_version_number(scenario),
        settings,
        assumptions,
        label="baseline",
    )
    second = repository.create_version(
        scenario,
        repository.next_version_number(scenario),
        {"coverage": "broad", "rate": 0.12},
        assumptions,
        label="reordered-spec",
    )

    assert first.version_number == 1
    assert second.version_number == 2
    assert first.spec_hash == canonical_spec_hash(settings, assumptions)
    assert second.spec_hash == first.spec_hash
    assert repository.latest_version(scenario) is second
    assert repository.get_scenario_by_slug("vat-reform") is scenario


def test_scenario_service_creates_versions_and_schedules_runs(session: Session) -> None:
    service = ScenarioService(session)
    scenario = service.create_scenario("budget-2026", "Budget 2026")

    version = service.create_version(
        scenario.id,
        {"spending": {"amount": 100}},
        [{"name": "multiplier", "type": "assumption"}],
    )
    run = service.schedule_run(
        version.id,
        label="baseline",
        random_seed=7,
        code_version="test-code",
    )

    assert version.version_number == 1
    assert run.scenario_version_id == version.id
    assert run.random_seed == 7
    assert run.code_version == "test-code"
    assert run.provenance["created_at"]
    assert service.recomputed_spec_hash(version.instrument_settings, version.assumptions) == (
        version.spec_hash
    )


def test_data_snapshot_repository_persists_series_and_metadata(session: Session) -> None:
    repository = DataSnapshotRepository(session)
    source = repository.register_source(
        code="official-statistics",
        name="Official statistics",
        kind="primary_data",
        verification_status="verified",
    )
    snapshot = repository.create_snapshot(
        source,
        vintage="2025-01",
        row_count=2,
        period_start="2024",
        period_end="2025",
        checksum_sha256="abc123",
    )
    series = repository.add_series_with_values(
        snapshot,
        code="gdp",
        name="GDP",
        observations=[("2024", 10), ("2025", 11.5)],
        unit="local_currency",
        frequency="annual",
        provenance_type="observed",
    )

    assert repository.get_series(series.id) is series
    assert repository.get_series_values(series) == [("2024", 10.0), ("2025", 11.5)]
    assert repository.snapshot_summary(snapshot) == {
        "snapshot_id": str(snapshot.id),
        "source_code": "official-statistics",
        "vintage": "2025-01",
        "row_count": 2,
    }


def test_artifact_service_registers_and_renders_object_reference(session: Session) -> None:
    service = ArtifactService(session)
    scenario_service = ScenarioService(session)
    scenario = scenario_service.create_scenario("artifact-run", "Artifact run")
    version = scenario_service.create_version(scenario.id, {}, [])
    run = scenario_service.schedule_run(version.id, "baseline")

    artifact = service.register_artifact(
        run.id,
        artifact_kind="run_manifest",
        bucket="fiscal-astra-artifacts",
        object_key="runs/example/manifest.json",
        content_type="application/json",
        size_bytes=128,
        checksum_sha256="deadbeef",
    )

    assert service.get_artifact(artifact.id) is artifact
    assert (
        service.find_by_object("fiscal-astra-artifacts", "runs/example/manifest.json") is artifact
    )
    assert service.s3_url("bucket", "path/result.json") == "s3://bucket/path/result.json"
    assert service.s3_url("bucket", "path/result.json", "http://minio:9000") == (
        "http://minio:9000/bucket/path/result.json"
    )
