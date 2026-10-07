"""
Weather Fetcher agent -- built on OPEN, key-less data (Open-Meteo).

History: this fetcher used to call the India Meteorological Department (IMD)
API portal, which needs an account plus an API key bound to your public IP.
It now uses Open-Meteo (open-source, CC BY 4.0, no registration):

  * Forecast API -> 10 m wind speed & gusts (km/h), 2 m temperature (deg C),
                    WMO weather code, precipitation, visibility
  * Marine API   -> significant wave height (m), swell, wave period,
                    sea-surface temperature

The output contract (WeatherDataBundle) is unchanged, so the risk / correlation
/ evaluation / synthesis agents work exactly as before.

What is observed vs modelled (spelled out so nothing is blurred):
  * Values are numerical-model analysis/forecast output for the grid cell at an
    OFFSHORE reference point per region -- not a buoy or station measurement.
  * wave_height_m is the model's significant wave height (a real model
    output, no longer an estimate derived from a text descriptor). If the marine
    model returns no value for the point, we raise -- we never guess.
  * Besides "now" we scan the next 24 h for peak gusts / waves / thunderstorms
    and raise hazards (with valid_until) so a fisherman leaving later today is
    still warned.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.fetchers.open_meteo_client import OpenMeteoClient
from app.models.schemas import DataSourceStatus, WeatherDataBundle, WeatherHazard

logger = logging.getLogger(__name__)

# Offshore reference points (lat, lon), chosen ~30-60 km off the coast so the
# marine wave model (which is null over land / very near shore) has data.
REGION_POINTS: dict[str, dict] = {
    "kerala coast": {"lat": 9.9, "lon": 75.6, "label": "off Kochi, Arabian Sea"},
    "tamil nadu coast": {"lat": 12.9, "lon": 80.6, "label": "off Chennai, Bay of Bengal"},
    "gujarat coast": {"lat": 22.0, "lon": 68.6, "label": "off Dwarka, Arabian Sea"},
    "andhra coast": {"lat": 17.5, "lon": 83.6, "label": "off Visakhapatnam, Bay of Bengal"},
    "west bengal coast": {"lat": 21.2, "lon": 88.3, "label": "off Sagar Island, Bay of Bengal"},
}
DEFAULT_REGION = "kerala coast"

# WMO weather interpretation codes (used by Open-Meteo)
_THUNDER_CODES = {95}
_SEVERE_THUNDER_CODES = {96, 99}          # thunderstorm with hail
_HEAVY_RAIN_CODES = {65, 82}              # heavy rain / violent rain showers
_FOG_CODES = {45, 48}

HIGH_WIND_KMH = 40.0
SEVERE_WIND_KMH = 55.0
GUST_WARN_KMH = 60.0
ROUGH_SEA_M = 2.5
VERY_ROUGH_SEA_M = 5.0
LOW_VISIBILITY_M = 2000.0


class WeatherFetchError(RuntimeError):
    pass


class WeatherFetcher:
    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._om = OpenMeteoClient(http_client)

    async def fetch(self, region: str) -> WeatherDataBundle:
        region_key = region.lower().strip()
        point = REGION_POINTS.get(region_key, REGION_POINTS[DEFAULT_REGION])

        # Both calls are independent -> concurrent. Any failure raises.
        fc, marine = await asyncio.gather(
            self._om.forecast(point["lat"], point["lon"]),
            self._om.marine(point["lat"], point["lon"]),
        )
        return build_bundle(region, point, fc, marine)

    async def aclose(self):
        await self._om.aclose()


def build_bundle(region: str, point: dict, fc: dict, marine: dict) -> WeatherDataBundle:
    """Pure function: turn raw Open-Meteo payloads into a WeatherDataBundle."""
    cur = fc.get("current") or {}
    mcur = marine.get("current") or {}

    wind = _num(cur.get("wind_speed_10m"))
    temp = _num(cur.get("temperature_2m"))
    wave = _num(mcur.get("wave_height"))
    if wind is None or temp is None:
        raise WeatherFetchError(
            f"Open-Meteo forecast returned no wind/temperature for {region!r} at "
            f"({point['lat']}, {point['lon']}): {cur!r}"
        )
    if wave is None:
        raise WeatherFetchError(
            f"Open-Meteo marine model returned no wave height for {region!r} at "
            f"({point['lat']}, {point['lon']}) -- point may be on land/too near shore. "
            "Adjust REGION_POINTS in app/fetchers/weather_fetcher.py."
        )

    gust = _num(cur.get("wind_gusts_10m"))
    code = _int(cur.get("weather_code"))
    visibility = _num(cur.get("visibility"))
    observed_at = _parse_time(cur.get("time")) or datetime.now(timezone.utc)
    sst = _num(mcur.get("sea_surface_temperature"))

    hazards: list[WeatherHazard] = []
    hazards.extend(_hazards_from_wmo_code(code))

    if wind > HIGH_WIND_KMH:
        hazards.append(WeatherHazard(
            hazard_type="high_wind",
            severity="high" if wind > SEVERE_WIND_KMH else "moderate",
            description=f"Open-Meteo reports winds of {wind:.0f} km/h"
                        + (f" (gusts {gust:.0f} km/h)." if gust is not None else "."),
        ))

    if wave >= ROUGH_SEA_M:
        hazards.append(WeatherHazard(
            hazard_type="rough_sea",
            severity="high" if wave >= VERY_ROUGH_SEA_M else "moderate",
            description=f"Marine model shows significant wave height around {wave:.1f} m.",
        ))

    if visibility is not None and visibility < LOW_VISIBILITY_M:
        hazards.append(WeatherHazard(
            hazard_type="low_visibility",
            severity="high" if visibility < 500 else "moderate",
            description=f"Visibility only about {visibility:.0f} m.",
        ))

    hazards.extend(_forecast_hazards(fc, marine, observed_at))

    notes = _build_notes(region, point, observed_at, wind, gust, temp, wave, sst, code, marine)
    return WeatherDataBundle(
        region=region,
        wind_speed_kmh=round(wind, 1),
        wave_height_m=round(wave, 1),
        temperature_celsius=round(temp, 1),
        hazards=hazards,
        observed_at=observed_at,
        source=DataSourceStatus.LIVE,
        notes=notes,
    )


# --- helpers (deterministic, unit-tested) ---------------------------------
def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def _parse_time(raw) -> datetime | None:
    """Open-Meteo returns 'YYYY-MM-DDTHH:MM' (UTC, since we request timezone=UTC)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hazards_from_wmo_code(code: int | None) -> list[WeatherHazard]:
    if code is None:
        return []
    if code in _SEVERE_THUNDER_CODES:
        return [WeatherHazard(hazard_type="thunderstorm", severity="severe",
                              description=f"WMO weather code {code}: thunderstorm with hail.")]
    if code in _THUNDER_CODES:
        return [WeatherHazard(hazard_type="thunderstorm", severity="high",
                              description=f"WMO weather code {code}: thunderstorm.")]
    if code in _HEAVY_RAIN_CODES:
        return [WeatherHazard(hazard_type="heavy_rain", severity="high",
                              description=f"WMO weather code {code}: heavy rain / violent showers.")]
    if code in _FOG_CODES:
        return [WeatherHazard(hazard_type="low_visibility", severity="moderate",
                              description=f"WMO weather code {code}: fog reducing visibility.")]
    return []


