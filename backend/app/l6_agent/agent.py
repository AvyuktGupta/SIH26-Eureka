"""L6 gated LLM agent.

Invoked only after L4 confirms a real state transition. Three tools only.
Output is schema-constrained JSON — the UI never parses free text.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import OLLAMA_MODEL, OLLAMA_URL
from app.contracts.schemas import (
    DRIVER_ALERT_JSON_SCHEMA,
    DriverAlert,
    RiskScore,
    RouteCandidate,
)
from app.l4_fusion.fusion_engine import get_risk_trend
from app.l6_agent.tools import get_candidate_routes, get_driver_context


SYSTEM_PROMPT = """You are the APCS driver-alert agent for a Himachal Pradesh hill-road demo.
You do not compute routes, risk scores, or collision-avoidance manoeuvres.
You only explain the already-computed fused risk and the OSRM candidate routes.
Return JSON matching the schema. Keep headline ≤ 80 characters. Be calm and specific.
"""


def _tool_payload(
    session_id: str,
    scores: list[RiskScore],
    flagged: list[str],
    primary: RouteCandidate | None,
    alternate: RouteCandidate | None,
    demo_mode: str,
    fallback_active: bool,
) -> dict[str, Any]:
    h3 = flagged[0] if flagged else (scores[0].h3_index if scores else "")
    trend = get_risk_trend(session_id, h3) if h3 else []
    return {
        "get_risk_trend": trend,
        "get_candidate_routes": get_candidate_routes(primary, alternate),
        "get_driver_context": get_driver_context(
            scores, flagged, demo_mode, fallback_active
        ),
    }


def _template_alert(
    tools: dict[str, Any],
    eta_delta_s: float,
    reason: str,
) -> DriverAlert:
    ctx = tools["get_driver_context"]
    hazard = ctx.get("dominant_hazard", "landslide")
    level = "critical" if ctx.get("max_fused_score", 0) >= 0.8 else "warning"
    minutes = round(eta_delta_s / 60.0, 1)
    action = "reroute" if tools["get_candidate_routes"].get("alternate") else "stop"
    return DriverAlert(
        alert_level=level,
        headline=f"{hazard.replace('_', ' ').title()} risk confirmed on NH-3 corridor",
        explanation=(
            f"{reason} Dominant hazard is {hazard}. "
            f"Hysteresis confirmed after sustained readings. "
            f"OSRM alternate ETA delta is {minutes} min."
        ),
        recommended_action=action,  # type: ignore[arg-type]
        hazard_type=hazard,
        confidence=float(ctx.get("confidence", 0.7)),
        eta_delta_minutes=minutes,
        source="llm_unavailable_template",
    )


def invoke_agent(
    session_id: str,
    scores: list[RiskScore],
    flagged: list[str],
    primary: RouteCandidate | None,
    alternate: RouteCandidate | None,
    demo_mode: str,
    fallback_active: bool,
) -> DriverAlert:
    tools = _tool_payload(
        session_id, scores, flagged, primary, alternate, demo_mode, fallback_active
    )
    eta = 0.0
    if alternate:
        eta = alternate.eta_delta_s
    user = (
        "Tool results (authoritative; do not invent numbers):\n"
        + json.dumps(tools, default=str)[:6000]
        + "\nProduce the driver alert JSON now."
    )
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": DRIVER_ALERT_JSON_SCHEMA,
        "options": {"temperature": 0.1, "num_predict": 256},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    }
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(f"{OLLAMA_URL}/api/chat", json=body)
            r.raise_for_status()
            content = (r.json().get("message") or {}).get("content") or ""
        parsed = json.loads(content)
        alert = DriverAlert.model_validate({**parsed, "source": "llm"})
        return alert
    except Exception:
        return _template_alert(
            tools,
            eta,
            "Local LLM unavailable; using schema-valid template so the driver still sees a warning.",
        )
