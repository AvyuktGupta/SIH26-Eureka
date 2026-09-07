"""Runtime loop. Strictly L1 → L2 → L3 → L4 → (maybe) L5 → (maybe) L6.

This module is the conductor, not a seventh layer. It only passes the defined
contracts between layers. On a normal hazard-free drive it stops at L4.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import (
    DEMO_DEST_LAT,
    DEMO_DEST_LON,
    DEMO_ORIGIN_LAT,
    DEMO_ORIGIN_LON,
)
from app.contracts.schemas import (
    GateDecision,
    HazardReading,
    HysteresisState,
    RiskScore,
    RouteCandidate,
    TickResult,
)
from app.l1_ingestion.poller import CORRIDOR_SAMPLES
from app.l1_ingestion.weather import WeatherFeedDown
from app.l2_geospatial.corridor import buffer_corridor_geojson
from app.l2_geospatial.feature_store import build_road_segment_risks
from app.l3_prediction.predict import boost_hazard_zone, predict_segment
from app.l4_fusion.fusion_engine import RiskFusionEngine
from app.l5_routing.routing_engine import OsrmUnavailable, RoutingEngine
from app.l6_agent.agent import invoke_agent


@dataclass
class SessionState:
    session_id: str
    origin: tuple[float, float]
    dest: tuple[float, float]
    demo_mode: str = "normal"
    kill_weather: bool = False
    extra_precip_mm: float = 0.0
    glof_scene: str = "beas_static_clear"
    tick: int = 0
    progress: float = 0.0  # 0..1 along primary route
    route: RouteCandidate | None = None
    alternate: RouteCandidate | None = None
    last_scores: list[RiskScore] = field(default_factory=list)
    last_gate: GateDecision | None = None
    last_alert: Any = None
    llm_fired_for_confirmed: bool = False
    notes: list[str] = field(default_factory=list)


SESSIONS: dict[str, SessionState] = {}
_fusion = RiskFusionEngine()
_routing = RoutingEngine()


def _point_along(geometry: dict, t: float) -> tuple[float, float]:
    coords = geometry.get("coordinates") or []
    if not coords:
        return DEMO_ORIGIN_LAT, DEMO_ORIGIN_LON
    t = max(0.0, min(1.0, t))
    idx = t * (len(coords) - 1)
    i = int(math.floor(idx))
    frac = idx - i
    if i >= len(coords) - 1:
        lon, lat = coords[-1]
        return lat, lon
    lon = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
    lat = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
    return lat, lon


def start_session(
    origin: tuple[float, float] | None = None,
    dest: tuple[float, float] | None = None,
    demo_mode: str = "normal",
) -> SessionState:
    origin = origin or (DEMO_ORIGIN_LAT, DEMO_ORIGIN_LON)
    dest = dest or (DEMO_DEST_LAT, DEMO_DEST_LON)
    session_id = uuid.uuid4().hex[:10]
    route, _ = _routing.compute_route(origin, dest, want_alternate=False)
    state = SessionState(
        session_id=session_id,
        origin=origin,
        dest=dest,
        demo_mode=demo_mode,
        route=route,
    )
    SESSIONS[session_id] = state
    return state


def _active_geometry(state: SessionState) -> list[list[float]]:
    if state.route:
        return list(state.route.geometry.get("coordinates") or [])
    return [
        [state.origin[1], state.origin[0]],
        [state.dest[1], state.dest[0]],
    ]


def run_tick(
    session_id: str,
    *,
    demo_mode: str | None = None,
    kill_weather: bool | None = None,
    inject_spike: bool = False,
    extra_precip_mm: float | None = None,
    glof_scene: str | None = None,
    step: bool = True,
) -> TickResult:
    state = SESSIONS.get(session_id)
    if state is None:
        raise KeyError(session_id)
    if demo_mode is not None:
        state.demo_mode = demo_mode
    if kill_weather is not None:
        state.kill_weather = kill_weather
    if extra_precip_mm is not None:
        state.extra_precip_mm = extra_precip_mm
    if glof_scene is not None:
        state.glof_scene = glof_scene

    state.tick += 1
    if step:
        # Hazard demo crawls so hysteresis can accumulate over several ticks.
        increment = 0.035 if state.demo_mode == "normal" else 0.018
        state.progress = min(1.0, state.progress + increment)

    notes: list[str] = []
    fallback_active = False
    coords = _active_geometry(state)

    # --- L1 / L2 ---
    try:
        rows = build_road_segment_risks(
            state.session_id,
            coords,
            CORRIDOR_SAMPLES,
            demo_mode=state.demo_mode,
            extra_precip_mm=state.extra_precip_mm,
            kill_weather=state.kill_weather,
        )
    except WeatherFeedDown:
        fallback_active = True
        notes.append("weather feed down and no last-known sample — using static terrain prior")
        rows = build_road_segment_risks(
            state.session_id,
            coords,
            CORRIDOR_SAMPLES,
            demo_mode="normal",
            extra_precip_mm=0.0,
            kill_weather=True,
        )
        # Force stale flags; fusion will apply terrain prior.
        for row in rows:
            row.source_flags["weather"] = "unavailable"
            row.source_flags["weather_stale"] = "true"

    if any(r.source_flags.get("weather_stale") == "true" for r in rows) or state.kill_weather:
        fallback_active = True
        notes.append("L1 degraded: last-known weather + static terrain risk (FR-19)")

    # --- L3 ---
    readings_by_h3: dict[str, list[HazardReading]] = {}
    boosted_rows = []
    for row in rows:
        adj = boost_hazard_zone(row, state.demo_mode if not inject_spike else "spike")
        boosted_rows.append(adj)
        readings_by_h3[row.h3_index] = predict_segment(
            adj,
            include_glof=True,
            glof_scene=state.glof_scene,
        )
    rows = boosted_rows

    spike_h3 = None
    if inject_spike and rows:
        on_route = [r for r in rows if r.on_active_route] or rows
        spike_h3 = max(on_route, key=lambda r: r.slope_deg).h3_index
        notes.append(f"injected single-tick spike on {spike_h3} — hysteresis must reject it")

    # --- L4 ---
    scores = _fusion.fuse_corridor(
        state.session_id,
        rows,
        readings_by_h3,
        used_fallback=fallback_active,
        spike_h3=spike_h3,
    )
    gate = _fusion.gate(scores)

    # --- L5 only if hysteresis confirmed ---
    alternate = None
    if gate.hysteresis_confirmed:
        try:
            primary, alternate = _routing.compute_route(
                state.origin,
                state.dest,
                scores=scores,
                flagged_h3=gate.flagged_h3,
                want_alternate=True,
            )
            state.route = primary
            state.alternate = alternate
            gate.reroute_invoked = True
            gate.stopped_at = "L5"
            notes.append("L5 recomputed risk-weighted route via OSRM (LLM did not invent the path)")
            if alternate:
                notes.append(
                    f"alternate avoids_flagged={alternate.avoids_flagged} "
                    f"eta_delta_s={alternate.eta_delta_s:.0f}"
                )
        except OsrmUnavailable as exc:
            notes.append(f"OSRM unavailable during reroute: {exc}")
            gate.reroute_invoked = False
    else:
        gate.reroute_invoked = False
        gate.llm_invoked = False
        gate.stopped_at = "L4"

    # --- L6 only on confirmed TRANSITION, not every confirmed tick ---
    alert = None
    confirmed_now = gate.hysteresis_confirmed
    if confirmed_now and not state.llm_fired_for_confirmed:
        alert = invoke_agent(
            state.session_id,
            scores,
            gate.flagged_h3,
            state.route,
            alternate or state.alternate,
            state.demo_mode,
            fallback_active,
        )
        state.llm_fired_for_confirmed = True
        state.last_alert = alert
        gate.llm_invoked = True
        gate.stopped_at = "L6"
        notes.append("L6 gated agent fired on confirmed transition (JSON schema only)")
    elif confirmed_now:
        alert = state.last_alert
        gate.llm_invoked = False
        gate.stopped_at = "L6" if alert else "L5"
        notes.append("transition already alerted — LLM not re-invoked")
    else:
        # Reset so a future new confirmation can fire again after cooling.
        if all(s.hysteresis_state == HysteresisState.NORMAL for s in scores if s.on_active_route):
            state.llm_fired_for_confirmed = False
        gate.llm_invoked = False

    state.last_scores = scores
    state.last_gate = gate
    vehicle_lat, vehicle_lon = _point_along(
        (state.route.geometry if state.route else {"coordinates": coords}),
        state.progress,
    )

    return TickResult(
        session_id=state.session_id,
        tick=state.tick,
        vehicle={"lat": vehicle_lat, "lon": vehicle_lon, "progress": state.progress},
        route=state.route,
        alternate_route=(alternate or state.alternate) if confirmed_now else None,
        scores=scores,
        gate=gate,
        alert=alert,
        fallback_active=fallback_active,
        weather_killed=state.kill_weather,
        demo_mode=state.demo_mode,
        notes=notes,
    )


def session_snapshot(session_id: str) -> dict[str, Any]:
    state = SESSIONS[session_id]
    corridor = buffer_corridor_geojson(_active_geometry(state))
    return {
        "session_id": state.session_id,
        "origin": {"lat": state.origin[0], "lon": state.origin[1]},
        "destination": {"lat": state.dest[0], "lon": state.dest[1]},
        "demo_mode": state.demo_mode,
        "kill_weather": state.kill_weather,
        "tick": state.tick,
        "progress": state.progress,
        "route": state.route.model_dump() if state.route else None,
        "alternate_route": state.alternate.model_dump() if state.alternate else None,
        "corridor": corridor,
        "gate": state.last_gate.model_dump() if state.last_gate else None,
    }
