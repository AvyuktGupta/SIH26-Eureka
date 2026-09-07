"""L6 gated LLM agent.

Invoked only after L4 confirms a real state transition. Three tools only.
Output is schema-constrained JSON — the UI never parses free text.

Ollama may be started after the backend. We probe live on each invoke and
retry later ticks if the first attempt had to use the template fallback.
"""

from __future__ import annotations

import json
import logging
import re
import time
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

log = logging.getLogger("apcs.l6")

SYSTEM_PROMPT = """You are the APCS driver-alert agent for a Himachal Pradesh hill-road demo.
You do not compute routes, risk scores, or collision-avoidance manoeuvres.
You only explain the already-computed fused risk and the OSRM candidate routes.
Return JSON matching the schema. Keep headline ≤ 80 characters. Be calm and specific.
"""

_PREFERRED_MODELS = (
    OLLAMA_MODEL,
    "llama3.2:3b",
    "llama3.2",
    "llama3.1:8b",
    "llama3.1",
    "llama3:8b",
    "llama3",
    "qwen2.5:3b",
    "qwen2.5:1.5b",
    "phi3:mini",
    "phi3",
    "mistral",
    "gemma2:2b",
)

_probe_cache: tuple[float, dict[str, Any]] | None = None


def _base() -> str:
    return OLLAMA_URL.rstrip("/")


def probe_ollama(force: bool = False) -> dict[str, Any]:
    global _probe_cache
    now = time.time()
    if not force and _probe_cache and now - _probe_cache[0] < 5:
        return _probe_cache[1]
    try:
        with httpx.Client(timeout=1.2) as client:
            r = client.get(f"{_base()}/api/tags")
            r.raise_for_status()
            names = [m.get("name") for m in (r.json().get("models") or []) if m.get("name")]
        model = _pick_model(names)
        result = {
            "reachable": True,
            "url": _base(),
            "models": names,
            "selected_model": model,
        }
    except Exception as exc:
        result = {
            "reachable": False,
            "url": _base(),
            "models": [],
            "selected_model": None,
            "error": str(exc),
        }
    _probe_cache = (now, result)
    return result


def _pick_model(available: list[str]) -> str | None:
    if not available:
        return None
    lower = {n.lower(): n for n in available}
    for want in _PREFERRED_MODELS:
        if want.lower() in lower:
            return lower[want.lower()]
        stem = want.split(":")[0].lower()
        for n in available:
            if n.lower().startswith(stem):
                return n
    return available[0]


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


def _extract_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("LLM returned no JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON was not an object")
    return parsed


def _coerce_alert(parsed: dict[str, Any]) -> DriverAlert:
    action = str(parsed.get("recommended_action") or "reroute").lower()
    if action not in {"continue", "reroute", "stop"}:
        action = "reroute"
    level = str(parsed.get("alert_level") or "warning").lower()
    if level not in {"info", "warning", "critical"}:
        level = "warning"
    try:
        conf = float(parsed.get("confidence") or 0.7)
    except (TypeError, ValueError):
        conf = 0.7
    try:
        eta = float(parsed.get("eta_delta_minutes") or 0.0)
    except (TypeError, ValueError):
        eta = 0.0
    return DriverAlert(
        alert_level=level,  # type: ignore[arg-type]
        headline=str(parsed.get("headline") or "Hazard confirmed on corridor")[:120],
        explanation=str(parsed.get("explanation") or "Fused risk crossed threshold."),
        recommended_action=action,  # type: ignore[arg-type]
        hazard_type=str(parsed.get("hazard_type") or "landslide"),
        confidence=max(0.0, min(1.0, conf)),
        eta_delta_minutes=eta,
        source="llm",
    )


def _chat(client: httpx.Client, model: str, messages: list[dict], fmt: Any) -> str:
    body = {
        "model": model,
        "stream": False,
        "format": fmt,
        "options": {"temperature": 0.1, "num_predict": 320},
        "messages": messages,
    }
    r = client.post(f"{_base()}/api/chat", json=body, timeout=60.0)
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content") or ""


def invoke_agent(
    session_id: str,
    scores: list[RiskScore],
    flagged: list[str],
    primary: RouteCandidate | None,
    alternate: RouteCandidate | None,
    demo_mode: str,
    fallback_active: bool,
) -> tuple[DriverAlert, str | None]:
    tools = _tool_payload(
        session_id, scores, flagged, primary, alternate, demo_mode, fallback_active
    )
    eta = alternate.eta_delta_s if alternate else 0.0
    user = (
        "Tool results (authoritative; do not invent numbers):\n"
        + json.dumps(tools, default=str)[:6000]
        + "\nProduce the driver alert JSON now."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

    try:
        status = probe_ollama(force=True)
        if not status["reachable"]:
            raise RuntimeError(status.get("error") or f"Ollama not reachable at {_base()}")
        model = status["selected_model"]
        if not model:
            raise RuntimeError(
                f"Ollama is up but has no models. Run: ollama pull {OLLAMA_MODEL}"
            )
        with httpx.Client(timeout=60.0) as client:
            try:
                content = _chat(client, model, messages, DRIVER_ALERT_JSON_SCHEMA)
            except Exception as schema_exc:
                log.info("schema format failed (%s); retrying format=json", schema_exc)
                content = _chat(client, model, messages, "json")
        alert = _coerce_alert(_extract_json(content))
        return alert, None
    except Exception as exc:
        log.warning("L6 Ollama invoke failed: %s", exc)
        return (
            _template_alert(
                tools,
                eta,
                "Local LLM unavailable; using schema-valid template so the driver still sees a warning.",
            ),
            str(exc),
        )
