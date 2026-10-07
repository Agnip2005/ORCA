"""
Compact data bundles before they go into an LLM prompt.

The satellite grids are ~200 pixels each and every PFZ advisory carries a map
polygon. Dumping all of that into the Correlation prompt costs ~9k tokens and
overflows small-TPM providers (e.g. Groq free tier: 8000 tokens/min -> HTTP 413).
The LLM only needs the *shape* of the data, so we send summary statistics plus a
small representative sample. The full-resolution data still feeds the PFZ engine
and the map layers -- only the prompt copy is compacted. The agents themselves
are unchanged.
"""
from __future__ import annotations

from statistics import mean

from app.models.schemas import OceanDataBundle, PFZDataBundle, PFZQuality

MAX_SAMPLES = 8
MAX_ADVISORIES = 6
_QUALITY_RANK = {PFZQuality.HIGH: 0, PFZQuality.MODERATE: 1, PFZQuality.LOW: 2}


def _sample_by_value(readings: list, value_attr: str, n: int = MAX_SAMPLES) -> list:
    """n readings evenly spread over the value range (always includes min and max)."""
    if len(readings) <= n:
        return list(readings)
    ordered = sorted(readings, key=lambda r: getattr(r, value_attr))
    idx = sorted({round(i * (len(ordered) - 1) / (n - 1)) for i in range(n)})
    return [ordered[i] for i in idx]


def _stats(label: str, readings: list, value_attr: str, unit: str) -> str:
    if not readings:
        return f"{label}: no valid pixels."
    vals = [getattr(r, value_attr) for r in readings]
    return (f"{label}: {len(vals)} pixels, min {min(vals):.2f}, mean {mean(vals):.2f}, "
            f"max {max(vals):.2f} {unit}.")


def compact_ocean(ocean: OceanDataBundle | None) -> OceanDataBundle | None:
    if ocean is None:
        return None
    summary = (_stats("SST", ocean.sst_readings, "sst_celsius", "C") + " "
               + _stats("Chlorophyll", ocean.chlorophyll_readings, "chlorophyll_mg_m3", "mg/m3")
               + f" Showing {MAX_SAMPLES} representative pixels of each (spread min..max).")
    return ocean.model_copy(update={
        "sst_readings": _sample_by_value(ocean.sst_readings, "sst_celsius"),
        "chlorophyll_readings": _sample_by_value(ocean.chlorophyll_readings, "chlorophyll_mg_m3"),
        "notes": summary,   # full provenance stays in the API response (map_layers.ocean_notes)
    })


def compact_pfz(pfz: PFZDataBundle | None) -> PFZDataBundle | None:
    if pfz is None:
        return None
    best = sorted(pfz.advisories, key=lambda a: _QUALITY_RANK[a.quality])[:MAX_ADVISORIES]
    slim = [a.model_copy(update={"zone_polygon": None}) for a in best]
    return pfz.model_copy(update={"advisories": slim})
