"""
Derived PFZ (Potential Fishing Zone) Scoring Engine.

IMPORTANT — READ BEFORE CLAIMING THIS IS "REAL PFZ DATA":

This is NOT the official INCOIS PFZ advisory. INCOIS's actual PFZ product
uses proprietary processing (per published methodology: SST front/gradient
detection via edge-detection algorithms like Canny, combined with
chlorophyll fronts, ADDITIONALLY informed by altimetry, wind/current data,
and in-situ validation from Fishery Survey of India vessels) that is not
publicly documented in full or replicable from public data alone.

What this module DOES do, honestly: it takes REAL SST and chlorophyll
readings (from open NOAA CoastWatch ERDDAP satellite products) and applies the
same *documented physical principle* the published literature describes --
strong SST gradients (thermal fronts) combined with elevated chlorophyll
indicate productive zones where pelagic fish aggregate. This is a real,
citable, peer-reviewed basis (see references below), computed with
deterministic code over real data -- not a fabricated advisory feed.

References informing the scoring logic:
- Solanki et al. and subsequent INCOIS methodology docs: PFZ = f(SST
  gradient strength, chlorophyll concentration)
- Multiple validation studies (e.g. ScienceDirect 2025, Turk. J. Fish.
  Aquat. Sci.) confirming CPUE (catch per unit effort) is higher in
  zones with strong SST/chlorophyll fronts than outside them
- MOSDAC (ISRO) PFZ methodology note: chlorophyll fronts via Canny
  edge-detection algorithm, combined with SST fronts

Label every output of this module clearly as "derived" / "heuristic",
never as an official INCOIS advisory, in both API responses and any UI
that surfaces it.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from itertools import combinations

from app.models.schemas import (
    ChlorophyllReading,
    DataSourceStatus,
    GeoPoint,
    PFZAdvisory,
    PFZDataBundle,
    PFZQuality,
    SSTReading,
)

# Thresholds are illustrative, calibrated loosely from the published
# literature's qualitative description ("high SST gradient", "high
# chlorophyll") rather than INCOIS's undisclosed exact numeric cutoffs.
# Tune these against real fetched data once ERDDAP data is flowing -- print
# actual gradient/chlorophyll distributions from a real fetch and adjust.
SST_GRADIENT_HIGH_C_PER_KM = 0.05   # °C per km -- a "strong" thermal front
SST_GRADIENT_MODERATE_C_PER_KM = 0.02
CHLOROPHYLL_HIGH_MG_M3 = 0.5
CHLOROPHYLL_MODERATE_MG_M3 = 0.25

# Half-width (km) of the mapped zone extent around a detected front: each
# candidate zone is drawn as a corridor of +/- this many km perpendicular to
# the front segment. Illustrative display extent only -- not an official
# INCOIS zone boundary (see _front_zone_polygon).
ZONE_HALF_WIDTH_KM = 5.0

EARTH_RADIUS_KM = 6371.0


def _haversine_km(p1: GeoPoint, p2: GeoPoint) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [p1.lat, p1.lon, p2.lat, p2.lon])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _nearest_chlorophyll(point: GeoPoint, chl_readings: list[ChlorophyllReading]) -> ChlorophyllReading | None:
    if not chl_readings:
        return None
    return min(chl_readings, key=lambda r: _haversine_km(point, r.location))


def compute_sst_gradients(sst_readings: list[SSTReading]) -> list[dict]:
    """
    Compute SST gradient magnitude between each pair of nearby grid points,
    as a proxy for thermal front strength. This is a simplified stand-in
    for proper edge-detection (e.g. Canny, as INCOIS's documented
    methodology uses) -- adequate for a coarse ERDDAP grid subset, not a
    substitute for real front-detection algorithms on full-resolution
    satellite imagery.

    Returns a list of {midpoint, gradient_c_per_km} between neighboring
    reading pairs within a reasonable distance of each other (skips pairs
    too far apart to represent a local front).
    """
    MAX_PAIR_DISTANCE_KM = 50.0
    gradients = []

    for r1, r2 in combinations(sst_readings, 2):
        dist_km = _haversine_km(r1.location, r2.location)
        if dist_km < 0.5 or dist_km > MAX_PAIR_DISTANCE_KM:
            continue  # too close (noise) or too far (not a local front)

        temp_diff = abs(r1.sst_celsius - r2.sst_celsius)
        gradient = temp_diff / dist_km

        midpoint = GeoPoint(
            lat=(r1.location.lat + r2.location.lat) / 2,
            lon=(r1.location.lon + r2.location.lon) / 2,
        )
        gradients.append(
            {
                "midpoint": midpoint,
                "gradient_c_per_km": gradient,
                # Endpoints of the segment that produced this gradient --
                # used to give each candidate zone a non-uniform mapped
                # extent (see _front_zone_polygon).
                "p1": r1.location,
                "p2": r2.location,
            }
        )

    return gradients


def _front_zone_polygon(
    p1: GeoPoint, p2: GeoPoint, half_width_km: float = ZONE_HALF_WIDTH_KM
) -> list[GeoPoint]:
    """
    Non-uniform mapped extent for a candidate zone: the corridor of
    +/-half_width_km around the SST-front segment p1->p2 (the pair of ERDDAP
    grid cells whose temperature difference produced the gradient). Because
    each front has its own orientation, length and position, every zone
    polygon comes out a different shape -- zones are never uniform boxes.

    Deterministic and computed in a local km-space approximation
    (degrees -> km via 111 km/deg, cos(lat) for longitude), which is ample
    for display purposes. Returns [] for a degenerate (zero-length)
    segment. This is an illustrative extent, NOT an official INCOIS zone
    boundary.
    """
    lat_ref = math.radians((p1.lat + p2.lat) / 2.0)
    km_per_deg_lat = 111.0
    km_per_deg_lon = max(1e-6, 111.0 * math.cos(lat_ref))

    dx_km = (p2.lon - p1.lon) * km_per_deg_lon
    dy_km = (p2.lat - p1.lat) * km_per_deg_lat
    length_km = math.hypot(dx_km, dy_km)
    if length_km < 1e-9:
        return []

    # Unit perpendicular to the segment, in km-space.
    nx, ny = -dy_km / length_km, dx_km / length_km
    off_lon = (nx * half_width_km) / km_per_deg_lon
    off_lat = (ny * half_width_km) / km_per_deg_lat

    def corner(base: GeoPoint, sign: float) -> GeoPoint:
        return GeoPoint(
            lat=round(base.lat + sign * off_lat, 4),
            lon=round(base.lon + sign * off_lon, 4),
        )

    # Trace the corridor: one side p1->p2, then back along the other side.
    return [corner(p1, 1.0), corner(p2, 1.0), corner(p2, -1.0), corner(p1, -1.0)]


def _classify_quality(gradient_c_per_km: float, chlorophyll_mg_m3: float | None) -> PFZQuality | None:
    """
    Deterministic scoring, no LLM -- consistent with the rest of this app's
    fetcher/agent split. Returns None if conditions don't meet the minimum
    bar for even a low-quality zone (i.e. most of the ocean, most of the
    time, correctly is NOT flagged as a PFZ candidate).
    """
    chl = chlorophyll_mg_m3 if chlorophyll_mg_m3 is not None else 0.0

    strong_front = gradient_c_per_km >= SST_GRADIENT_HIGH_C_PER_KM
    moderate_front = gradient_c_per_km >= SST_GRADIENT_MODERATE_C_PER_KM
    high_chl = chl >= CHLOROPHYLL_HIGH_MG_M3
    moderate_chl = chl >= CHLOROPHYLL_MODERATE_MG_M3

    if strong_front and high_chl:
        return PFZQuality.HIGH
    if (strong_front and moderate_chl) or (moderate_front and high_chl):
        return PFZQuality.MODERATE
    if moderate_front and moderate_chl:
        return PFZQuality.LOW
    return None  # doesn't meet the bar -- not flagged as a candidate zone


class DerivedPFZEngine:
    """
    Computes PFZ-like candidate zones from real SST + chlorophyll data.
    Deterministic, no LLM -- same fetcher-layer philosophy as the rest of
    the pipeline's data procurement.
    """

    def compute(
        self,
        region: str,
        sst_readings: list[SSTReading],
        chl_readings: list[ChlorophyllReading],
        coastline_ref: GeoPoint | None = None,
    ) -> PFZDataBundle:
        if len(sst_readings) < 2:
            return self._empty_bundle(
                region, reason="insufficient SST readings to compute gradients (need >= 2 grid points)"
            )

        gradients = compute_sst_gradients(sst_readings)
        if not gradients:
            return self._empty_bundle(
                region, reason="no SST reading pairs within local-front distance range"
            )

        advisories = []
        for g in gradients:
            nearest_chl = _nearest_chlorophyll(g["midpoint"], chl_readings)
            chl_value = nearest_chl.chlorophyll_mg_m3 if nearest_chl else None

            quality = _classify_quality(g["gradient_c_per_km"], chl_value)
            if quality is None:
                continue  # below threshold, not a candidate zone

            distance_km = _haversine_km(g["midpoint"], coastline_ref) if coastline_ref else 0.0

            advisories.append(
                PFZAdvisory(
                    location=g["midpoint"],
                    distance_from_coast_km=round(distance_km, 1),
                    quality=quality,
                    valid_date=datetime.now(timezone.utc),
                    zone_polygon=_front_zone_polygon(g["p1"], g["p2"]),
                    notes=(
                        f"Derived from SST gradient {g['gradient_c_per_km']:.3f}°C/km"
                        + (f" and chlorophyll {chl_value:.2f} mg/m³" if chl_value is not None else " (no nearby chlorophyll reading)")
                        + f"; mapped as a ±{ZONE_HALF_WIDTH_KM:.0f} km corridor around the detected front (illustrative extent, not an INCOIS boundary)"
                    ),
                )
            )

        # Sort strongest candidates first, cap to a reasonable number for display
        quality_rank = {PFZQuality.HIGH: 0, PFZQuality.MODERATE: 1, PFZQuality.LOW: 2}
        advisories.sort(key=lambda a: quality_rank[a.quality])
        advisories = advisories[:8]

        return PFZDataBundle(
            region=region,
            advisories=advisories,
            source=DataSourceStatus.DERIVED,
            disclaimer=(
                "These zones are DERIVED from open satellite (NOAA CoastWatch) SST/chlorophyll data using a "
                "simplified version of the published PFZ methodology (SST gradient + "
                "chlorophyll threshold). This is NOT the official INCOIS PFZ advisory, "
                "which uses additional proprietary processing and validation. Treat as "
                "an illustrative, physically-grounded estimate, not an authoritative "
                "fishing advisory."
            ),
        )

    @staticmethod
    def _empty_bundle(region: str, reason: str) -> PFZDataBundle:
        return PFZDataBundle(
            region=region,
            advisories=[],
            source=DataSourceStatus.DERIVED,
            disclaimer=f"No PFZ candidate zones derived: {reason}.",
        )
