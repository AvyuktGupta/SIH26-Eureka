# APCS — Adaptive Path & Collision-avoidance System

Smart India Hackathon 2026, problem statement **SIH26037**.

A six-layer route-planning prototype: ingest weather and terrain along a driving
corridor, score per-segment hazard risk, fuse it with hysteresis, feed it into
OSRM as an edge-weight penalty, and call a local LLM **only** when a real risk
state transition is confirmed.

## Demo region

**NH-3 / Beas valley, Kullu → Manali, Himachal Pradesh**

| | |
| --- | --- |
| Origin | Kullu (31.9578, 77.1095) |
| Destination | Manali Mall Road (32.2396, 77.1887) |
| Why this corridor | Real monsoon landslide hotspot, compact OSM extract, credible GLOF-adjacent upper valley for the stubbed glacier path |

## What is real vs synthetic / stubbed

| Piece | Status |
| --- | --- |
| Weather | **Real** — [Open-Meteo](https://open-meteo.com/) (no API key) |
| Terrain / DEM | **Real** — Open-Elevation, with Open-Meteo elevation (SRTM) as fallback |
| Corridor / H3 tiles | **Real** — 15 km buffer, H3 resolution 8 along the route |
| Routing | **Real** — local OSRM on an OSM highway extract of this bbox |
| Landslide / flood / avalanche models | **Real XGBoost**, trained on **synthetic rule-based labels** (no public labelled set for this corridor) |
| Glacier / GLOF CNN | **Stubbed** — static scene catalogue, not live satellite. Interface is in `backend/app/l1_ingestion/glacier.py` |
| LLM alerts | **Real local Ollama** (`llama3.2:3b`), schema-constrained JSON, gated behind threshold + hysteresis |

The synthetic labels and the GLOF stub are called out in code comments. Do not
present the glacier path as live satellite inference.

## One-command demo (Docker)

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) first.
This machine's repo is ready; the first start downloads OSM, builds the OSRM
graph, trains GBM models, and pulls the 3B Ollama model (several GB, several
minutes).

```bash
docker compose up --build
```

Then open **http://localhost:8080**.

Services:

| Service | Port |
| --- | --- |
| Frontend (nginx) | 8080 |
| Backend (FastAPI) | 8000 |
| OSRM | 5000 |
| Ollama | 11434 |

If Overpass is slow on first boot, wait and retry `docker compose up`. The OSM
extract is cached in the `osrm-data` volume afterwards.

## Demo script (for judges)

1. **Normal drive** — click *New route*, scenario **Normal**, *Start drive*.
   Risk stays low. The pipeline badge **stops at L4**. LLM cost is zero.
2. **Hazard drive** — switch scenario to **Hazard (landslide)** and keep driving.
   Rain/slope features in the Katrain stretch rise; fused score climbs.
3. **Hysteresis** — a single noisy tick is not enough. Use *Inject one noisy
   spike* on a normal run: threshold may fire, hysteresis **holds**, no reroute.
   On the hazard run, three consecutive high ticks **confirm**.
4. **Reroute** — L5 asks OSRM for an alternate, applies risk as an edge-weight
   penalty, and reports ETA delta. The LLM does not invent the path.
5. **LLM alert** — only on the confirmed transition. A low-distraction card
   renders schema JSON (`headline`, `recommended_action`, …), not parsed prose.
6. **Fallback** — tick **Kill weather API**. The map must still show last-known
   risk plus static terrain risk, never a silent “no warning”.

## Local dev (without Docker)

Requires a running OSRM (Docker service, or temporarily `OSRM_URL=https://router.project-osrm.org` for UI work only) and optional Ollama.

```bash
# backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend
npm install
npm run dev
```

UI: http://localhost:5173 (Vite proxies `/api` to the backend).

To train models only:

```bash
cd backend
python -m app.l3_prediction.train
```

## Hard design rules (enforced in code)

- Routes come from OSRM / graph search — never from the LLM.
- The LLM never computes risk, paths, or collision-avoidance manoeuvres.
- LLM output is schema-constrained JSON only.
- Data scope is the 10–20 km route corridor, not global.
- Threshold + hysteresis run as plain code on every tick; the LLM runs only on
  confirmed transitions.

## Out of scope (per SRS)

National-scale deployment, certified emergency broadcast, full L4/L5 vehicle
control, Physics-GNN flash-flood and ConvLSTM+ResNet avalanche models, native
Android client.
