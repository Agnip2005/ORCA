#!/usr/bin/env python3
"""
Optional helper: verify / refresh the ERDDAP candidate datasets.

The project ships app/core/erddap_datasets.json pre-filled with open NOAA
CoastWatch datasets, and the OceanFetcher tries them in order at runtime, so
you do NOT need to run this to start the server. Run it when a dataset has been
retired (ERDDAP catalogs change) or to add new candidates:

    python scripts/discover_erddap_datasets.py            # verify current candidates
    python scripts/discover_erddap_datasets.py --search   # also list catalog matches

It queries the server's own catalog (`/search/index.json`) and /info pages,
prints which candidates still exist, and (with --write) drops dead ones.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE = "https://coastwatch.pfeg.noaa.gov/erddap"
OUT = Path(__file__).parent.parent / "app" / "core" / "erddap_datasets.json"
SEARCH_TERMS = {"sst": "MUR SST daily global", "chlorophyll": "chlorophyll-a VIIRS MODIS global"}


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SIH-Marine-Intelligence/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def exists(dataset_id: str) -> bool:
    try:
        get_json(f"{BASE}/info/{dataset_id}/index.json")
        return True
    except (urllib.error.URLError, ValueError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", action="store_true", help="list catalog search matches")
    ap.add_argument("--write", action="store_true", help="remove dead candidates from the JSON")
    args = ap.parse_args()

    cfg = json.loads(OUT.read_text())
    for key in ("sst", "chlorophyll"):
        print(f"\n== {key} ==")
        alive = []
        for cand in cfg[key]["candidates"]:
            ok = exists(cand["dataset_id"])
            print(f"  [{'OK ' if ok else 'DEAD'}] {cand['dataset_id']}  {cand.get('note', '')}")
            if ok:
                alive.append(cand)
        cfg[key]["candidates"] = alive or cfg[key]["candidates"]
        if not alive:
            print("  !! no live candidate - add one with --search")
        if args.search:
            q = urllib.parse.urlencode({"page": 1, "itemsPerPage": 15, "searchFor": SEARCH_TERMS[key]})
            try:
                t = get_json(f"{BASE}/search/index.json?{q}")["table"]
                ci, ct = t["columnNames"].index("Dataset ID"), t["columnNames"].index("Title")
                for r in t["rows"]:
                    print(f"     found: {r[ci]:35s} {r[ct][:70]}")
            except Exception as exc:  # helper script, report and continue
                print(f"     search failed: {exc}")
    if args.write:
        OUT.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
