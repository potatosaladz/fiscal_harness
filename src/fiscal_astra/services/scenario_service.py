"""Application service for orchestrating scenario and run lifecycle.

The service layer coordinates repositories inside a transactional scope. It is
the boundary that the orchestrator and tests call; it never performs numeric
economic computation itself.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from fiscal_astra.database.models_scenario import Scenario, ScenarioVersion
from fiscal_astra.database.models_simulation import SimulationRun
from fiscal_astra.database.repositories import (
    ScenarioRepository,
    SimulationRunRepository,
    canonical_spec_hash,
)
from fiscal_astra.simulation.contracts import BaselineOrPolicy, SimulationRequest
from fiscal_astra.simulation.debt_dynamics import FiscalDebtDynamicsMvp


class ScenarioService:
    """High-level orchestrator-facing use cases for scenarios."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._scenarios = ScenarioRepository(session)

    def create_scenario(
        self,
        slug: str,
        title: str,
        description: str | None = None,
        jurisdiction: str | None = None,
        reference_period: str | None = None,
        cutoff_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> Scenario:
        return self._scenarios.create_scenario(
            slug=slug,
            title=title,
            description=description,
            jurisdiction=jurisdiction,
            reference_period=reference_period,
            cutoff_at=cutoff_at,
            provenance=provenance,
        )

    def create_version(
        self,
        scenario_id: Any,
        instrument_settings: dict[str, Any],
        assumptions: list[Any],
        label: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> ScenarioVersion:
        scenario = self._scenarios.get_scenario(scenario_id)
        if scenario is None:
            raise ValueError(f"Scenario not found: {scenario_id}")
        version_number = self._scenarios.next_version_number(scenario)
        return self._scenarios.create_version(
            scenario=scenario,
            version_number=version_number,
            instrument_settings=instrument_settings,
            assumptions=assumptions,
            label=label,
            provenance=provenance,
        )

    def schedule_run(
        self,
        scenario_version_id: Any,
        label: str,
        random_seed: int | None = None,
        code_version: str | None = None,
        baseline_or_policy: BaselineOrPolicy = "baseline",
        frequency: str = "annual",
        horizon: int = 10,
        parameters: dict[str, Any] | None = None,
        requested_outputs: list[str] | None = None,
    ) -> SimulationRun:
        version = self._session.get(ScenarioVersion, scenario_version_id)
        if version is None:
            raise ValueError(f"ScenarioVersion not found: {scenario_version_id}")
        run = SimulationRunRepository(self._session).create_run(
            scenario_version=version,
            label=label,
            random_seed=random_seed,
            code_version=code_version,
        )
        if parameters is not None:
            request = SimulationRequest(
                run_id=str(run.id),
                scenario_id=str(version.scenario_id),
                engine_name=FiscalDebtDynamicsMvp.engine_name,
                engine_version=FiscalDebtDynamicsMvp.engine_version,
                baseline_or_policy=baseline_or_policy,
                horizon=horizon,
                frequency=frequency,
                parameters=parameters,
                data_snapshot_ids=[],
                random_seed=random_seed,
                solver_options={},
                requested_outputs=requested_outputs or ["debt_to_gdp"],
            )
            run.provenance = {
                **(run.provenance or {}),
                "request": request.model_dump(mode="json"),
            }
        return run

    @staticmethod
    def compute_spec_hash(
        instrument_settings: dict[str, Any],
        assumptions: list[Any],
        salt: str | None = None,
    ) -> str:
        """Return a deterministic spec hash for idempotent version handling.

        Exposed for service-layer tests and for callers that need to detect
        whether two specs are identical before persisting a new version.
        """

        payload = json.dumps(
            {"instrument_settings": instrument_settings, "assumptions": assumptions},
            sort_keys=True,
            default=str,
        )
        if salt:
            payload = payload + salt
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def recomputed_spec_hash(
        instrument_settings: dict[str, Any],
        assumptions: list[Any],
        salt: str | None = None,
    ) -> str:
        """Alias kept for parity with canonical_spec_hash used elsewhere."""
        return canonical_spec_hash(instrument_settings, assumptions)

    def now_iso(self) -> str:
        return datetime.now(UTC).isoformat()
