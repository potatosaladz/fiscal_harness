"""Common simulation-engine contracts and the executable MVP engine."""

from fiscal_astra.simulation.contracts import (
    ArtifactReference,
    Diagnostics,
    ModelPlan,
    SimulationRequest,
    SimulationResult,
    ValidationResult,
)
from fiscal_astra.simulation.debt_dynamics import FiscalDebtDynamicsMvp
from fiscal_astra.simulation.protocols import SimulationEngine
from fiscal_astra.simulation.registry import registered_engines, resolve_engine

__all__ = [
    "ArtifactReference",
    "Diagnostics",
    "FiscalDebtDynamicsMvp",
    "ModelPlan",
    "SimulationEngine",
    "SimulationRequest",
    "SimulationResult",
    "ValidationResult",
    "registered_engines",
    "resolve_engine",
]
