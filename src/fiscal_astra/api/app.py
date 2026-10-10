"""FastAPI application entrypoint for the Fase 0 service."""

from fastapi import FastAPI

from fiscal_astra import __version__
from fiscal_astra.config.settings import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Fase 0 application shell for Fiscal Astra. Numerical calculations are "
        "not implemented in this phase."
    ),
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Return a process-level health response."""

    return {"status": "ok", "service": "fiscal-astra", "version": __version__}


@app.get("/ready", tags=["health"])
def ready() -> dict[str, str]:
    """Return readiness for the Fase 0 application shell."""

    return {"status": "ready", "database": "not_configured", "simulation": "not_executed"}
