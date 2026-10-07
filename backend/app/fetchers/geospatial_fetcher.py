"""
Geospatial Fetcher agent.

Provides static reference geometry (coastline points, named-region
centroids) for map rendering and as context for other agents. This is
genuinely static data -- India's coastline doesn't move -- so there's no
"mock vs live" distinction here the way there is for SST or weather.

Plain code, no LLM, per the architecture's data-procurement rule.
"""
from __future__ import annotations

from app.fetchers.ocean_fetcher import REGION_BOUNDS
from app.models.schemas import GeoPoint


class GeospatialFetcher:
    async def fetch(self, region: str) -> dict:
        region_key = region.lower().strip()
        bounds = REGION_BOUNDS.get(region_key)

        if bounds is None:
            return {
                "region": region,
                "known_region": False,
                "bounds": None,
                "centroid": None,
            }

        centroid = GeoPoint(
            lat=(bounds["lat_min"] + bounds["lat_max"]) / 2,
            lon=(bounds["lon_min"] + bounds["lon_max"]) / 2,
        )
        return {
            "region": region,
            "known_region": True,
            "bounds": bounds,
            "centroid": centroid.model_dump(),
        }
