from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import db
from app.config import (
    CORRIDOR_BUFFER_KM,
    DEMO_DEST_LAT,
    DEMO_DEST_LON,
    DEMO_ORIGIN_LAT,
    DEMO_ORIGIN_LON,
    FUSION_ENTER_THRESHOLD,
    FUSION_EXIT_THRESHOLD,
    H3_RESOLUTION,
    HYSTERESIS_MIN_TICKS,
)
from app.l2_geospatial.corridor import cell_boundary
from app.l3_prediction.predict import ensure_models
from app.l1_ingestion.poller import start_poller
from app.l6_agent.agent import probe_ollama
from app.runtime.tick import SESSIONS, run_tick, session_snapshot, start_session

log = logging.getLogger("apcs")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    ensure_models()
    try:
        start_poller()
    except Exception as exc:
        log.warning("L1 poller warm-up failed (will retry on ticks): %s", exc)
    yield


app = FastAPI(
    title="APCS — Adaptive Path & Collision-avoidance System",
    version="0.1.0",
    description="SIH 2026 / SIH26037 prototype. Six-layer architecture.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartBody(BaseModel):
    origin_lat: float = DEMO_ORIGIN_LAT
    origin_lon: float = DEMO_ORIGIN_LON
    dest_lat: float = DEMO_DEST_LAT
    dest_lon: float = DEMO_DEST_LON
    demo_mode: str = "normal"


class TickBody(BaseModel):
    demo_mode: str | None = None
    kill_weather: bool | None = None
    inject_spike: bool = False
    extra_precip_mm: float | None = None
    glof_scene: str | None = None
    step: bool = True


@app.get("/api/health")
def health():
    ollama = probe_ollama()
    return {"ok": True, "service": "apcs-backend", "ollama": ollama}


@app.get("/api/meta")
def meta():
    return {
        "problem": "SIH26037",
        "system": "APCS",
        "demo_region": {
            "name": "Kullu–Manali corridor, Himachal Pradesh (NH-3)",
            "origin": {"name": "Kullu", "lat": DEMO_ORIGIN_LAT, "lon": DEMO_ORIGIN_LON},
            "destination": {"name": "Manali", "lat": DEMO_DEST_LAT, "lon": DEMO_DEST_LON},
            "why": "Real monsoon landslide corridor on the Beas; compact OSM extract for a laptop demo.",
        },
        "corridor_buffer_km": CORRIDOR_BUFFER_KM,
        "h3_resolution": H3_RESOLUTION,
        "hysteresis": {
            "enter": FUSION_ENTER_THRESHOLD,
            "exit": FUSION_EXIT_THRESHOLD,
            "min_ticks": HYSTERESIS_MIN_TICKS,
        },
        "real_vs_stub": {
            "weather": "real — Open-Meteo",
            "terrain": "real — Open-Elevation, fallback Open-Meteo elevation (SRTM)",
            "satellite_glof": "STUBBED — static scene catalogue, not live satellite",
            "gbm_labels": "synthetic rule-based labels; XGBoost models are real and trained",
            "routing": "real OSRM on a local OSM extract",
            "llm": "local Ollama, gated, JSON schema only",
        },
    }


@app.post("/api/session/start")
def api_start(body: StartBody):
    try:
        state = start_session(
            origin=(body.origin_lat, body.origin_lon),
            dest=(body.dest_lat, body.dest_lon),
            demo_mode=body.demo_mode,
        )
        snap = session_snapshot(state.session_id)
        tick = run_tick(state.session_id, demo_mode=body.demo_mode, step=False)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not start session (is OSRM up?): {exc}") from exc
    return {"session": snap, "tick": _tick_payload(tick)}


@app.post("/api/session/{session_id}/tick")
def api_tick(session_id: str, body: TickBody):
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="unknown session")
    tick = run_tick(
        session_id,
        demo_mode=body.demo_mode,
        kill_weather=body.kill_weather,
        inject_spike=body.inject_spike,
        extra_precip_mm=body.extra_precip_mm,
        glof_scene=body.glof_scene,
        step=body.step,
    )
    return _tick_payload(tick)


@app.get("/api/session/{session_id}")
def api_session(session_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="unknown session")
    return session_snapshot(session_id)


def _tick_payload(tick) -> dict:
    overlay = []
    for s in tick.scores:
        overlay.append(
            {
                "h3_index": s.h3_index,
                "fused_score": s.fused_score,
                "hysteresis_state": s.hysteresis_state.value,
                "pending_ticks": s.pending_ticks,
                "dominant_hazard": s.dominant_hazard.value,
                "on_active_route": s.on_active_route,
                "used_fallback": s.used_fallback,
                "lat": s.lat,
                "lon": s.lon,
                "boundary": cell_boundary(s.h3_index),
            }
        )
    data = tick.model_dump()
    data["overlay"] = overlay
    # Keep payload lighter for the map: drop per-segment readings from scores.
    for item in data.get("scores") or []:
        item.pop("readings", None)
    return data
