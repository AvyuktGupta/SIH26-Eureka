"""L1 terrain / DEM.

Primary: Open-Elevation (as specified). Fallback: Open-Meteo elevation
(SRTM wrapper, no key) — Open-Elevation's public instance is often down.

Slope and aspect are derived in L2 from neighboring H3 cell elevations, not
here. This module only returns elevation points.
"""

from __future__ import annotations

from typing import Iterable

import httpx

from app import db
from app.config import DEM_CACHE_TTL_S, OPEN_ELEVATION_URL, OPEN_METEO_ELEVATION_URL
from app.contracts.schemas import TerrainSample


def _key(lat: float, lon: float) -> str:
    return f"dem:{round(lat, 4)}:{round(lon, 4)}"


def _from_open_elevation(points: list[tuple[float, float]]) -> list[float]:
    payload = {"locations": [{"latitude": lat, "longitude": lon} for lat, lon in points]}
    with httpx.Client(timeout=15.0) as client:
        r = client.post(OPEN_ELEVATION_URL, json=payload)
        r.raise_for_status()
        results = r.json().get("results") or []
    if len(results) != len(points):
        raise RuntimeError("Open-Elevation returned incomplete results")
    return [float(item["elevation"]) for item in results]


def _from_open_meteo(points: list[tuple[float, float]]) -> list[float]:
    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)
    with httpx.Client(timeout=15.0) as client:
        r = client.get(
            OPEN_METEO_ELEVATION_URL,
            params={"latitude": lats, "longitude": lons},
        )
        r.raise_for_status()
        elev = r.json().get("elevation") or []
    if len(elev) != len(points):
        raise RuntimeError("Open-Meteo elevation returned incomplete results")
    return [float(x) for x in elev]


def fetch_elevations(points: Iterable[tuple[float, float]]) -> list[TerrainSample]:
    pts = list(points)
    samples: list[TerrainSample] = []
    missing: list[tuple[int, tuple[float, float]]] = []

    for i, (lat, lon) in enumerate(pts):
        cached = db.cache_get(_key(lat, lon), DEM_CACHE_TTL_S)
        if cached:
            samples.append(TerrainSample(**cached))
        else:
            last, _ = db.cache_get_last(_key(lat, lon))
            if last:
                s = TerrainSample(**last)
                s.stale = True
                samples.append(s)
            else:
                samples.append(
                    TerrainSample(lat=lat, lon=lon, elevation_m=0.0, source="pending")
                )
                missing.append((i, (lat, lon)))

    if not missing:
        return samples

    chunk = [p for _, p in missing]
    source = "open-elevation"
    try:
        elevations = _from_open_elevation(chunk)
    except Exception:
        source = "open-meteo-elevation"
        elevations = _from_open_meteo(chunk)

    for (i, (lat, lon)), elev in zip(missing, elevations):
        sample = TerrainSample(
            lat=lat,
            lon=lon,
            elevation_m=elev,
            source=source,
            stale=False,
        )
        db.cache_put(_key(lat, lon), source, sample.model_dump())
        samples[i] = sample
    return samples
