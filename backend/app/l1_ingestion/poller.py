"""L1 scheduled poller. Refreshes weather for a handful of corridor sample points.

Ticks never hit the network for weather/DEM; they read cache. The poller is
what keeps the cache warm and respects free-tier rate limits.
"""

from __future__ import annotations

import logging
from typing import Sequence

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import POLLER_INTERVAL_S
from app.l1_ingestion import terrain, weather

log = logging.getLogger("apcs.l1")
_scheduler: BackgroundScheduler | None = None

# Sparse sample points along Kullu → Manali so we do not hammer free APIs.
CORRIDOR_SAMPLES: list[tuple[float, float]] = [
    (31.9578, 77.1095),  # Kullu
    (32.0200, 77.1200),
    (32.0800, 77.1320),
    (32.1250, 77.1450),  # Katrain / hazard-prone stretch
    (32.1800, 77.1650),
    (32.2396, 77.1887),  # Manali
]


def refresh_corridor(points: Sequence[tuple[float, float]] | None = None) -> None:
    pts = list(points or CORRIDOR_SAMPLES)
    for lat, lon in pts:
        try:
            weather.fetch_weather(lat, lon)
        except Exception as exc:
            log.warning("weather poll failed at %s,%s: %s", lat, lon, exc)
    try:
        terrain.fetch_elevations(pts)
    except Exception as exc:
        log.warning("DEM poll failed: %s", exc)


def start_poller() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    refresh_corridor()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(refresh_corridor, "interval", seconds=POLLER_INTERVAL_S)
    _scheduler.start()
    log.info("L1 poller started every %ss", POLLER_INTERVAL_S)
