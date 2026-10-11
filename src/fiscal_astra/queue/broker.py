"""Dramatiq broker shared by the API producer and simulation worker."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from fiscal_astra.config.settings import get_settings

settings = get_settings()
broker = RedisBroker(url=settings.broker_url)  # type: ignore[no-untyped-call]
dramatiq.set_broker(broker)
