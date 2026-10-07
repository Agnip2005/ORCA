"""
FastAPI app - the "Conversation Layer" from the architecture diagram's
perspective of the world. Desktop and (later) mobile UIs both talk to this.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.pipeline import pipeline
from app.fetchers.ocean_fetcher import REGION_BOUNDS
from app.models.schemas import PipelineResponse

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await pipeline.aclose()


app = FastAPI(
    title="Marine Intelligence Platform",
    description="SIH26176 - Multi-agent marine advisory system for Indian fishermen",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    query: str


@app.get("/health")
async def health():
    """
    Reports whether required API keys are configured -- not whether a mode
    is 'live' or 'mock', since there's no mock mode anymore. Missing a key
    means the server will fail on first use of that agent/fetcher, not that
    it's silently degrading.
    """
    from app.core.config import settings
    return {
        "status": "ok",
        "openai_configured": bool(settings.openai_api_key),
        "weather_source": "Open-Meteo forecast + marine APIs (open, no key)",
        "ocean_source": f"ERDDAP {settings.erddap_base} (open, no key)",
        "erddap_datasets_pinned": _erddap_datasets_pinned(),
        "eez_boundary": "real" if _real_eez_present() else "placeholder (run scripts/fetch_real_eez.py)",
        "coastline": (
            "real"
            if (Path(__file__).parent / "core" / "india_coastline.geojson").exists()
            else "missing (run scripts/fetch_real_coastline.py -- UI falls back to labeled silhouette)"
        ),
        "pfz": "derived from open satellite SST/chlorophyll (no public official PFZ API exists)",
    }


def _erddap_datasets_pinned() -> bool:
    from pathlib import Path
    return (Path(__file__).parent / "core" / "erddap_datasets.json").exists()


def _real_eez_present() -> bool:
    from pathlib import Path
    return (Path(__file__).parent / "core" / "india_eez_real.geojson").exists()


@app.post("/api/query", response_model=PipelineResponse)
async def query(request: QueryRequest):
    return await pipeline.run(request.query)


@app.get("/api/geo")
async def geo():
    """
    Geometry layers for the frontend map, each labeled with its provenance
    so a placeholder can never masquerade as survey-grade geometry:

      * coastline -- real India outline from app/core/india_coastline.geojson
        (one-time scripts/fetch_real_coastline.py); when absent, the UI is
        told to use its labeled approximate silhouette instead.
      * eez       -- the exact polygon the deterministic boundary guardrail
        checks against: the real Marine Regions EEZ when fetched, otherwise
        the bundled placeholder (flagged is_placeholder=True).
      * regions   -- approximate bounding boxes of the five coastal regions
        (display extents, not legal boundaries).
    """
    return {
        "coastline": _coastline_layer(),
        "eez": _eez_layer(),
        "regions": REGION_BOUNDS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _read_features(path):
    """Load a GeoJSON file -> (geometries list, raw top-level dict)."""
    if not path.exists():
        return [], {}
    data = json.loads(path.read_text())
    features = data.get("features", [])
    geometries = [
        f["geometry"]
        for f in features
        if isinstance(f, dict) and isinstance(f.get("geometry"), dict)
    ]
    if data.get("type") == "Feature" and isinstance(data.get("geometry"), dict):
        geometries.append(data["geometry"])
    return geometries, data


def _coastline_layer() -> dict:
    path = Path(__file__).parent / "core" / "india_coastline.geojson"
    if not path.exists():
        return {
            "geometries": [],
            "source": None,
            "license": None,
            "label": "approximate silhouette (no real coastline fetched yet)",
            "setup_hint": "python scripts/fetch_real_coastline.py",
        }
    geometries, data = _read_features(path)
    source = data.get("source") or "unknown source"
    return {
        "geometries": geometries,
        "source": source,
        "license": data.get("license"),
        "label": f"real coastline -- {source}",
        "setup_hint": None,
    }


def _eez_layer() -> dict:
    core = Path(__file__).parent / "core"
    real_path = core / "india_eez_real.geojson"
    is_placeholder = not real_path.exists()
    path = real_path if not is_placeholder else core / "india_eez_simplified.geojson"
    geometries, data = _read_features(path)
    if not geometries:
        return {
            "geometries": [],
            "source": None,
            "is_placeholder": True,
            "disclaimer": "no EEZ geometry found on disk",
        }
    return {
        "geometries": geometries,
        "source": (
            "Marine Regions (VLIZ) MRGID 8480 -- real India EEZ"
            if not is_placeholder
            else "placeholder -- buffered region boxes, not a real EEZ"
        ),
        "is_placeholder": is_placeholder,
        "disclaimer": data.get("_disclaimer"),
    }


