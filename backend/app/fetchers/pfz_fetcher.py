"""
PFZ (Potential Fishing Zone) fetcher.

INCOIS has no public REST/JSON API for official PFZ advisories -- they're
portal/PDF/social-media only. Instead of faking an advisory feed, this
fetcher DERIVES PFZ-like candidate zones from real SST and chlorophyll data
(fetched from open NOAA CoastWatch ERDDAP via OceanFetcher) using the same
documented physical principle INCOIS's own published methodology relies on:
SST gradient strength ("fronts") + chlorophyll concentration. See
app/fetchers/pfz_derived_engine.py for the full methodology and citations.

This is explicitly NOT the official INCOIS advisory -- every bundle
produced here carries a disclaimer saying so.

No mock fallback: this requires real ocean_data (source=LIVE) as input.
Since OceanFetcher itself now raises on any failure rather than degrading,
ocean_data passed in here is always genuinely live -- if it weren't, that
would already have surfaced as an exception upstream, before this fetcher
is ever called.

An empty result (no advisories) is a legitimate, non-error outcome: it
means the ocean was uniform/calm with no strong fronts, not that anything
failed.
"""
from __future__ import annotations

import logging

from app.fetchers.ocean_fetcher import DEFAULT_REGION, REGION_BOUNDS
from app.fetchers.pfz_derived_engine import DerivedPFZEngine
from app.models.schemas import DataSourceStatus, GeoPoint, OceanDataBundle, PFZDataBundle

logger = logging.getLogger(__name__)


class PFZFetcher:
    def __init__(self):
        self._engine = DerivedPFZEngine()

    async def fetch(self, region: str, ocean_data: OceanDataBundle) -> PFZDataBundle:
        if ocean_data.source != DataSourceStatus.LIVE:
            # Should be unreachable now that OceanFetcher has no mock/fallback
            # path -- if this ever fires, something upstream changed in a way
            # that broke the live-only invariant this fetcher depends on.
            raise RuntimeError(
                f"PFZFetcher received non-live ocean_data (source={ocean_data.source!r}) "
                f"for region {region!r}. This should be unreachable -- OceanFetcher no "
                "longer has a mock/fallback path."
            )

        region_bounds = REGION_BOUNDS.get(region.lower().strip(), REGION_BOUNDS[DEFAULT_REGION])
        coastline_ref = GeoPoint(
            lat=(region_bounds["lat_min"] + region_bounds["lat_max"]) / 2,
            lon=region_bounds["lon_min"],  # rough coastline-side reference point
        )
        bundle = self._engine.compute(
            region=region,
            sst_readings=ocean_data.sst_readings,
            chl_readings=ocean_data.chlorophyll_readings,
            coastline_ref=coastline_ref,
        )

        if bundle.advisories:
            logger.info("Derived %d PFZ candidate(s) from real ocean data for %s", len(bundle.advisories), region)
        else:
            logger.info("Derived PFZ engine found no qualifying fronts for %s; ocean was calm/uniform.", region)

        return bundle
