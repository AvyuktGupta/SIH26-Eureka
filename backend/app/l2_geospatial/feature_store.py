"""L2 feature store: persist RoadSegmentRisk rows (H3 cell + weather + DEM)."""

from __future__ import annotations

import math

import h3

from app import db
from app.config import H3_RESOLUTION
from app.contracts.schemas import RoadSegmentRisk, TerrainSample, WeatherSample
from app.l1_ingestion.terrain import fetch_elevations
from app.l1_ingestion.weather import apply_demo_weather, fetch_weather
from app.l2_geospatial.corridor import cell_center, point_on_line, tile_route


def _nearest_weather(
    lat: float, lon: float, samples: list[WeatherSample]
) -> WeatherSample:
    return min(
        samples,
        key=lambda s: (s.lat - lat) ** 2 + (s.lon - lon) ** 2,
    )


def _slope_aspect(h3_index: str, elev_lookup: dict[str, float]) -> tuple[float, float]:
    """Derive slope/aspect from the H3 neighbor elevation field (metres)."""
    origin_elev = elev_lookup.get(h3_index)
    if origin_elev is None:
        return 0.0, 0.0
    neighbors = list(h3.grid_disk(h3_index, 1))
    zx_vals: list[float] = []
    zy_vals: list[float] = []
    olat, olon = cell_center(h3_index)
    for n in neighbors:
        if n == h3_index or n not in elev_lookup:
            continue
        nlat, nlon = cell_center(n)
        de = elev_lookup[n] - origin_elev
        # local metres using simple equirectangular deltas
        dx = (nlon - olon) * 111320.0 * math.cos(math.radians(olat))
        dy = (nlat - olat) * 110540.0
        dist = math.hypot(dx, dy) or 1.0
        zx_vals.append(de * dx / (dist * dist))
        zy_vals.append(de * dy / (dist * dist))
    if not zx_vals:
        return 0.0, 0.0
    zx = sum(zx_vals) / len(zx_vals)
    zy = sum(zy_vals) / len(zy_vals)
    slope = math.degrees(math.atan(math.hypot(zx, zy)))
    aspect = (math.degrees(math.atan2(zy, -zx)) + 360.0) % 360.0
    return slope, aspect


def build_road_segment_risks(
    session_id: str,
    route_lonlat: list[list[float]],
    weather_points: list[tuple[float, float]],
    *,
    demo_mode: str = "normal",
    extra_precip_mm: float = 0.0,
    kill_weather: bool = False,
    weather_samples: list[WeatherSample] | None = None,
) -> list[RoadSegmentRisk]:
    cells = tile_route(route_lonlat, H3_RESOLUTION, ring=1)
    centers = [cell_center(c) for c in cells]

    if weather_samples is None:
        weather_samples = []
        for lat, lon in weather_points:
            sample = fetch_weather(lat, lon, kill=kill_weather)
            weather_samples.append(
                apply_demo_weather(sample, demo_mode, extra_precip_mm)
            )

    terrain_samples: list[TerrainSample] = fetch_elevations(centers)
    elev_by_h3 = {
        cell: ts.elevation_m for cell, ts in zip(cells, terrain_samples)
    }

    rows: list[RoadSegmentRisk] = []
    for cell, (lat, lon), ts in zip(cells, centers, terrain_samples):
        slope, aspect = _slope_aspect(cell, elev_by_h3)
        wx = _nearest_weather(lat, lon, weather_samples)
        on_route = point_on_line(lat, lon, route_lonlat, max_m=450.0)
        row = RoadSegmentRisk(
            h3_index=cell,
            lat=lat,
            lon=lon,
            elevation_m=ts.elevation_m,
            slope_deg=slope,
            aspect_deg=aspect,
            precipitation_mm=wx.precipitation_mm,
            precip_24h_mm=wx.precip_24h_mm,
            temperature_c=wx.temperature_c,
            wind_ms=wx.wind_ms,
            snowfall_cm=wx.snowfall_cm,
            on_active_route=on_route,
            source_flags={
                "weather": wx.source,
                "terrain": ts.source,
                "weather_stale": str(wx.stale).lower(),
                "terrain_stale": str(ts.stale).lower(),
            },
        )
        rows.append(row)

    db.upsert_segments(session_id, [r.model_dump() for r in rows])
    return rows
