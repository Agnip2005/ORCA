#!/usr/bin/env python3
"""
Downloads a REAL India coastline/outline GeoJSON and writes it to
app/core/india_coastline.geojson, so the frontend can draw an accurate
India instead of the bundled illustrative SVG silhouette.

Run this on your own machine (needs internet access), like
scripts/fetch_real_eez.py:

    python scripts/fetch_real_coastline.py

Sources (tried in order, both verified reachable at time of writing):

 1. Natural Earth 10m admin-0 countries (public domain) -- one ~13 MB
    global file; we keep only the India feature (mainland + Andaman &
    Nicobar Islands + Lakshadweep).
    https://github.com/nvkelso/natural-earth-vector
 2. geoBoundaries gbOpen IND ADM0, simplified (~90 KB, CC0-1.0) -- a much
    smaller download if Natural Earth is unreachable.
    https://www.geoboundaries.org/  (API: gbOpen / IND / ADM0)

The output FeatureCollection carries `source`, `source_url`, `license` and
`fetched_at` fields at the top level; GET /api/geo serves them to the UI so
the map can label its own provenance. If this script has not been run,
/api/geo reports the coastline as missing and the UI falls back to the
labeled "approximate silhouette" -- it never silently pretends.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent.parent / "app" / "core" / "india_coastline.geojson"
USER_AGENT = "marine-intel-sih2026/1.0"

# (label, url, license, kind) -- kind tells the filter how to pick India.
SOURCES = [
    (
        "Natural Earth 10m admin-0 countries (India feature)",
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson",
        "Public domain (Natural Earth)",
        "ne",
    ),
    (
        "geoBoundaries gbOpen IND ADM0 (simplified)",
        "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM0/geoBoundaries-IND-ADM0_simplified.geojson",
        "CC0-1.0 (geoBoundaries / Wikimedia Commons)",
        "gb",
    ),
]


def _download(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _is_india(properties: dict) -> bool:
    """Match India across Natural Earth and geoBoundaries property naming."""
    for key in ("ADMIN", "admin", "NAME", "name", "boundaryCanonical", "SHAPENAME"):
        value = str(properties.get(key, "")).strip().lower()
        if value == "india":
            return True
    for key in ("ISO_A3", "ISO_A3_EH", "SOV_A3", "ADM0_A3", "boundaryISO"):
        value = str(properties.get(key, "")).strip().upper()
        if value == "IND":
            return True
    return False


def _extract_india_features(data: dict, kind: str) -> list[dict]:
    features = data.get("features")
    if features is None and data.get("type") == "Feature":
        features = [data]
    if not features:
        raise ValueError("response has no 'features' array")

    if kind == "gb":
        # geoBoundaries serves a single ADM0 feature for India already.
        return features

    india = [f for f in features if isinstance(f, dict) and _is_india(f.get("properties") or {})]
    if not india:
        raise ValueError("no India feature found (property names changed upstream?)")
    return india


def _describe(features: list[dict]) -> str:
    parts = 0
    for feature in features:
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates")
        if geom.get("type") == "MultiPolygon":
            parts += len(coords)
        elif geom.get("type") == "Polygon":
            parts += 1
    return f"{len(features)} feature(s), {parts} polygon part(s) (mainland + islands)"


def main() -> None:
    print("Fetching a real India coastline/outline for the frontend map...\n")
    errors = []

    for label, url, license_name, kind in SOURCES:
        print(f"Trying: {label}\n  {url}")
        try:
            raw = _download(url)
            data = json.loads(raw.decode("utf-8"))
            features = _extract_india_features(data, kind)
        except Exception as exc:  # network error, bad JSON, filter miss
            print(f"  -> failed: {exc}")
            errors.append(f"{label}: {exc}")
            print()
            continue

        out = {
            "type": "FeatureCollection",
            "source": label,
            "source_url": url,
            "license": license_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "features": features,
        }
        OUTPUT_PATH.write_text(json.dumps(out, indent=1))
        print(f"  -> OK: {_describe(features)}")
        print(f"\nSaved real India coastline to: {OUTPUT_PATH}")
        print("Source: " + label)
        print("License: " + license_name)
        print("\nThe map API (GET /api/geo) picks this up immediately; restart not")
        print("required. The frontend will label the coastline with this source.")
        return

    print("\nAll sources failed:")
    for err in errors:
        print(f"  - {err}")
    print("\nManual fallback:")
    print("  1. Download any India-outline GeoJSON (Feature or FeatureCollection),")
    print(f"  2. Save it as: {OUTPUT_PATH}")
    print("  3. Include a top-level \"source\" field describing where it came from.")
    print("\nUntil then the UI shows the labeled approximate silhouette -- honest,")
    print("but not survey-grade.")
    sys.exit(1)


if __name__ == "__main__":
    main()
