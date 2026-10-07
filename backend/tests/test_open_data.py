"""
Unit tests for the open-data layer (Open-Meteo weather + ERDDAP ocean).
No network, no credentials: they run on canned payloads shaped like the real APIs.

    python -m unittest discover -s tests -t .
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.fetchers.erddap_client import ERDDAPError, build_query, parse_grid, parse_info
from app.fetchers.ocean_fetcher import load_candidates, to_celsius
from app.fetchers.weather_fetcher import REGION_POINTS, WeatherFetchError, build_bundle

POINT = REGION_POINTS["kerala coast"]


def _fc(wind=20.0, gust=30.0, temp=28.0, code=1, vis=24000.0, hourly_gust=None, hourly_code=None):
    times = [f"2026-09-29T{h:02d}:00" for h in range(5, 24)]
    return {
        "current": {"time": "2026-09-29T05:00", "temperature_2m": temp, "wind_speed_10m": wind,
                    "wind_gusts_10m": gust, "weather_code": code, "visibility": vis},
        "hourly": {"time": times,
                   "wind_gusts_10m": hourly_gust or [gust] * len(times),
                   "wind_speed_10m": [wind] * len(times),
                   "weather_code": hourly_code or [code] * len(times)},
    }


def _marine(wave=1.0, hourly=None):
    times = [f"2026-09-29T{h:02d}:00" for h in range(5, 24)]
    return {"current": {"time": "2026-09-29T05:00", "wave_height": wave, "sea_surface_temperature": 29.1},
            "hourly": {"time": times, "wave_height": hourly or [wave] * len(times)}}


class WeatherTests(unittest.TestCase):
    def test_calm_day_no_hazards(self):
        b = build_bundle("Kerala Coast", POINT, _fc(), _marine())
        self.assertEqual(b.wind_speed_kmh, 20.0)
        self.assertEqual(b.wave_height_m, 1.0)
        self.assertEqual(b.temperature_celsius, 28.0)
        self.assertEqual(b.hazards, [])
        self.assertEqual(b.observed_at, datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc))
        self.assertIn("Open-Meteo", b.notes)

    def test_high_wind_and_rough_sea(self):
        b = build_bundle("x", POINT, _fc(wind=60, gust=80), _marine(wave=3.0))
        types = {h.hazard_type: h.severity for h in b.hazards}
        self.assertEqual(types["high_wind"], "high")
        self.assertEqual(types["rough_sea"], "moderate")
        self.assertIn("forecast_gusts", types)

    def test_thunderstorm_code(self):
        b = build_bundle("x", POINT, _fc(code=95), _marine())
        self.assertIn("thunderstorm", {h.hazard_type for h in b.hazards})
        b = build_bundle("x", POINT, _fc(code=99), _marine())
        self.assertEqual([h.severity for h in b.hazards if h.hazard_type == "thunderstorm"], ["severe"])

    def test_low_visibility(self):
        b = build_bundle("x", POINT, _fc(vis=300), _marine())
        self.assertEqual([h.severity for h in b.hazards if h.hazard_type == "low_visibility"], ["high"])

    def test_forecast_peak_wave_later_today(self):
        hourly = [1.0] * 19
        hourly[10] = 4.0
        b = build_bundle("x", POINT, _fc(), _marine(wave=1.0, hourly=hourly))
        h = next(h for h in b.hazards if h.hazard_type == "forecast_rough_sea")
        self.assertEqual(h.severity, "moderate")
        self.assertIsNotNone(h.valid_until)

    def test_missing_wave_raises(self):
        with self.assertRaises(WeatherFetchError):
            build_bundle("x", POINT, _fc(), _marine(wave=None))

    def test_missing_wind_raises(self):
        with self.assertRaises(WeatherFetchError):
            build_bundle("x", POINT, _fc(wind=None), _marine())


def _info_json(dims, var="analysed_sst", lat_sp=0.01, lon_sp=0.01, extra_rows=()):
    rows = [["dimension", d, "", "double", f"nValues=100, evenlySpaced=true, averageSpacing={sp}"]
            for d, sp in zip(dims, [1.0, lat_sp, lon_sp] if len(dims) == 3 else [1.0, 1.0, lat_sp, lon_sp])]
    rows.append(["variable", var, "", "float", ", ".join(dims)])
    rows.append(["attribute", "NC_GLOBAL", "title", "String", "Test SST"])
    rows.append(["attribute", var, "units", "String", "degree_C"])
    rows.extend(extra_rows)
    return {"table": {"columnNames": ["Row Type", "Variable Name", "Attribute Name", "Data Type", "Value"],
                      "rows": rows}}


BOUNDS = {"lat_min": 8.0, "lat_max": 12.5, "lon_min": 74.5, "lon_max": 77.5}


class ERDDAPTests(unittest.TestCase):
    def test_parse_info_basic(self):
        info = parse_info("ds", _info_json(["time", "latitude", "longitude"]), ["analysed_sst"])
        self.assertEqual((info.lat_dim, info.lon_dim, info.time_dim), ("latitude", "longitude", "time"))
        self.assertEqual(info.title, "Test SST")

    def test_parse_info_missing_variable(self):
        with self.assertRaises(ERDDAPError):
            parse_info("ds", _info_json(["time", "latitude", "longitude"]), ["nope"])

    def test_query_thins_grid_ascending(self):
        info = parse_info("ds", _info_json(["time", "latitude", "longitude"]), ["analysed_sst"])
        q = build_query(info, BOUNDS, 0.25)
        self.assertEqual(q, "analysed_sst[(last)][(8.0):25:(12.5)][(74.5):25:(77.5)]")

    def test_query_altitude_axis_and_descending_lat(self):
        info = parse_info("ds", _info_json(["time", "altitude", "latitude", "longitude"], var="chlorophyll",
                                          lat_sp=-0.0417, lon_sp=0.0417), ["chlorophyll"])
        q = build_query(info, BOUNDS, 0.25)
        self.assertEqual(q, "chlorophyll[(last)][0][(12.5):6:(8.0)][(74.5):6:(77.5)]")
        # reverse flag flips the lat order
        self.assertIn("[(8.0):6:(12.5)]", build_query(info, BOUNDS, 0.25, reverse_lat=True))

    def test_parse_grid_drops_nulls(self):
        info = parse_info("ds", _info_json(["time", "latitude", "longitude"]), ["analysed_sst"])
        data = {"table": {"columnNames": ["time", "latitude", "longitude", "analysed_sst"],
                          "rows": [["2026-09-28T09:00:00Z", 9.0, 75.0, 29.1],
                                   ["2026-09-28T09:00:00Z", 9.0, 75.25, None]]}}
        self.assertEqual(parse_grid(info, data), [("2026-09-28T09:00:00Z", 9.0, 75.0, 29.1)])

    def test_kelvin_conversion(self):
        self.assertAlmostEqual(to_celsius([300.15, 301.15])[0], 27.0)
        self.assertEqual(to_celsius([28.0, 29.0]), [28.0, 29.0])

    def test_shipped_candidates_load(self):
        c = load_candidates()
        self.assertTrue(c["sst"] and c["chlorophyll"])


class LLMPayloadTests(unittest.TestCase):
    def test_compaction_shrinks_prompt(self):
        import json
        from app.core.llm_payload import compact_ocean, compact_pfz
        from app.models.schemas import (ChlorophyllReading, DataSourceStatus as S, GeoPoint, OceanDataBundle,
                                        PFZAdvisory, PFZDataBundle, PFZQuality, SSTReading)
        now = datetime(2026, 9, 29, tzinfo=timezone.utc)
        sst = [SSTReading(location=GeoPoint(lat=8 + i * .25, lon=75), sst_celsius=27 + i * .01, observed_at=now, source=S.LIVE) for i in range(216)]
        chl = [ChlorophyllReading(location=GeoPoint(lat=8 + i * .25, lon=75), chlorophyll_mg_m3=.1 + i * .01, observed_at=now, source=S.LIVE) for i in range(216)]
        ocean = OceanDataBundle(region="r", sst_readings=sst, chlorophyll_readings=chl, source=S.LIVE, notes="n" * 500)
        poly = [GeoPoint(lat=9, lon=75)] * 5
        pfz = PFZDataBundle(region="r", source=S.DERIVED, advisories=[
            PFZAdvisory(location=GeoPoint(lat=9, lon=75), distance_from_coast_km=10, quality=PFZQuality.LOW,
                        valid_date=now, zone_polygon=poly) for _ in range(30)])
        full = len(json.dumps([ocean.model_dump(mode="json"), pfz.model_dump(mode="json")]))
        co, cp = compact_ocean(ocean), compact_pfz(pfz)
        small = len(json.dumps([co.model_dump(mode="json"), cp.model_dump(mode="json")]))
        self.assertLess(small, full / 5)
        self.assertEqual(len(co.sst_readings), 8)
        self.assertEqual(co.sst_readings[0].sst_celsius, 27.0)      # min kept
        self.assertAlmostEqual(co.sst_readings[-1].sst_celsius, 27 + 215 * .01)  # max kept
        self.assertEqual(len(cp.advisories), 6)
        self.assertIsNone(cp.advisories[0].zone_polygon)
        self.assertEqual(len(ocean.sst_readings), 216)               # original untouched


if __name__ == "__main__":
    unittest.main()
