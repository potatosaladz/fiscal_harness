"""Simulation run submission and inspection endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from fiscal_astra.config.settings import get_settings
from fiscal_astra.database.session import create_database_engine, create_session_factory
from fiscal_astra.services.run_service import RunSubmissionService
from fiscal_astra.services.simulation_queue import DramatiqOrchestratorPublisher
from fiscal_astra.simulation.contracts import BaselineOrPolicy, SimulationRequest
from fiscal_astra.simulation.debt_dynamics import FiscalDebtDynamicsMvp

router = APIRouter(prefix="/runs", tags=["runs"])
settings = get_settings()
_engine = create_database_engine(settings)
_factory = create_session_factory(_engine)
_service = RunSubmissionService(
    _factory,
    settings.artifact_root,
    DramatiqOrchestratorPublisher(),
    settings.simulation_max_attempts,
)


class RunSubmitRequest(BaseModel):
    """Minimal request payload for POST /runs."""

    label: str = Field(min_length=1, max_length=200)
    scenario_id: str | None = None
    baseline_or_policy: BaselineOrPolicy = "baseline"
    horizon: int = Field(gt=0, le=10_000, default=10)
    frequency: str = Field(min_length=1, max_length=32, default="annual")
    parameters: dict[str, Any]
    data_snapshot_ids: list[str] = Field(default_factory=list)
    random_seed: int | None = None
    solver_options: dict[str, Any] = Field(default_factory=dict)
    requested_outputs: list[str] = Field(min_length=1, default=["debt_to_gdp"])


class RunResponse(BaseModel):
    """Response envelope for run submission and inspection."""

    run_id: str
    correlation_id: str
    label: str
    status: str
    stage: str
    orchestrator_state: str
    error_code: str | None = None
    scenario_id: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RunResponse)
def submit_run(payload: RunSubmitRequest) -> RunResponse:
    """Submit a simulation to the durable queue and return immediately."""

    run_id = str(uuid.uuid4())
    scenario_id = payload.scenario_id or str(uuid.uuid4())
    request = SimulationRequest(
        run_id=run_id,
        scenario_id=scenario_id,
        engine_name=FiscalDebtDynamicsMvp.engine_name,
        engine_version=FiscalDebtDynamicsMvp.engine_version,
        baseline_or_policy=payload.baseline_or_policy,
        horizon=payload.horizon,
        frequency=payload.frequency,
        parameters=payload.parameters,
        data_snapshot_ids=payload.data_snapshot_ids,
        random_seed=payload.random_seed,
        solver_options=payload.solver_options,
        requested_outputs=payload.requested_outputs,
    )
    run = _service.submit(request, payload.label)
    return RunResponse(
        run_id=str(run.id),
        correlation_id=run.correlation_id,
        label=run.label,
        status=run.status,
        stage=run.stage,
        orchestrator_state=run.orchestrator_state,
        error_code=run.error_code,
        scenario_id=str(run.provenance["request"]["scenario_id"]),
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        error_message=run.error_message,
    )


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> RunResponse:
    """Inspect the current status and metadata of a run."""

    try:
        run = _service.get(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"SimulationRun not found: {run_id}") from None
    return RunResponse(
        run_id=str(run.id),
        correlation_id=run.correlation_id,
        label=run.label,
        status=run.status,
        stage=run.stage,
        orchestrator_state=run.orchestrator_state,
        error_code=run.error_code,
        scenario_id=str(run.provenance["request"]["scenario_id"]),
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        error_message=run.error_message,
    )
