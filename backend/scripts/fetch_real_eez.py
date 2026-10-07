#!/usr/bin/env python3
"""
Downloads the REAL India EEZ boundary polygon from Marine Regions (VLIZ)
and swaps it in for the simplified placeholder used during early dev.

Run this on your own machine (needs internet access to geo.vliz.be),
NOT inside the Claude sandbox -- that network is allowlisted and can't
reach this domain.

Usage:
    python scripts/fetch_real_eez.py

Source: Marine Regions WFS (Flanders Marine Institute / VLIZ)
India EEZ gazetteer entry: https://marineregions.org/gazetteer.php?p=details&id=8480
MRGID for India EEZ: 8480
"""
import json
import sys
import urllib.request
from pathlib import Path

MRGID_INDIA_EEZ = 8480
WFS_URL = (
    "https://geo.vliz.be/geoserver/MarineRegions/wfs"
    "?service=WFS&version=1.0.0&request=GetFeature"
    f"&typeNames=eez&cql_filter=mrgid={MRGID_INDIA_EEZ}"
    "&outputFormat=application/json"
)

OUTPUT_PATH = Path(__file__).parent.parent / "app" / "core" / "india_eez_real.geojson"


def main():
    print(f"Fetching India EEZ (MRGID {MRGID_INDIA_EEZ}) from Marine Regions WFS...")
    print(f"URL: {WFS_URL}")

    try:
        req = urllib.request.Request(WFS_URL, headers={"User-Agent": "marine-intel-sih2026/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"\nFAILED to fetch: {exc}")
        print("\nManual fallback:")
        print("  1. Open this URL in a browser:")
        print(f"     {WFS_URL}")
        print(f"  2. Save the response as: {OUTPUT_PATH}")
        sys.exit(1)

    features = data.get("features", [])
    if not features:
        print("WARNING: response contained no features. Check the MRGID or query.")
        print(json.dumps(data, indent=2)[:500])
        sys.exit(1)

    OUTPUT_PATH.write_text(json.dumps(data, indent=2))
    print(f"\nSaved real India EEZ polygon to: {OUTPUT_PATH}")
    print(f"Features: {len(features)}")
    print("\nNext step: update app/core/boundary_guardrail.py to load")
    print("'india_eez_real.geojson' instead of 'india_eez_simplified.geojson'")
    print("(or just delete the simplified file and rename this one to match --")
    print(" the loader looks for india_eez_simplified.geojson by default).")


if __name__ == "__main__":
    main()
