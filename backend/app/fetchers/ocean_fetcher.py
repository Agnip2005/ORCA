"""
Ocean Fetcher agent (plain code, no LLM).

Open data only -- NO registration or API key:
  * SST          NASA JPL MUR / NOAA Coral Reef Watch via NOAA CoastWatch ERDDAP
  * Chlorophyll  VIIRS / MODIS ocean colour via the same ERDDAP server

(This replaces INCOIS ERDDAP + ISRO MOSDAC, which required account setup.)

Dataset IDs come from app/core/erddap_datasets.json as an ordered candidate
list. Each candidate is introspected (axis order, grid spacing, variable) and
tried in order until one returns enough valid pixels -- daily ocean-colour
composites are often cloud-masked (especially in the monsoon), so the chain
falls back to 8-day / monthly composites. If NO candidate works we raise: no
fake data, ever.

The grid is thinned to ~GRID_STEP_DEG so the PFZ engine (pairwise gradients)
stays fast and gradients are computed at a physically meaningful scale.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from app.core.config import settings
from app.fetchers.erddap_client import DatasetInfo, ERDDAPClient, ERDDAPError
from app.fetchers.http_util import make_client
from app.models.schemas import (
    ChlorophyllReading,
    DataSourceStatus,
    GeoPoint,
    OceanDataBundle,
    SSTReading,
)

logger = logging.getLogger(__name__)

_PINNED_DATASETS_PATH = Path(__file__).parent.parent / "core" / "erddap_datasets.json"

GRID_STEP_DEG = 0.25     # ~28 km spacing after thinning
MIN_VALID_POINTS = 6     # fewer valid pixels than this -> try next candidate

# Rough bounding boxes per named region, used to build ERDDAP lat/lon queries.
REGION_BOUNDS = {
    "kerala coast": {"lat_min": 8.0, "lat_max": 12.5, "lon_min": 74.5, "lon_max": 77.5},
    "tamil nadu coast": {"lat_min": 8.0, "lat_max": 13.5, "lon_min": 78.0, "lon_max": 80.5},
    "gujarat coast": {"lat_min": 20.0, "lat_max": 23.5, "lon_min": 68.0, "lon_max": 72.5},
    "andhra coast": {"lat_min": 13.5, "lat_max": 19.0, "lon_min": 80.0, "lon_max": 84.5},
    "west bengal coast": {"lat_min": 21.0, "lat_max": 22.5, "lon_min": 87.5, "lon_max": 89.0},
}
DEFAULT_REGION = "kerala coast"


class ERDDAPDatasetsNotPinnedError(RuntimeError):
    pass


def load_candidates(path: Path = _PINNED_DATASETS_PATH) -> dict[str, list[dict]]:
    if not path.exists():
        raise ERDDAPDatasetsNotPinnedError(f"{path} not found (it ships with the project).")
    cfg = json.loads(path.read_text())
    out = {}
    for key in ("sst", "chlorophyll"):
        cands = (cfg.get(key) or {}).get("candidates") or []
        if not cands:
            raise ERDDAPDatasetsNotPinnedError(f"{path} has no '{key}' candidates.")
        out[key] = cands
    return out


_CANDIDATES = load_candidates()   # fail at import if the file is broken


def to_celsius(values: list[float]) -> list[float]:
    """Some SST products are in Kelvin; convert if the median is obviously > 200."""
    if values and sorted(values)[len(values) // 2] > 200:
        return [v - 273.15 for v in values]
    return values


class OceanFetcher:
    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._client = http_client or make_client(timeout=60.0)
        self._erddap = ERDDAPClient(settings.erddap_base, self._client)
        self._chosen: dict[str, DatasetInfo] = {}

    async def fetch(self, region: str) -> OceanDataBundle:
        bounds = REGION_BOUNDS.get(region.lower().strip(), REGION_BOUNDS[DEFAULT_REGION])

        sst_info, sst_rows = await self._fetch_layer("sst", bounds)
        chl_info, chl_rows = await self._fetch_layer("chlorophyll", bounds)

        sst_vals = to_celsius([r[3] for r in sst_rows])
        sst_readings = [
            SSTReading(location=GeoPoint(lat=r[1], lon=r[2]), sst_celsius=round(v, 3),
                       observed_at=_ts(r[0]), source=DataSourceStatus.LIVE)
            for r, v in zip(sst_rows, sst_vals)
        ]
        chl_readings = [
            ChlorophyllReading(location=GeoPoint(lat=r[1], lon=r[2]), chlorophyll_mg_m3=r[3],
                               observed_at=_ts(r[0]), source=DataSourceStatus.LIVE)
            for r in chl_rows if r[3] >= 0
        ]
        return OceanDataBundle(
            region=region,
            sst_readings=sst_readings,
            chlorophyll_readings=chl_readings,
            source=DataSourceStatus.LIVE,
            notes=self._notes(sst_info, sst_rows, chl_info, chl_rows),
        )

    async def _fetch_layer(self, key: str, bounds: dict):
        """Try candidates in order; return (info, rows) of the first with enough valid pixels."""
        failures = []
        ordered = list(_CANDIDATES[key])
        if key in self._chosen:   # try the previously successful dataset first
            ordered.sort(key=lambda c: c["dataset_id"] != self._chosen[key].dataset_id)
        for cand in ordered:
            try:
                info = await self._erddap.info(cand["dataset_id"], cand["variables"])
                rows = await self._erddap.fetch_grid(info, bounds, GRID_STEP_DEG)
            except (ERDDAPError, RuntimeError) as exc:
                failures.append(f"{cand['dataset_id']}: {exc}")
                logger.warning("ERDDAP candidate %s failed for %s: %s", cand["dataset_id"], key, exc)
                continue
            if len(rows) < MIN_VALID_POINTS:
                failures.append(f"{cand['dataset_id']}: only {len(rows)} valid pixel(s) (cloud-masked/empty)")
                continue
            self._chosen[key] = info
            return info, rows
        raise ERDDAPError(
            f"No open ERDDAP dataset returned usable {key} data for bounds {bounds}. Tried: "
            + " | ".join(failures)
        )

    @staticmethod
    def _notes(sst_info: DatasetInfo, sst_rows, chl_info: DatasetInfo, chl_rows) -> str:
        def desc(i: DatasetInfo, rows) -> str:
            return (f"{i.dataset_id} ('{i.title or i.variable}'"
                    + (f", {i.institution}" if i.institution else "")
                    + f"; {len(rows)} valid pixels, slice {rows[0][0]})")
        return (
            "Sources: NOAA CoastWatch ERDDAP (open data, no API key). "
            f"SST from {desc(sst_info, sst_rows)}. "
            f"Chlorophyll-a from {desc(chl_info, chl_rows)}. "
            f"Grid thinned to ~{GRID_STEP_DEG} deg. Satellite products can be cloud-masked; "
            "the fetcher falls back to longer composites when daily coverage is poor."
        )

    async def aclose(self):
        await self._client.aclose()


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
