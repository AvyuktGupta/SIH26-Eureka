"""L3 hazard prediction — one GBM per hazard, stubbed CNN for glacier/GLOF.

Inference is a small XGBoost forward pass per segment (milliseconds).
"""

from __future__ import annotations

import math
import os

import numpy as np
from xgboost import XGBClassifier

from app.config import HAZARD_ZONE_LAT, HAZARD_ZONE_LON, MODELS_DIR
from app.contracts.schemas import HazardReading, HazardType, RoadSegmentRisk
from app.l1_ingestion.glacier import default_scorer
from app.l3_prediction.train import train_all, vectorize

_MODELS: dict[str, XGBClassifier] = {}


def ensure_models() -> None:
    needed = [HazardType.LANDSLIDE, HazardType.FLOOD, HazardType.AVALANCHE]
    missing = [
        h for h in needed if not os.path.exists(os.path.join(MODELS_DIR, f"{h.value}_xgb.json"))
    ]
    if missing:
        os.makedirs(MODELS_DIR, exist_ok=True)
        train_all()


def _load(hazard: HazardType) -> XGBClassifier:
    if hazard.value in _MODELS:
        return _MODELS[hazard.value]
    ensure_models()
    path = os.path.join(MODELS_DIR, f"{hazard.value}_xgb.json")
    model = XGBClassifier()
    model.load_model(path)
    _MODELS[hazard.value] = model
    return model


def _features(row: RoadSegmentRisk) -> np.ndarray:
    return np.array(
        [
            vectorize(
                row.slope_deg,
                row.aspect_deg,
                row.elevation_m,
                row.precipitation_mm,
                row.precip_24h_mm,
                row.temperature_c,
                row.wind_ms,
                row.snowfall_cm,
            )
        ]
    )


def _severity(hazard: HazardType, row: RoadSegmentRisk, probability: float) -> float:
    if hazard == HazardType.LANDSLIDE:
        return float(np.clip(0.35 + row.slope_deg / 60.0 + probability * 0.2, 0, 1))
    if hazard == HazardType.FLOOD:
        return float(np.clip(0.3 + row.precip_24h_mm / 140.0, 0, 1))
    return float(np.clip(0.4 + row.snowfall_cm / 40.0, 0, 1))


def _exposure(row: RoadSegmentRisk) -> float:
    return 0.85 if row.on_active_route else 0.35


def predict_gbm(row: RoadSegmentRisk, hazard: HazardType) -> HazardReading:
    model = _load(hazard)
    proba = float(model.predict_proba(_features(row))[0, 1])
    # Confidence is higher when features are in-distribution-ish (non-stale weather).
    stale = row.source_flags.get("weather_stale") == "true"
    confidence = 0.55 if stale else 0.82
    return HazardReading(
        h3_index=row.h3_index,
        hazard=hazard,
        probability=max(0.0, min(1.0, proba)),
        severity=_severity(hazard, row, proba),
        exposure=_exposure(row),
        confidence=confidence,
        model=f"xgboost:{hazard.value}",
        synthetic_labels=True,
        stubbed=False,
    )


def predict_glof(row: RoadSegmentRisk, scene_id: str = "beas_static_clear") -> HazardReading:
    score = default_scorer().score(scene_id)
    # GLOF exposure is higher in the upper Beas / glacier-adjacent north of Manali.
    northness = max(0.0, (row.lat - 32.18) / 0.12)
    return HazardReading(
        h3_index=row.h3_index,
        hazard=HazardType.GLOF,
        probability=min(1.0, score.probability * (0.4 + 0.8 * northness)),
        severity=score.severity,
        exposure=_exposure(row),
        confidence=score.confidence,
        model="stub-cnn:glof",
        synthetic_labels=False,
        stubbed=True,
    )


def predict_segment(
    row: RoadSegmentRisk,
    *,
    include_glof: bool = True,
    glof_scene: str = "beas_static_clear",
) -> list[HazardReading]:
    readings = [
        predict_gbm(row, HazardType.LANDSLIDE),
        predict_gbm(row, HazardType.FLOOD),
        predict_gbm(row, HazardType.AVALANCHE),
    ]
    if include_glof:
        readings.append(predict_glof(row, glof_scene))
    return readings


def boost_hazard_zone(row: RoadSegmentRisk, demo_mode: str) -> RoadSegmentRisk:
    """Shift features near the known NH-3 slide stretch for the hazard demo.

    This still goes through the GBM — we do not hardcode the output probability.
    """
    if demo_mode not in {"hazard", "spike", "avalanche", "flood"}:
        return row
    dlat = row.lat - HAZARD_ZONE_LAT
    dlon = row.lon - HAZARD_ZONE_LON
    dist = math.hypot(dlat * 111.0, dlon * 96.0)  # rough km
    if dist > 4.5:
        return row
    out = row.model_copy()
    proximity = max(0.0, 1.0 - dist / 4.5)
    if demo_mode in {"hazard", "spike"}:
        out.precipitation_mm = max(out.precipitation_mm, 8 + 30 * proximity)
        out.precip_24h_mm = max(out.precip_24h_mm, 40 + 80 * proximity)
        out.slope_deg = max(out.slope_deg, 18 + 16 * proximity)
    if demo_mode == "flood":
        out.precipitation_mm = max(out.precipitation_mm, 35 * proximity)
        out.precip_24h_mm = max(out.precip_24h_mm, 120 * proximity)
        out.slope_deg = min(out.slope_deg, 6)
    if demo_mode == "avalanche":
        out.snowfall_cm = max(out.snowfall_cm, 20 * proximity)
        out.temperature_c = min(out.temperature_c, 1.0)
        out.slope_deg = max(out.slope_deg, 32 * proximity)
        out.elevation_m = max(out.elevation_m, 2800)
    return out
