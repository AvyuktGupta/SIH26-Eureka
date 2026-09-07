"""Exactly three tools. Facades over L4 time-series and L5 route candidates.

The agent must not reach into L1–L3 internals.
"""

from __future__ import annotations

from typing import Any, Optional

from app.contracts.schemas import RiskScore, RouteCandidate


def get_risk_trend_tool(session_id: str, h3_index: str) -> list[dict[str, Any]]:
    from app.l4_fusion.fusion_engine import get_risk_trend

    return get_risk_trend(session_id, h3_index)


def get_candidate_routes(
    primary: RouteCandidate | None,
    alternate: RouteCandidate | None,
) -> dict[str, Any]:
    def slim(route: RouteCandidate | None) -> dict[str, Any] | None:
        if route is None:
            return None
        return {
            "duration_s": round(route.duration_s, 1),
            "distance_m": round(route.distance_m, 1),
            "risk_weighted_duration_s": round(route.risk_weighted_duration_s, 1),
            "eta_delta_s": round(route.eta_delta_s, 1),
            "avoids_flagged": route.avoids_flagged,
            "via_used": route.via_used,
            "flagged_h3_count": len(route.flagged_h3),
        }

    return {"primary": slim(primary), "alternate": slim(alternate)}


def get_driver_context(
    scores: list[RiskScore],
    flagged: list[str],
    demo_mode: str,
    fallback_active: bool,
) -> dict[str, Any]:
    active = [s for s in scores if s.on_active_route] or scores
    flagged_scores = [s for s in scores if s.h3_index in set(flagged)] or active
    top = max(flagged_scores, key=lambda s: s.fused_score) if flagged_scores else None
    return {
        "demo_mode": demo_mode,
        "fallback_active": fallback_active,
        "corridor": "NH-3 Kullu–Manali, Himachal Pradesh",
        "flagged_segment_count": len(flagged),
        "max_fused_score": round(top.fused_score, 3) if top else 0.0,
        "dominant_hazard": top.dominant_hazard.value if top else "none",
        "confidence": round(top.confidence, 3) if top else 0.0,
        "hysteresis_state": top.hysteresis_state.value if top else "normal",
        "vehicle_context": "driver decision-support only — no vehicle actuation",
    }