def _next_hours(series: dict, start: datetime, hours: int = 24) -> list[tuple[datetime, dict]]:
    """Rows of an Open-Meteo `hourly` block within [start, start+hours]."""
    times = series.get("time") or []
    end = start + timedelta(hours=hours)
    out = []
    for i, t in enumerate(times):
        dt = _parse_time(t)
        if dt is None or dt < start - timedelta(minutes=59) or dt > end:
            continue
        out.append((dt, {k: (v[i] if i < len(v) else None) for k, v in series.items() if k != "time"}))
    return out


def _forecast_hazards(fc: dict, marine: dict, now: datetime) -> list[WeatherHazard]:
    """Peak gust / wave / thunderstorm over the next 24 h -> hazards with valid_until."""
    out: list[WeatherHazard] = []

    rows = _next_hours(fc.get("hourly") or {}, now)
    gusts = [(dt, _num(r.get("wind_gusts_10m"))) for dt, r in rows]
    gusts = [(dt, g) for dt, g in gusts if g is not None]
    if gusts:
        dt, g = max(gusts, key=lambda x: x[1])
        if g >= GUST_WARN_KMH:
            out.append(WeatherHazard(
                hazard_type="forecast_gusts",
                severity="high" if g >= 75 else "moderate",
                description=f"Gusts up to {g:.0f} km/h forecast within 24 h (peak {dt:%H:%M} UTC).",
                valid_until=dt,
            ))
    storm = [dt for dt, r in rows if _int(r.get("weather_code")) in (_THUNDER_CODES | _SEVERE_THUNDER_CODES)]
    if storm:
        out.append(WeatherHazard(
            hazard_type="forecast_thunderstorm", severity="high",
            description=f"Thunderstorm forecast within 24 h (from {storm[0]:%H:%M} UTC).",
            valid_until=storm[-1],
        ))

    mrows = _next_hours(marine.get("hourly") or {}, now)
    waves = [(dt, _num(r.get("wave_height"))) for dt, r in mrows]
    waves = [(dt, w) for dt, w in waves if w is not None]
    if waves:
        dt, w = max(waves, key=lambda x: x[1])
        if w >= ROUGH_SEA_M:
            out.append(WeatherHazard(
                hazard_type="forecast_rough_sea",
                severity="high" if w >= VERY_ROUGH_SEA_M else "moderate",
                description=f"Waves up to {w:.1f} m forecast within 24 h (peak {dt:%H:%M} UTC).",
                valid_until=dt,
            ))
    return out


def _build_notes(region, point, observed_at, wind, gust, temp, wave, sst, code, marine) -> str:
    parts = [
        "Sources: Open-Meteo Forecast API + Marine API (open data, CC BY 4.0, no API key).",
        f"Reference point {point['lat']}N, {point['lon']}E ({point['label']}); "
        f"model time {observed_at:%Y-%m-%d %H:%M} UTC.",
        f"Wind {wind:.1f} km/h" + (f" (gusts {gust:.1f} km/h)" if gust is not None else "")
        + f", air temperature {temp:.1f} C, WMO weather code {code if code is not None else 'n/a'}.",
        f"Significant wave height {wave:.1f} m from the Open-Meteo marine wave model "
        "(model output for the grid cell, not a buoy measurement).",
    ]
    if sst is not None:
        parts.append(f"Model sea-surface temperature {sst:.1f} C.")
    return " ".join(parts)
