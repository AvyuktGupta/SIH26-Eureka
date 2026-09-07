# Architecture notes — folder to layer map

Source of truth: team architecture document (six strictly ordered layers) and
the IEEE 830 SRS for APCS / SIH26037. This file is a Q&A cheat sheet, not a
second spec.

## Demo region

Kullu → Manali on NH-3 (Beas valley, Himachal Pradesh). 15 km corridor buffer,
H3 resolution 8, sampled along the route plus a 1-ring so we stay inside the
corridor without filling thousands of empty hexes.

## Layer table ↔ code

| Layer | Responsibility (architecture doc) | Code |
| --- | --- | --- |
| L1 | Scheduled pollers, cache, free-tier rate limits | `backend/app/l1_ingestion/` — `weather.py` (Open-Meteo), `terrain.py` (Open-Elevation / Open-Meteo DEM), `glacier.py` (**stubbed CNN interface**), `poller.py` |
| L2 | 10–20 km corridor, H3 tiles, `RoadSegmentRisk` rows | `backend/app/l2_geospatial/` — `corridor.py`, `feature_store.py`; SQLite table `road_segment_risk` in `db.py` |
| L3 | One GBM per hazard; CNN only for glacier/GLOF | `backend/app/l3_prediction/` — `train.py` (synthetic labels), `predict.py` (XGBoost + GLOF stub wrapper) |
| L4 | Fuse p/s/e/confidence; hysteresis; time-series | `backend/app/l4_fusion/fusion_engine.py`; SQLite `risk_timeseries` + `hysteresis_memory` |
| L5 | OSRM; risk as edge-weight penalty; never the LLM | `backend/app/l5_routing/routing_engine.py` |
| L6 | Gated local LLM; 3 tools; JSON schema only | `backend/app/l6_agent/` — `agent.py`, `tools.py` |

Shared contracts (UML objects): `backend/app/contracts/schemas.py`
(`RoadSegmentRisk`, `HazardReading`, `RiskScore`, `DriverAlert`, `GateDecision`).

Conductor (not a seventh layer): `backend/app/runtime/tick.py` calls L1→L6 in
order and **stops at L4** unless hysteresis confirms a transition.

Only L4 and L6 (via `get_risk_trend`) read the time-series store.

## Runtime gates

Every GPS/tick:

1. L1 cache read (poller, not per-request HTTP)
2. L2 upsert `RoadSegmentRisk`
3. L3 GBM (+ async-style GLOF stub)
4. L4 fuse + Schmitt hysteresis (`enter=0.62`, `exit=0.40`, `min_ticks=3`)
5. **If not confirmed → stop.** This is the cheap path and the main “lightweight” demo beat.
6. L5 OSRM recompute with `duration * (1 + λ * fused_score)`
7. L6 Ollama, **once per confirmed transition**, tools:
   `get_risk_trend`, `get_candidate_routes`, `get_driver_context`
   Output validated against `DRIVER_ALERT_JSON_SCHEMA`.

## Fallback (FR-19, FR-20)

Kill-weather toggle in the UI sets `kill_weather` on the tick. L1 returns
last-known Open-Meteo samples (or a marked-stale placeholder). L4 still emits a
fused score using static terrain prior (slope). The driver never gets a silent
all-clear because a feed dropped.

## UI

`frontend/src/` — Leaflet + OSM/Carto dark tiles, H3 risk overlay, primary
(blue) vs alternate (dashed amber) routes, pipeline badge, alert card bound to
JSON fields only.

## What we deliberately did not build

Physics-GNN, ConvLSTM+ResNet, native Android, national multi-region, emergency
broadcast, vehicle actuation. GBM-only for landslide/flood/avalanche, as in the
architecture execution plan.
