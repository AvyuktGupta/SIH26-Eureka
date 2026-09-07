"""L4 risk fusion + hysteresis + time-series state.

A single noisy reading must not trigger a reroute. Implementation is a Schmitt
trigger (enter/exit thresholds) plus a consecutive-tick counter.
"""

from __future__ import annotations

import time
from typing import Sequence

from app import db
from app.config import (
    FUSION_ENTER_THRESHOLD,
    FUSION_EXIT_THRESHOLD,
    HYSTERESIS_MIN_TICKS,
)
from app.contracts.schemas import (
    GateDecision,
    HazardReading,
    HazardType,
    HysteresisState,
    RiskScore,
    RoadSegmentRisk,
)


def _noisy_or(values: Sequence[float]) -> float:
    acc = 1.0
    for v in values:
        acc *= 1.0 - max(0.0, min(1.0, v))
    return 1.0 - acc


def fuse_readings(
    row: RoadSegmentRisk,
    readings: list[HazardReading],
    *,
    used_fallback: bool = False,
    terrain_prior: float | None = None,
) -> tuple[float, float, float, float, float, HazardType]:
    if not readings:
        prior = terrain_prior if terrain_prior is not None else 0.0
        return prior, prior, 0.4, 0.3, 0.3, HazardType.TERRAIN

    components = []
    for r in readings:
        raw = r.probability * r.severity * r.exposure
        components.append(r.confidence * raw + (1.0 - r.confidence) * (terrain_prior or 0.0))

    fused = _noisy_or(components)
    # Dominant hazard by contribution
    dominant = max(
        readings,
        key=lambda r: r.probability * r.severity * r.exposure,
    ).hazard

    probability = max(r.probability for r in readings)
    severity = max(r.severity for r in readings)
    exposure = max(r.exposure for r in readings)
    confidence = sum(r.confidence for r in readings) / len(readings)
    if used_fallback:
        confidence = min(confidence, 0.45)
        if terrain_prior is not None:
            fused = max(fused, terrain_prior)
    return (
        max(0.0, min(1.0, fused)),
        probability,
        severity,
        exposure,
        confidence,
        dominant,
    )


def terrain_prior(row: RoadSegmentRisk) -> float:
    """Static terrain risk used when live feeds drop (FR-19 / FR-20)."""
    return max(0.0, min(1.0, (row.slope_deg / 45.0) * 0.45))


def apply_hysteresis(
    session_id: str,
    h3_index: str,
    fused: float,
    memory: dict[str, dict],
) -> tuple[HysteresisState, int]:
    slot = memory.get(
        h3_index,
        {"state": HysteresisState.NORMAL.value, "pending_ticks": 0, "last_score": 0.0},
    )
    state = HysteresisState(slot["state"])
    pending = int(slot["pending_ticks"])

    if state in {HysteresisState.NORMAL, HysteresisState.PENDING}:
        if fused >= FUSION_ENTER_THRESHOLD:
            pending += 1
            state = (
                HysteresisState.CONFIRMED
                if pending >= HYSTERESIS_MIN_TICKS
                else HysteresisState.PENDING
            )
        else:
            pending = 0
            state = HysteresisState.NORMAL
    elif state == HysteresisState.CONFIRMED:
        if fused <= FUSION_EXIT_THRESHOLD:
            state = HysteresisState.COOLING
            pending = 0
        else:
            pending = HYSTERESIS_MIN_TICKS
    elif state == HysteresisState.COOLING:
        if fused >= FUSION_ENTER_THRESHOLD:
            pending += 1
            state = (
                HysteresisState.CONFIRMED
                if pending >= HYSTERESIS_MIN_TICKS
                else HysteresisState.PENDING
            )
        elif fused <= FUSION_EXIT_THRESHOLD:
            state = HysteresisState.NORMAL
            pending = 0

    memory[h3_index] = {
        "state": state.value,
        "pending_ticks": pending,
        "last_score": fused,
    }
    return state, pending


class RiskFusionEngine:
    def fuse_corridor(
        self,
        session_id: str,
        rows: list[RoadSegmentRisk],
        readings_by_h3: dict[str, list[HazardReading]],
        *,
        used_fallback: bool = False,
        spike_h3: str | None = None,
    ) -> list[RiskScore]:
        memory = db.load_hysteresis(session_id)
        scores: list[RiskScore] = []
        now = int(time.time())
        for row in rows:
            readings = readings_by_h3.get(row.h3_index, [])
            prior = terrain_prior(row)
            fused, p, s, e, c, dominant = fuse_readings(
                row,
                readings,
                used_fallback=used_fallback,
                terrain_prior=prior,
            )
            if spike_h3 and row.h3_index == spike_h3:
                fused = min(1.0, max(fused, 0.92))
                dominant = HazardType.LANDSLIDE
            state, pending = apply_hysteresis(session_id, row.h3_index, fused, memory)
            score = RiskScore(
                h3_index=row.h3_index,
                lat=row.lat,
                lon=row.lon,
                fused_score=fused,
                probability=p,
                severity=s,
                exposure=e,
                confidence=c,
                hysteresis_state=state,
                pending_ticks=pending,
                dominant_hazard=dominant,
                readings=readings,
                on_active_route=row.on_active_route,
                used_fallback=used_fallback,
            )
            scores.append(score)
            db.append_risk(
                session_id,
                {
                    "h3_index": row.h3_index,
                    "ts": now,
                    "fused_score": fused,
                    "probability": p,
                    "severity": s,
                    "exposure": e,
                    "confidence": c,
                    "hysteresis_state": state.value,
                    "pending_ticks": pending,
                    "dominant_hazard": dominant.value,
                    "used_fallback": used_fallback,
                },
            )
        db.save_hysteresis(session_id, memory)
        return scores

    def gate(self, scores: list[RiskScore]) -> GateDecision:
        active = [s for s in scores if s.on_active_route]
        pool = active or scores
        flagged = [
            s
            for s in pool
            if s.hysteresis_state == HysteresisState.CONFIRMED
            and s.fused_score >= FUSION_ENTER_THRESHOLD
        ]
        pending_high = [
            s
            for s in pool
            if s.fused_score >= FUSION_ENTER_THRESHOLD
            and s.hysteresis_state != HysteresisState.CONFIRMED
        ]
        if flagged:
            return GateDecision(
                threshold_crossed=True,
                hysteresis_confirmed=True,
                reroute_invoked=True,
                llm_invoked=True,
                stopped_at="L6",
                reason="fused risk crossed threshold and hysteresis confirmed a real transition",
                flagged_h3=[s.h3_index for s in flagged],
            )
        if pending_high:
            return GateDecision(
                threshold_crossed=True,
                hysteresis_confirmed=False,
                reroute_invoked=False,
                llm_invoked=False,
                stopped_at="L4",
                reason="threshold crossed but hysteresis has not confirmed — single spike / still pending",
                flagged_h3=[s.h3_index for s in pending_high],
            )
        return GateDecision(
            threshold_crossed=False,
            hysteresis_confirmed=False,
            reroute_invoked=False,
            llm_invoked=False,
            stopped_at="L4",
            reason="risk below threshold — cheap gate, LLM not invoked",
            flagged_h3=[],
        )


def get_risk_trend(session_id: str, h3_index: str, limit: int = 12) -> list[dict]:
    """L6 tool surface — reads the time-series store only."""
    return db.get_trend(session_id, h3_index, limit=limit)
