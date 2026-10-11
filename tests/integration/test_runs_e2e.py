"""End-to-end API -> Redis -> worker -> PostgreSQL test.

Run this test against the compose stack with ``ASTRA_E2E=1`` after migrations:
``docker compose up -d api worker redis postgres`` then run the tools container
on the compose network. It is skipped during the isolated unit suite because it
requires the separately supervised API and worker containers.
"""

from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fiscal_astra.database import AuditEvent, SimulationArtifact, SimulationResult, SimulationRun
from fiscal_astra.database.enums import AuditActionType, RunStatus

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("ASTRA_E2E") != "1",
    reason="requires API, Redis, worker, and PostgreSQL",
)
def test_post_run_is_processed_by_external_worker() -> None:
    """POST /runs returns before numeric execution and reaches succeeded state."""

    api_url = os.getenv("ASTRA_E2E_API_URL", "http://api:8000")
    database_url = os.environ["ASTRA_DATABASE_URL"]
    with httpx.Client(base_url=api_url, timeout=5.0) as client:
        response = client.post(
            "/runs",
            json={
                "label": f"e2e-{uuid.uuid4()}",
                "baseline_or_policy": "baseline",
                "horizon": 4,
                "frequency": "annual",
                "parameters": {
                    "initial_debt_to_gdp": 0.55,
                    "nominal_interest_rate": 0.05,
                    "nominal_gdp_growth_rate": 0.03,
                    "primary_balance": 0.005,
                },
                "random_seed": 99,
                "requested_outputs": ["debt_to_gdp"],
            },
        )
        assert response.status_code == 202
        submitted = response.json()
        assert submitted["status"] in {RunStatus.PENDING.value, RunStatus.QUEUED.value}
        run_id = submitted["run_id"]

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = client.get(f"/runs/{run_id}")
            assert current.status_code == 200
            if current.json()["status"] in {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.NOT_EXECUTED.value,
            }:
                break
            time.sleep(0.25)
        else:
            pytest.fail("external simulation worker did not finish within 30 seconds")

    engine = create_engine(database_url)
    with Session(engine) as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == RunStatus.SUCCEEDED.value
        assert (
            session.scalars(
                select(SimulationResult).where(SimulationResult.run_id == run.id)
            ).first()
            is not None
        )
        assert (
            session.scalars(
                select(SimulationArtifact).where(SimulationArtifact.run_id == run.id)
            ).first()
            is not None
        )
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run.id).order_by(AuditEvent.sequence)
        ).all()
        assert events[0].action_type == AuditActionType.SIMULATION_SUBMITTED
        assert any(event.action_type == AuditActionType.VALIDATION_STARTED for event in events)
        assert events[-1].action_type == AuditActionType.SIMULATION_COMPLETED
    engine.dispose()
