"""
Shared data contracts.

These schemas are the "standardised data access" layer from the architecture
diagram (the MCP Tools box). Every fetcher returns data shaped like this,
so downstream agents don't need to know which specific API a value came
from -- only whether it's a direct live measurement (LIVE) or a real
computation derived from live data but not itself an official feed
(DERIVED, e.g. PFZ candidate zones -- see app/fetchers/pfz_derived_engine.py).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DataSourceStatus(str, Enum):
    LIVE = "live"        # fetched directly from a real external API this request
    DERIVED = "derived"  # computed from real underlying data, not itself an official feed (e.g. PFZ)


class GeoPoint(BaseModel):
    lat: float
    lon: float


class SSTReading(BaseModel):
    location: GeoPoint
    sst_celsius: float
    observed_at: datetime
    source: DataSourceStatus


class ChlorophyllReading(BaseModel):
    location: GeoPoint
    chlorophyll_mg_m3: float
    observed_at: datetime
    source: DataSourceStatus


class OceanDataBundle(BaseModel):
    """Result of the Ocean Fetcher agent."""
    region: str
    sst_readings: list[SSTReading]
    chlorophyll_readings: list[ChlorophyllReading]
    source: DataSourceStatus
    notes: Optional[str] = None


class WeatherHazard(BaseModel):
    hazard_type: str  # e.g. "high_wind", "cyclone_watch", "rough_sea"
    severity: str      # "low" | "moderate" | "high" | "severe"
    description: str
    valid_until: Optional[datetime] = None


class WeatherDataBundle(BaseModel):
    """Result of the Weather Fetcher agent -- values from the open Open-Meteo
    forecast + marine APIs (no key); `notes` records exact provenance (which
    endpoint / reference point produced each value and that they are model
    output). See app/fetchers/weather_fetcher.py."""
    region: str
    wind_speed_kmh: float
    wave_height_m: float
    temperature_celsius: float
    hazards: list[WeatherHazard]
    observed_at: datetime
    source: DataSourceStatus
    notes: Optional[str] = None


class PFZQuality(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class PFZAdvisory(BaseModel):
    location: GeoPoint
    distance_from_coast_km: float
    quality: PFZQuality
    valid_date: datetime
    notes: Optional[str] = None
    # Non-uniform zone extent for mapping: a corridor polygon around the
    # SST front that produced this candidate (the segment between the two
    # ERDDAP grid cells, +/-5 km perpendicular -- see
    # app/fetchers/pfz_derived_engine.py::_front_zone_polygon). Every zone
    # therefore has its own orientation/length/shape -- never a uniform box.
    # Illustrative extent derived from front geometry, NOT an official
    # INCOIS zone boundary.
    zone_polygon: Optional[list[GeoPoint]] = None


class PFZDataBundle(BaseModel):
    """Result of the PFZ fetcher -- derived from real SST/chlorophyll data,
    not an official INCOIS feed (no public API for that exists). See
    app/fetchers/pfz_derived_engine.py for methodology."""
    region: str
    advisories: list[PFZAdvisory]
    source: DataSourceStatus
    disclaimer: str = (
        "PFZ advisory positions are illustrative. INCOIS does not expose a "
        "public API for PFZ advisories; these are derived from open satellite "
        "data and are NOT the official INCOIS advisory."
    )


class BoundaryCheckResult(BaseModel):
    """Result of the deterministic Maritime Boundary Guardrail."""
    is_safe: bool
    distance_to_boundary_km: Optional[float] = None
    boundary_type: Optional[str] = None  # e.g. "IMBL", "EEZ"
    message: str


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskAssessment(BaseModel):
    risk_level: RiskLevel
    factors: list[str]
    explanation: str


class AgentTrace(BaseModel):
    """One step of the pipeline, kept for judge-facing transparency / debugging."""
    agent_name: str
    started_at: datetime
    finished_at: datetime
    summary: str
    data_source: Optional[DataSourceStatus] = None


class PipelineResponse(BaseModel):
    query: str
    chat_response: str
    map_layers: dict = Field(default_factory=dict)
    risk_assessment: Optional[RiskAssessment] = None
    boundary_check: Optional[BoundaryCheckResult] = None
    safety_alert: bool = False
    trace: list[AgentTrace] = Field(default_factory=list)
    regeneration_count: int = 0
