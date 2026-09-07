"""L2 corridor buffering (10–20 km) and H3 tiling against the active route.

No shapely/pyproj — local equirectangular metres are accurate enough at this
corridor scale. We sample the route rather than filling the whole buffer
polygon, so H3 work stays bounded to the driving path.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import h3

from app.config import CORRIDOR_BUFFER_KM, H3_RESOLUTION


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _lat0(coordinates_lonlat: list[list[float]]) -> float:
    return sum(c[1] for c in coordinates_lonlat) / max(1, len(coordinates_lonlat))


def _to_xy(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    return lon * 111320.0 * math.cos(math.radians(lat0)), lat * 110540.0


def _from_xy(x: float, y: float, lat0: float) -> tuple[float, float]:
    lat = y / 110540.0
    lon = x / (111320.0 * math.cos(math.radians(lat0)) or 1.0)
    return lat, lon


def sample_route_points(
    coordinates_lonlat: list[list[float]],
    spacing_m: float = 400.0,
) -> list[tuple[float, float]]:
    if not coordinates_lonlat:
        return []
    pts = [(c[1], c[0]) for c in coordinates_lonlat]
    if len(pts) == 1:
        return pts
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]))
    total = cum[-1]
    n = max(2, int(total / spacing_m) + 1)
    out: list[tuple[float, float]] = []
    seg_i = 0
    for k in range(n):
        d = min(total, k * spacing_m)
        while seg_i < len(cum) - 1 and cum[seg_i + 1] < d:
            seg_i += 1
        if seg_i >= len(pts) - 1:
            out.append(pts[-1])
            continue
        span = cum[seg_i + 1] - cum[seg_i] or 1.0
        t = (d - cum[seg_i]) / span
        lat = pts[seg_i][0] + t * (pts[seg_i + 1][0] - pts[seg_i][0])
        lon = pts[seg_i][1] + t * (pts[seg_i + 1][1] - pts[seg_i][1])
        out.append((lat, lon))
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


def tile_route(
    coordinates_lonlat: list[list[float]],
    resolution: int = H3_RESOLUTION,
    ring: int = 1,
) -> list[str]:
    cells: set[str] = set()
    for lat, lon in sample_route_points(coordinates_lonlat):
        origin = h3.latlng_to_cell(lat, lon, resolution)
        cells.update(h3.grid_disk(origin, ring))
    return sorted(cells)


def cell_center(h3_index: str) -> tuple[float, float]:
    lat, lon = h3.cell_to_latlng(h3_index)
    return float(lat), float(lon)


def cell_boundary(h3_index: str) -> list[list[float]]:
    boundary = h3.cell_to_boundary(h3_index)
    ring = [[float(lon), float(lat)] for lat, lon in boundary]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def cells_geojson(h3_indices: Iterable[str], properties: dict[str, Any] | None = None) -> dict:
    feats = []
    for idx in h3_indices:
        lat, lon = cell_center(idx)
        feats.append(
            {
                "type": "Feature",
                "properties": {"h3_index": idx, "lat": lat, "lon": lon, **(properties or {})},
                "geometry": {"type": "Polygon", "coordinates": [cell_boundary(idx)]},
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def buffer_corridor_geojson(
    coordinates_lonlat: list[list[float]],
    buffer_km: float = CORRIDOR_BUFFER_KM,
) -> dict[str, Any]:
    """Approximate corridor polygon: stadium around the route in local metres."""
    if len(coordinates_lonlat) < 2:
        return {"type": "Feature", "properties": {"buffer_km": buffer_km}, "geometry": None}
    lat0 = _lat0(coordinates_lonlat)
    radius = buffer_km * 1000.0
    left: list[list[float]] = []
    right: list[list[float]] = []
    xy = [_to_xy(c[1], c[0], lat0) for c in coordinates_lonlat]
    for i, (x, y) in enumerate(xy):
        if i < len(xy) - 1:
            dx, dy = xy[i + 1][0] - x, xy[i + 1][1] - y
        else:
            dx, dy = x - xy[i - 1][0], y - xy[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * radius, dx / length * radius
        lat_l, lon_l = _from_xy(x + nx, y + ny, lat0)
        lat_r, lon_r = _from_xy(x - nx, y - ny, lat0)
        left.append([lon_l, lat_l])
        right.append([lon_r, lat_r])
    ring = left + list(reversed(right))
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return {
        "type": "Feature",
        "properties": {"buffer_km": buffer_km},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def point_on_line(
    lat: float,
    lon: float,
    coordinates_lonlat: list[list[float]],
    max_m: float = 250.0,
) -> bool:
    if len(coordinates_lonlat) < 2:
        return False
    lat0 = _lat0(coordinates_lonlat)
    px, py = _to_xy(lat, lon, lat0)
    best = 1e12
    for i in range(1, len(coordinates_lonlat)):
        x1, y1 = _to_xy(coordinates_lonlat[i - 1][1], coordinates_lonlat[i - 1][0], lat0)
        x2, y2 = _to_xy(coordinates_lonlat[i][1], coordinates_lonlat[i][0], lat0)
        dx, dy = x2 - x1, y2 - y1
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denom))
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return best <= max_m
