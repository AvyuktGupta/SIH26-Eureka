"""L1 weather poller — Open-Meteo, no API key.

Cached aggressively. Never called on-demand per map pan; the runtime tick
reads the cache, and a scheduled poller refreshes it.
"""

from __future__ import annotations

import time

import httpx

from app import db
from app.config import OPEN_METEO_URL, WEATHER_CACHE_TTL_S
from app.contracts.schemas import WeatherSample


class WeatherFeedDown(RuntimeError):
    pass


def _cache_key(lat: float, lon: float) -> str:
    return f"weather:{round(lat, 3)}:{round(lon, 3)}"


def fetch_weather(lat: float, lon: float, *, kill: bool = False) -> WeatherSample:
    key = _cache_key(lat, lon)
    if kill:
        last, _fetched_at = db.cache_get_last(key)
        if last:
            sample = WeatherSample(**last)
            sample.stale = True
            sample.source = "open-meteo:last-known"
            return sample
        return WeatherSample(
            lat=lat,
            lon=lon,
            source="weather-killed:terrain-prior-only",
            stale=True,
        )

    cached = db.cache_get(key, WEATHER_CACHE_TTL_S)
    if cached:
        return WeatherSample(**cached)

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation,temperature_2m,wind_speed_10m,snowfall,rain",
        "forecast_days": 1,
        "timezone": "Asia/Kolkata",
    }
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(OPEN_METEO_URL, params=params)
            r.raise_for_status()
            hourly = r.json().get("hourly", {})
    except Exception as exc:
        last, _ = db.cache_get_last(key)
        if last:
            sample = WeatherSample(**last)
            sample.stale = True
            sample.source = "open-meteo:last-known"
            return sample
        raise WeatherFeedDown(str(exc)) from exc

    precip = hourly.get("precipitation") or [0.0]
    rain = hourly.get("rain") or precip
    temp = hourly.get("temperature_2m") or [12.0]
    wind = hourly.get("wind_speed_10m") or [1.0]
    snow = hourly.get("snowfall") or [0.0]

    now_mm = float(precip[0] or 0.0)
    rain_24 = float(sum((x or 0.0) for x in rain[:24]))
    sample = WeatherSample(
        lat=lat,
        lon=lon,
        precipitation_mm=now_mm,
        precip_24h_mm=rain_24,
        temperature_c=float(temp[0] or 12.0),
        wind_ms=float(wind[0] or 0.0) / 3.6,  # Open-Meteo default is km/h
        snowfall_cm=float(snow[0] or 0.0),
        source="open-meteo",
        fetched_at=int(time.time()),
        stale=False,
    )
    db.cache_put(key, "open-meteo", sample.model_dump())
    return sample


def apply_demo_weather(
    sample: WeatherSample,
    demo_mode: str,
    extra_precip_mm: float = 0.0,
) -> WeatherSample:
    """Demo overlays. Does not pretend to be live API data."""
    out = sample.model_copy()
    if extra_precip_mm:
        out.precipitation_mm = extra_precip_mm
        out.precip_24h_mm = max(out.precip_24h_mm, extra_precip_mm * 6)
        out.source = f"{out.source}+demo_overlay"
    if demo_mode == "hazard":
        out.precipitation_mm = max(out.precipitation_mm, 28.0)
        out.precip_24h_mm = max(out.precip_24h_mm, 90.0)
        out.source = f"{out.source}+demo_hazard"
    if demo_mode == "avalanche":
        out.snowfall_cm = max(out.snowfall_cm, 18.0)
        out.temperature_c = min(out.temperature_c, 1.5)
        out.source = f"{out.source}+demo_avalanche"
    return out
