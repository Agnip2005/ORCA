"""
Open-Meteo client (https://open-meteo.com) -- free, open-source weather API.

NO API KEY, NO REGISTRATION. Replaces the India Meteorological Department
portal (api.imd.gov.in), which required an account and an IP-bound key.

Two endpoints are used:
  * Forecast API  https://api.open-meteo.com/v1/forecast
        current + hourly: 10 m wind speed/gusts, 2 m temperature,
        WMO weather code, precipitation, visibility
  * Marine API    https://marine-api.open-meteo.com/v1/marine
        current + hourly: significant wave height, wave period, wind-wave and
        swell height, sea-surface temperature (wave models: MFWAM / ERA5-Ocean
        / GFS-Wave -- model output, not a buoy measurement)

Data is licensed CC BY 4.0; free for non-commercial use (<10k calls/day).
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.fetchers.http_util import get_json, make_client

FORECAST_CURRENT = [
    "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
    "weather_code", "precipitation", "visibility",
]
FORECAST_HOURLY = ["wind_gusts_10m", "wind_speed_10m", "weather_code", "precipitation"]
MARINE_CURRENT = [
    "wave_height", "wave_direction", "wave_period",
    "wind_wave_height", "swell_wave_height", "sea_surface_temperature",
]
MARINE_HOURLY = ["wave_height", "swell_wave_height"]


class OpenMeteoClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._client = http_client or make_client()
        self._forecast_url = f"{settings.open_meteo_base.rstrip('/')}/v1/forecast"
        self._marine_url = f"{settings.open_meteo_marine_base.rstrip('/')}/v1/marine"

    async def forecast(self, lat: float, lon: float, forecast_hours: int = 24) -> dict:
        params = {
            "latitude": lat, "longitude": lon,
            "current": ",".join(FORECAST_CURRENT),
            "hourly": ",".join(FORECAST_HOURLY),
            "wind_speed_unit": "kmh", "timezone": "UTC",
            "forecast_hours": forecast_hours,
        }
        return await get_json(self._client, self._forecast_url, params, what="Open-Meteo forecast API")

    async def marine(self, lat: float, lon: float, forecast_hours: int = 24) -> dict:
        params = {
            "latitude": lat, "longitude": lon,
            "current": ",".join(MARINE_CURRENT),
            "hourly": ",".join(MARINE_HOURLY),
            "timezone": "UTC", "forecast_hours": forecast_hours,
        }
        return await get_json(self._client, self._marine_url, params, what="Open-Meteo marine API")

    async def aclose(self):
        await self._client.aclose()
