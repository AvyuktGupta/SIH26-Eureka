"""L3 training-set construction.

Labels are SYNTHETIC / rule-based. There is no public labelled landslide/flood/
avalanche dataset for this demo corridor. We still train real XGBoost models
so inference is a model forward pass (milliseconds), not an if/else at runtime.

Rules used only to generate y for training:
  landslide: steep slope + heavy rain
  flood: low slope + heavy rain + lower elevation
  avalanche: mid/steep slope + snow + near-freezing temperature
"""

from __future__ import annotations

import math
import os
from typing import Iterable

import numpy as np
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from app.config import MODELS_DIR
from app.contracts.schemas import HazardType

FEATURE_NAMES = [
    "slope_deg",
    "aspect_sin",
    "aspect_cos",
    "elevation_m",
    "precipitation_mm",
    "precip_24h_mm",
    "temperature_c",
    "wind_ms",
    "snowfall_cm",
]


def vectorize(
    slope_deg: float,
    aspect_deg: float,
    elevation_m: float,
    precipitation_mm: float,
    precip_24h_mm: float,
    temperature_c: float,
    wind_ms: float,
    snowfall_cm: float,
) -> list[float]:
    rad = math.radians(aspect_deg)
    return [
        slope_deg,
        math.sin(rad),
        math.cos(rad),
        elevation_m,
        precipitation_mm,
        precip_24h_mm,
        temperature_c,
        wind_ms,
        snowfall_cm,
    ]


def _synthetic_label(hazard: HazardType, rng: np.random.Generator, row: np.ndarray) -> int:
    slope, _s, _c, elev, precip, precip24, temp, _wind, snow = row
    if hazard == HazardType.LANDSLIDE:
        score = 0.0
        score += np.clip((slope - 12.0) / 25.0, 0, 1) * 0.55
        score += np.clip(precip24 / 80.0, 0, 1) * 0.35
        score += np.clip(precip / 20.0, 0, 1) * 0.15
        score += 0.05 * rng.normal()
        return int(score > 0.55)
    if hazard == HazardType.FLOOD:
        score = 0.0
        score += np.clip((8.0 - slope) / 8.0, 0, 1) * 0.35
        score += np.clip(precip24 / 100.0, 0, 1) * 0.45
        score += np.clip((1800.0 - elev) / 1200.0, 0, 1) * 0.2
        score += 0.05 * rng.normal()
        return int(score > 0.5)
    # avalanche
    slope_band = np.exp(-((slope - 35.0) ** 2) / (2 * 12.0**2))
    score = slope_band * 0.45
    score += np.clip(snow / 20.0, 0, 1) * 0.35
    score += np.clip((2.0 - abs(temp - 0.5)) / 6.0, 0, 1) * 0.2
    score += 0.05 * rng.normal()
    return int(score > 0.5)


def make_dataset(hazard: HazardType, n: int = 2400, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed + hash(hazard.value) % 1000)
    slope = rng.uniform(0, 50, n)
    aspect = rng.uniform(0, 360, n)
    elev = rng.uniform(800, 4200, n)
    precip = rng.gamma(1.2, 4.0, n)
    precip24 = precip * rng.uniform(2.0, 8.0, n)
    temp = rng.normal(10, 10, n)
    wind = rng.gamma(2.0, 1.5, n)
    snow = np.clip(rng.normal(2, 8, n), 0, 40)
    X = np.column_stack(
        [
            slope,
            np.sin(np.radians(aspect)),
            np.cos(np.radians(aspect)),
            elev,
            precip,
            precip24,
            temp,
            wind,
            snow,
        ]
    )
    y = np.array([_synthetic_label(hazard, rng, X[i]) for i in range(n)], dtype=int)
    # Keep both classes present
    if y.sum() == 0:
        y[: n // 5] = 1
    if y.sum() == n:
        y[: n // 5] = 0
    return X, y


def train_hazard_model(hazard: HazardType) -> str:
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, f"{hazard.value}_xgb.json")
    X, y = make_dataset(hazard)
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    model = XGBClassifier(
        n_estimators=64,
        max_depth=4,
        learning_rate=0.12,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        n_jobs=2,
        tree_method="hist",
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    model.save_model(path)
    acc = float((model.predict(X_val) == y_val).mean())
    print(f"trained {hazard.value} GBM  val_acc={acc:.3f}  -> {path}")
    return path


def train_all() -> dict[str, str]:
    return {
        h.value: train_hazard_model(h)
        for h in (HazardType.LANDSLIDE, HazardType.FLOOD, HazardType.AVALANCHE)
    }


if __name__ == "__main__":
    train_all()
