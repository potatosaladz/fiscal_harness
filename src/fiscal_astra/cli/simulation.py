"""Command line runner for simulations detached from the API process.

    python -m fiscal_astra.cli.simulation --run-id <uuid> --mode baseline

The process reads the stored request from the run manifest, so the simulation
keeps running to completion even when the browser or application that scheduled
it has been closed, and every step is written to the append-only audit trail.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.config.settings import get_settings
from fiscal_astra.database.models_simulation import SimulationRun
from fiscal_astra.database.session import create_database_engine, create_session_factory
from fiscal_astra.services.audit_logger import AuditLogger
from fiscal_astra.services.simulation_service import SimulationPersistenceService
from fiscal_astra.simulation.contracts import BaselineOrPolicy, SimulationRequest
from fiscal_astra.simulation.registry import resolve_engine


def build_request_from_run(
    session_factory: sessionmaker[Session],
    run_id: str,
    mode: BaselineOrPolicy | None = None,
    parameters: dict[str, Any] | None = None,
    solver_options: dict[str, Any] | None = None,
    random_seed: int | None = None,
) -> SimulationRequest:
    """Reconstruct the stored request, applying optional explicit overrides."""

    with session_factory() as session:
        run = session.get(SimulationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"SimulationRun not found: {run_id}")
        stored = dict(run.provenance or {}).get("request") or {}
        if not stored:
            raise ValueError(
                "Run has no stored simulation request; schedule the run first so "
                "the worker can run fully detached."
            )
        return SimulationRequest(
            run_id=run_id,
            scenario_id=stored["scenario_id"],
            engine_name=stored["engine_name"],
            engine_version=stored["engine_version"],
            baseline_or_policy=mode or stored["baseline_or_policy"],
            horizon=int(stored["horizon"]),
            frequency=stored["frequency"],
            parameters=parameters or dict(stored["parameters"]),
            data_snapshot_ids=list(stored.get("data_snapshot_ids") or []),
            random_seed=random_seed if random_seed is not None else stored.get("random_seed"),
            solver_options={**(stored.get("solver_options") or {}), **(solver_options or {})},
            requested_outputs=list(stored["requested_outputs"]),
        )


def run_simulation(
    session_factory: sessionmaker[Session],
    artifact_root: Path,
    request: SimulationRequest,
) -> dict[str, Any]:
    """Execute one engine invocation and persist progress and results."""

    persistence = SimulationPersistenceService(
        session_factory,
        artifact_root,
        AuditLogger(session_factory),
    )
    engine = resolve_engine(request.engine_name, artifact_root)
    started = time.perf_counter()
    logs: list[str] = []

    validation = engine.validate_request(request)
    logs.append(f"validate_request valid={validation.valid} errors={validation.errors}")
    if not validation.valid:
        raise SystemExit(f"Invalid simulation request: {validation.errors}")

    plan = engine.build_model_plan(request)
    logs.append(f"model_plan objective={plan.objective}")
    persistence.mark_running(request, plan)
    logs.append("mark_running committed")

    result = engine.run(request)
    logs.append(f"engine status={result.status} series={sorted(result.time_series)}")
    if result.status == "converged":
        post = engine.validate_result(request, result)
        logs.append(f"validate_result valid={post.valid} errors={post.errors}")
        diagnostics = engine.get_diagnostics(result)
        artifacts = engine.get_artifacts(result)
        logs.append(f"diagnostics={diagnostics.status} artifacts={len(artifacts)}")

    persistence.persist_result(request, result, plan)
    logs.append(f"persist_result committed in {time.perf_counter() - started:.3f}s")
    log_artifact = persistence.store_log(request, logs)
    logs.append(f"log_artifact={log_artifact.location}")
    return {"status": result.status, "error": result.error, "logs": logs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a scheduled simulation detached from the API."
    )
    parser.add_argument("--run-id", required=True, dest="run_id")
    parser.add_argument(
        "--mode",
        default=None,
        choices=["baseline", "policy", "shock", "sensitivity", "monte_carlo"],
    )
    parser.add_argument("--random-seed", dest="random_seed", type=int)
    parser.add_argument("--parameters", help="JSON parameter overrides")
    parser.add_argument("--solver-options", dest="solver_options", help="JSON solver overrides")
    parsed = parser.parse_args(argv)

    settings = get_settings()
    session_factory = create_session_factory(create_database_engine(settings))
    request = build_request_from_run(
        session_factory,
        parsed.run_id,
        mode=parsed.mode,
        parameters=json.loads(parsed.parameters) if parsed.parameters else None,
        solver_options=json.loads(parsed.solver_options) if parsed.solver_options else None,
        random_seed=parsed.random_seed,
    )
    outcome = run_simulation(session_factory, Path(settings.artifact_root), request)
    print(json.dumps({"status": outcome["status"], "error": outcome["error"]}, indent=2))
    return 0 if outcome["status"] == "converged" else 1


if __name__ == "__main__":
    sys.exit(main())
