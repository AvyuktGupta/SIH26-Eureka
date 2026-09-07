"""Cross-layer data contracts. Later layers consume these objects only.

Matches the architecture UML: RoadSegment / RoadSegmentRisk, HazardReading,
RiskScore. L6 output is schema-constrained JSON (never free text alone).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HazardType(str, Enum):
    LANDSLIDE = "landslide"
    FLOOD = "flood"
    AVALANCHE = "avalanche"
    GLOF = "glof"
    TERRAIN = "terrain"


class HysteresisState(str, Enum):
    NORMAL = "normal"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COOLING = "cooling"


class WeatherSample(BaseModel):
    lat: float
    lon: float
    precipitation_mm: float = 0.0
    precip_24h_mm: float = 0.0
    temperature_c: float = 15.0
    wind_ms: float = 1.0
    snowfall_cm: float = 0.0
    source: str = "open-meteo"
    fetched_at: int = 0
    stale: bool = False


class TerrainSample(BaseModel):
    lat: float
    lon: float
    elevation_m: float
    slope_deg: float = 0.0
    aspect_deg: float = 0.0
    source: str = "open-elevation"
    stale: bool = False


class RoadSegmentRisk(BaseModel):
    """L2 row: one H3-tiled road segment in the active corridor."""

    h3_index: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    slope_deg: float = 0.0
    aspect_deg: float = 0.0
    precipitation_mm: float = 0.0
    precip_24h_mm: float = 0.0
    temperature_c: float = 15.0
    wind_ms: float = 1.0
    snowfall_cm: float = 0.0
    on_active_route: bool = False
    source_flags: dict[str, str] = Field(default_factory=dict)


class HazardReading(BaseModel):
    """L3 output for one hazard on one segment."""

    h3_index: str
    hazard: HazardType
    probability: float = Field(ge=0.0, le=1.0)
    severity: float = Field(ge=0.0, le=1.0)
    exposure: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    model: str
    synthetic_labels: bool = False
    stubbed: bool = False


class RiskScore(BaseModel):
    """L4 fused, hysteresis-stabilised score for one segment."""

    h3_index: str
    lat: float
    lon: float
    fused_score: float = Field(ge=0.0, le=1.0)
    probability: float
    severity: float
    exposure: float
    confidence: float
    hysteresis_state: HysteresisState = HysteresisState.NORMAL
    pending_ticks: int = 0
    dominant_hazard: HazardType = HazardType.LANDSLIDE
    readings: list[HazardReading] = Field(default_factory=list)
    on_active_route: bool = False
    used_fallback: bool = False


class GateDecision(BaseModel):
    """Runtime gates. On a normal drive the loop stops at L4."""

    threshold_crossed: bool = False
    hysteresis_confirmed: bool = False
    reroute_invoked: bool = False
    llm_invoked: bool = False
    stopped_at: Literal["L4", "L5", "L6"] = "L4"
    reason: str = "risk below threshold"
    flagged_h3: list[str] = Field(default_factory=list)


class RouteCandidate(BaseModel):
    geometry: dict[str, Any]
    duration_s: float
    distance_m: float
    risk_weighted_duration_s: float
    eta_delta_s: float = 0.0
    avoids_flagged: bool = True
    flagged_h3: list[str] = Field(default_factory=list)
    via_used: bool = False
    source: str = "osrm"


class DriverAlert(BaseModel):
    """L6 schema-constrained output. UI must consume this object, not prose."""

    alert_level: Literal["info", "warning", "critical"]
    headline: str
    explanation: str
    recommended_action: Literal["continue", "reroute", "stop"]
    hazard_type: str
    confidence: float
    eta_delta_minutes: float = 0.0
    source: Literal["llm", "llm_unavailable_template"] = "llm"


class TickResult(BaseModel):
    session_id: str
    tick: int
    vehicle: dict[str, float]
    route: Optional[RouteCandidate] = None
    alternate_route: Optional[RouteCandidate] = None
    scores: list[RiskScore] = Field(default_factory=list)
    gate: GateDecision
    alert: Optional[DriverAlert] = None
    fallback_active: bool = False
    weather_killed: bool = False
    demo_mode: str = "normal"
    notes: list[str] = Field(default_factory=list)


DRIVER_ALERT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "alert_level": {"type": "string", "enum": ["info", "warning", "critical"]},
        "headline": {"type": "string"},
        "explanation": {"type": "string"},
        "recommended_action": {
            "type": "string",
            "enum": ["continue", "reroute", "stop"],
        },
        "hazard_type": {"type": "string"},
        "confidence": {"type": "number"},
        "eta_delta_minutes": {"type": "number"},
    },
    "required": [
        "alert_level",
        "headline",
        "explanation",
        "recommended_action",
        "hazard_type",
        "confidence",
        "eta_delta_minutes",
    ],
    "additionalProperties": False,
}
