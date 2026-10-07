"""
Maritime Boundary Guardrail.

Per the architecture diagram, this is a DETERMINISTIC check that runs
alongside the fetchers off the orchestrator, and its output can hard-stop
the pipeline (bypassing synthesis entirely) if a query is directing a
vessel into or near a boundary it shouldn't cross. This is intentionally
NOT an LLM call -- a safety-critical geofence should be reproducible,
auditable code, not a language model's judgment.

Loads app/core/india_eez_real.geojson if present (produced by
scripts/fetch_real_eez.py), otherwise falls back to the simplified
placeholder polygon with a loud warning -- the simplified polygon is NOT
accurate enough for a real safety claim.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shapely.geometry import Point, shape

from app.models.schemas import BoundaryCheckResult, GeoPoint

logger = logging.getLogger(__name__)

_CORE_DIR = Path(__file__).parent
_REAL_EEZ_PATH = _CORE_DIR / "india_eez_real.geojson"
_SIMPLIFIED_EEZ_PATH = _CORE_DIR / "india_eez_simplified.geojson"

# Distance inside which we warn even if technically still within the EEZ,
# to give a fisherman advance notice before they cross.
WARNING_BUFFER_KM = 15.0


class MaritimeBoundaryGuardrail:
    def __init__(self):
        self._polygon, self._using_real_data = self._load_boundary()

    def _load_boundary(self):
        if _REAL_EEZ_PATH.exists():
            data = json.loads(_REAL_EEZ_PATH.read_text())
            geom = shape(data["features"][0]["geometry"])
            logger.info("Boundary guardrail loaded REAL India EEZ polygon from Marine Regions.")
            return geom, True

        logger.warning(
            "Boundary guardrail using SIMPLIFIED/APPROXIMATE EEZ polygon. "
            "Run scripts/fetch_real_eez.py on a machine with internet access "
            "before relying on this for anything beyond a demo."
        )
        data = json.loads(_SIMPLIFIED_EEZ_PATH.read_text())
        geom = shape(data["features"][0]["geometry"])
        return geom, False

    def check(self, point: GeoPoint) -> BoundaryCheckResult:
        shapely_point = Point(point.lon, point.lat)
        is_inside = self._polygon.contains(shapely_point)

        # Rough degrees->km conversion for a quick distance-to-boundary estimate.
        # Fine for a warning buffer; not for legal/navigational precision.
        # .boundary works for both Polygon and MultiPolygon (unlike .exterior,
        # which only exists on a single Polygon).
        distance_deg = self._polygon.boundary.distance(shapely_point)
        distance_km = distance_deg * 111.0

        accuracy_note = "" if self._using_real_data else " [DEMO DATA: approximate boundary, not for real navigation]"

        if not is_inside:
            return BoundaryCheckResult(
                is_safe=False,
                distance_to_boundary_km=round(distance_km, 1),
                boundary_type="EEZ",
                message=(
                    f"This location falls outside India's Exclusive Economic Zone. "
                    f"Advisories cannot be issued for this position.{accuracy_note}"
                ),
            )

        if distance_km < WARNING_BUFFER_KM:
            return BoundaryCheckResult(
                is_safe=True,
                distance_to_boundary_km=round(distance_km, 1),
                boundary_type="EEZ",
                message=(
                    f"This location is within {distance_km:.1f} km of the EEZ boundary. "
                    f"Proceed with caution.{accuracy_note}"
                ),
            )

        return BoundaryCheckResult(
            is_safe=True,
            distance_to_boundary_km=round(distance_km, 1),
            boundary_type="EEZ",
            message=f"Location is safely within India's EEZ.{accuracy_note}",
        )


maritime_boundary_guardrail = MaritimeBoundaryGuardrail()
