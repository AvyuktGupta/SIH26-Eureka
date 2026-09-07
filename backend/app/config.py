"""Demo region, thresholds, and service URLs.

Demo corridor: NH-3 / NH-105 Kullu → Manali, Himachal Pradesh.
Chosen because it is a real monsoon landslide corridor (Beas valley), compact
enough for a laptop OSRM extract, and well mapped in OSM.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA = _REPO_ROOT / "data"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- Demo geography (lat, lon) ---
DEMO_ORIGIN_LAT = 31.9578  # Kullu, near Akhara Bazaar / NH-3
DEMO_ORIGIN_LON = 77.1095
DEMO_DEST_LAT = 32.2396  # Manali Mall Road
DEMO_DEST_LON = 77.1887

# Bounding box matching scripts/download_osm.py
BBOX_SOUTH, BBOX_WEST, BBOX_NORTH, BBOX_EAST = 31.90, 77.02, 32.30, 77.32

CORRIDOR_BUFFER_KM = 15.0  # mid-point of the 10–20 km requirement
H3_RESOLUTION = 8  # ~0.74 km² — road-sampled cells, not a filled polygon

# Known landslide-prone stretch on NH-3 (near Katrain / Patlikuhal)
HAZARD_ZONE_LAT = 32.125
HAZARD_ZONE_LON = 77.145

# --- L4 fusion / hysteresis (Schmitt trigger + consecutive ticks) ---
FUSION_ENTER_THRESHOLD = 0.62
FUSION_EXIT_THRESHOLD = 0.40
HYSTERESIS_MIN_TICKS = 3
SPIKE_DECAY = 0.15

# Risk as OSRM duration multiplier: duration * (1 + lambda * fused_score)
RISK_PENALTY_LAMBDA = 10.0
FLAGGED_SEGMENT_THRESHOLD = 0.62

# --- Caches / pollers ---
WEATHER_CACHE_TTL_S = 15 * 60
DEM_CACHE_TTL_S = 30 * 24 * 3600  # terrain is static for the demo
POLLER_INTERVAL_S = 10 * 60

# --- Services ---
OSRM_URL = _env("OSRM_URL", "http://127.0.0.1:5000")
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "llama3.2:3b")
DATABASE_PATH = _env("DATABASE_PATH", str(_DATA / "apcs.db"))
MODELS_DIR = _env("MODELS_DIR", str(_DATA / "models"))
GLACIER_STUB_DIR = _env("GLACIER_STUB_DIR", str(_DATA / "glacier_stub"))

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
