"""L5 routing engine — OSRM graph search. The LLM never invents a path.

Fused risk is applied as an edge-weight penalty:
    risk_weighted_duration = sum(duration_i * (1 + λ * fused_score_i))

When hysteresis confirms a hazard on the active path, we ask OSRM for
alternatives and, if needed, a via-point around the flagged H3 cluster.
The chosen alternate is verified to avoid flagged cells; ETA delta is reported.
"""

from __future__ import annotations

import time
from typing import Any

import h3
import httpx

from app.config import (
    FLAGGED_SEGMENT_THRESHOLD,
    HAZARD_ZONE_LAT,
    HAZARD_ZONE_LON,
    OSRM_URL,
    RISK_PENALTY_LAMBDA,
)
from app.contracts.schemas import RiskScore, RouteCandidate
from app.l2_geospatial.corridor import haversine_m


class OsrmUnavailable(RuntimeError):
    pass


def _coords_param(lat: float, lon: float) -> str:
    return f"{lon:.6f},{lat:.6f}"


def osrm_route(
    origin: tuple[float, float],
    dest: tuple[float, float],
    *,
    alternatives: bool = True,
    waypoints: list[tuple[float, float]] | None = None,
    timeout: float = 20.0,
    retries: int = 12,
) -> list[dict[str, Any]]:
    pts = [origin, *(waypoints or []), dest]
    coords = ";".join(_coords_param(lat, lon) for lat, lon in pts)
    url = f"{OSRM_URL}/route/v1/driving/{coords}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "true",
        "alternatives": "true" if alternatives and not waypoints else "false",
        "annotations": "duration,distance",
    }
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, params=params)
                r.raise_for_status()
                payload = r.json()
            if payload.get("code") != "Ok":
                raise OsrmUnavailable(payload.get("message", "OSRM routing failed"))
            return payload.get("routes") or []
        except OsrmUnavailable:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException) as exc:
            last_exc = exc
            time.sleep(min(8, 1.5 * (attempt + 1)))
        except Exception as exc:
            raise OsrmUnavailable(str(exc)) from exc
    raise OsrmUnavailable(str(last_exc))


def _match_point_to_score(lat: float, lon: float, scores: list[RiskScore]) -> float:
    if not scores:
        return 0.0
    best = min(scores, key=lambda s: haversine_m(lat, lon, s.lat, s.lon))
    if haversine_m(lat, lon, best.lat, best.lon) > 1200:
        return 0.0
    return best.fused_score


def _flagged_along_geometry(
    geometry: dict[str, Any],
    scores: list[RiskScore],
    flagged: set[str],
) -> list[str]:
    hits: set[str] = set()
    for lon, lat in geometry.get("coordinates") or []:
        cell = h3.latlng_to_cell(lat, lon, 8)
        if cell in flagged:
            hits.add(cell)
            continue
        score = _match_point_to_score(lat, lon, scores)
        if score >= FLAGGED_SEGMENT_THRESHOLD:
            nearby = min(scores, key=lambda s: haversine_m(lat, lon, s.lat, s.lon))
            hits.add(nearby.h3_index)
    return sorted(hits)


def score_osrm_route(
    raw: dict[str, Any],
    scores: list[RiskScore],
    flagged: list[str],
    baseline_duration: float | None = None,
) -> RouteCandidate:
    geometry = raw["geometry"]
    duration = float(raw.get("duration") or 0.0)
    distance = float(raw.get("distance") or 0.0)
    coords = geometry.get("coordinates") or []
    if len(coords) < 2:
        weighted = duration
    else:
        weighted_acc = 0.0
        legs = raw.get("legs") or [{}]
        durations = []
        for leg in legs:
            ann = (leg.get("annotation") or {}).get("duration") or []
            durations.extend(ann)
        if durations and len(durations) == len(coords) - 1:
            for i, d in enumerate(durations):
                lon, lat = coords[i]
                risk = _match_point_to_score(lat, lon, scores)
                weighted_acc += float(d) * (1.0 + RISK_PENALTY_LAMBDA * risk)
            weighted = weighted_acc
        else:
            # Uniform sample of the polyline
            n = min(len(coords), 80)
            step = max(1, len(coords) // n)
            risks = [
                _match_point_to_score(coords[i][1], coords[i][0], scores)
                for i in range(0, len(coords), step)
            ]
            mean_risk = sum(risks) / max(1, len(risks))
            weighted = duration * (1.0 + RISK_PENALTY_LAMBDA * mean_risk)

    hit = _flagged_along_geometry(geometry, scores, set(flagged))
    eta_delta = 0.0 if baseline_duration is None else duration - baseline_duration
    return RouteCandidate(
        geometry=geometry,
        duration_s=duration,
        distance_m=distance,
        risk_weighted_duration_s=weighted,
        eta_delta_s=eta_delta,
        avoids_flagged=len(hit) == 0,
        flagged_h3=hit,
        source="osrm",
    )


def _via_candidates(flagged_scores: list[RiskScore]) -> list[tuple[float, float]]:
    if flagged_scores:
        lat = sum(s.lat for s in flagged_scores) / len(flagged_scores)
        lon = sum(s.lon for s in flagged_scores) / len(flagged_scores)
    else:
        lat, lon = HAZARD_ZONE_LAT, HAZARD_ZONE_LON
    # East/west offsets off the Beas; keep them small enough that OSRM can snap.
    return [
        (lat, lon + dlon)
        for dlon in (0.035, -0.035, 0.06, -0.06, 0.02)
    ]


class RoutingEngine:
    def compute_route(
        self,
        origin: tuple[float, float],
        dest: tuple[float, float],
        scores: list[RiskScore] | None = None,
        flagged_h3: list[str] | None = None,
        want_alternate: bool = False,
    ) -> tuple[RouteCandidate, RouteCandidate | None]:
        scores = scores or []
        flagged_h3 = flagged_h3 or []
        raw_routes = osrm_route(origin, dest, alternatives=True)
        if not raw_routes:
            raise OsrmUnavailable("OSRM returned no routes")

        primary = score_osrm_route(raw_routes[0], scores, flagged_h3)
        candidates = [score_osrm_route(r, scores, flagged_h3, primary.duration_s) for r in raw_routes]

        if not want_alternate:
            return primary, None

        flagged_scores = [s for s in scores if s.h3_index in set(flagged_h3)]
        avoiding = [c for c in candidates[1:] if c.avoids_flagged]
        avoiding.sort(key=lambda c: c.risk_weighted_duration_s)
        if avoiding:
            alt = avoiding[0]
            alt.via_used = False
            return primary, alt

        best_via: RouteCandidate | None = None
        for via in _via_candidates(flagged_scores):
            try:
                via_raw = osrm_route(
                    origin, dest, alternatives=False, waypoints=[via], retries=2
                )
            except OsrmUnavailable:
                continue
            if not via_raw:
                continue
            alt = score_osrm_route(via_raw[0], scores, flagged_h3, primary.duration_s)
            alt.via_used = True
            if best_via is None or alt.risk_weighted_duration_s < best_via.risk_weighted_duration_s:
                best_via = alt
            if alt.avoids_flagged:
                return primary, alt

        rest = sorted(candidates[1:], key=lambda c: c.risk_weighted_duration_s)
        return primary, best_via or (rest[0] if rest else None)
