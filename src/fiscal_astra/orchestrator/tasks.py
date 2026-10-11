"""Dramatiq entrypoints that drive the orchestrator outside the HTTP process.

The orchestrator container runs this module. On boot it reconciles every
non-terminal run, which is how the state machine continues after a restart
without redoing stages that already finished.
"""

from __future__ import annotations

import logging
from typing import Any

import dramatiq

from fiscal_astra.config.settings import get_settings
from fiscal_astra.orchestrator.service import build_orchestrator_service
from fiscal_astra.queue.broker import broker  # noqa: F401

logger = logging.getLogger(__name__)
settings = get_settings()


@dramatiq.actor(
    queue_name=settings.orchestrator_queue_name,
    max_retries=settings.simulation_max_attempts - 1,
    min_backoff=settings.simulation_retry_min_backoff_ms,
    max_backoff=settings.simulation_retry_max_backoff_ms,
)
def advance_run(run_id: str) -> None:
    """Resume the orchestrator pipeline for one run."""

    build_orchestrator_service().advance(run_id)


class ReconcileMiddleware(dramatiq.Middleware):
    """Resume non-terminal runs whenever the orchestrator process boots."""

    def before_worker_boot(self, broker: Any, worker: Any) -> None:
        try:
            resumed = build_orchestrator_service().reconcile()
        except Exception:  # pragma: no cover - a boot failure must not kill the worker
            logger.warning("orchestrator reconcile on boot failed", exc_info=True)
            return
        if resumed:
            logger.info("orchestrator resumed %d run(s) after restart", len(resumed))


broker.add_middleware(ReconcileMiddleware())
