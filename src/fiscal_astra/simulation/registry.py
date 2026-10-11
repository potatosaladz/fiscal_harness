"""Registry of local simulation engines available to the harness."""

from __future__ import annotations

from pathlib import Path

from fiscal_astra.simulation.debt_dynamics import FiscalDebtDynamicsMvp
from fiscal_astra.simulation.protocols import EngineFactory, SimulationEngine

_ENGINES: dict[str, EngineFactory] = {
    FiscalDebtDynamicsMvp.engine_name: FiscalDebtDynamicsMvp,
}


def resolve_engine(engine_name: str, artifact_root: str | Path | None = None) -> SimulationEngine:
    """Instantiate a registered engine by name."""

    if engine_name not in _ENGINES:
        available = ", ".join(sorted(_ENGINES))
        raise KeyError(f"Unknown simulation engine: {engine_name} (available: {available})")
    return _ENGINES[engine_name](artifact_root=artifact_root)


def registered_engines() -> list[str]:
    return sorted(_ENGINES)
