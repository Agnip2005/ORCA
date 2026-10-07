#!/usr/bin/env python3
"""
Live smoke test for the OPEN data sources (no keys needed for these).

Checks, with PASS/WARN/FAIL each:
  1. OPENAI_API_KEY configured (LLM agents; unchanged)
  2. Open-Meteo forecast + marine reachable for every region's reference point
  3. WeatherFetcher end-to-end
  4. ERDDAP (NOAA CoastWatch) SST + chlorophyll candidates: which one works
  5. OceanFetcher end-to-end
  6. Map geometry files present

Usage (from backend/):
    python scripts/smoke_test.py
    python scripts/smoke_test.py --region "tamil nadu coast"
Exit code 1 if any FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings  # noqa: E402
from app.fetchers.ocean_fetcher import OceanFetcher  # noqa: E402
from app.fetchers.open_meteo_client import OpenMeteoClient  # noqa: E402
from app.fetchers.weather_fetcher import REGION_POINTS, WeatherFetcher  # noqa: E402

CORE_DIR = Path(__file__).parent.parent / "app" / "core"
RESULTS: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    print(f"  [{status}] {name}" + (f" -> {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n--- {title} ---")


async def check_open_meteo() -> None:
    section("Open-Meteo (open, no key)")
    om = OpenMeteoClient()
    for region, pt in REGION_POINTS.items():
        try:
            fc = await om.forecast(pt["lat"], pt["lon"])
            mar = await om.marine(pt["lat"], pt["lon"])
            wave = (mar.get("current") or {}).get("wave_height")
            wind = (fc.get("current") or {}).get("wind_speed_10m")
            if wave is None or wind is None:
                record("FAIL", f"Open-Meteo {region}", f"null wave/wind at {pt} (point on land?)")
            else:
                record("PASS", f"Open-Meteo {region}", f"wind={wind} km/h, wave={wave} m")
        except Exception as exc:
            record("FAIL", f"Open-Meteo {region}", str(exc)[:300])
    await om.aclose()


async def check_weather_bundle(region: str) -> None:
    section(f"WeatherFetcher end-to-end ({region})")
    f = WeatherFetcher()
    try:
        b = await f.fetch(region)
        record("PASS", "WeatherFetcher.fetch",
               f"wind={b.wind_speed_kmh} km/h, wave={b.wave_height_m} m, temp={b.temperature_celsius}C, "
               f"hazards={len(b.hazards)}")
        print(f"       notes: {b.notes}")
    except Exception as exc:
        record("FAIL", "WeatherFetcher.fetch", str(exc)[:400])
    finally:
        await f.aclose()


async def check_ocean(region: str) -> None:
    section(f"OceanFetcher end-to-end ({region}) -- NOAA CoastWatch ERDDAP")
    f = OceanFetcher()
    try:
        b = await f.fetch(region)
        sst = [r.sst_celsius for r in b.sst_readings]
        record("PASS", "OceanFetcher.fetch",
               f"{len(sst)} SST px ({min(sst):.1f}-{max(sst):.1f} C), {len(b.chlorophyll_readings)} chl px")
        print(f"       notes: {b.notes}")
        if not (20 <= sum(sst) / len(sst) <= 35):
            record("WARN", "SST plausibility", "mean SST outside 20-35 C for Indian waters - check units/dataset")
    except Exception as exc:
        record("FAIL", "OceanFetcher.fetch", str(exc)[:600])
    finally:
        await f.aclose()


def check_map_geometry() -> None:
    section("Map geometry files")
    if (CORE_DIR / "india_coastline.geojson").exists():
        record("PASS", "real India coastline")
    else:
        record("WARN", "real India coastline", "run: python scripts/fetch_real_coastline.py")
    if (CORE_DIR / "india_eez_real.geojson").exists():
        record("PASS", "real India EEZ (Marine Regions MRGID 8480)")
    else:
        record("WARN", "real India EEZ", "run: python scripts/fetch_real_eez.py (placeholder polygon until then)")


async def run(region: str) -> int:
    print("=" * 72)
    print("SMOKE TEST -- open data sources (Open-Meteo, NOAA CoastWatch ERDDAP)")
    print("=" * 72)
    section("Config")
    record("PASS" if settings.openai_api_key else "FAIL", "OPENAI_API_KEY configured")
    await check_open_meteo()
    await check_weather_bundle(region)
    await check_ocean(region)
    check_map_geometry()

    fails = [r for r in RESULTS if r[0] == "FAIL"]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print("\n" + "=" * 72)
    print(f"SUMMARY: {len(RESULTS) - len(fails) - len(warns)} PASS | {len(warns)} WARN | {len(fails)} FAIL")
    for _, n, d in fails:
        print(f"  FAIL: {n} -- {d}")
    for _, n, d in warns:
        print(f"  WARN: {n} -- {d}")
    print("=" * 72)
    return 1 if fails else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="kerala coast")
    sys.exit(asyncio.run(run(ap.parse_args().region)))


if __name__ == "__main__":
    main()
