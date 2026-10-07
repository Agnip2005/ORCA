"""
Unit tests for the derived PFZ engine -- quality classification, SST
gradient computation, and the non-uniform zone polygon (the corridor
drawn around each detected front).

Pure functions over synthetic readings: no network, no ERDDAP pin, no
credentials. Run from backend/:

    python -m unittest discover -s tests -t .
    python -m unittest tests.test_pfz_engine
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.fetchers.pfz_derived_engine import (
    DerivedPFZEngine,
    ZONE_HALF_WIDTH_KM,
    _classify_quality,
    _front_zone_polygon,
    compute_sst_gradients,
)
from app.models.schemas import (
    ChlorophyllReading,
    DataSourceStatus,
    GeoPoint,
    PFZQuality,
    SSTReading,
)

NOW = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)


def _sst(lat: float, lon: float, temp: float) -> SSTReading:
    return SSTReading(
        location=GeoPoint(lat=lat, lon=lon),
        sst_celsius=temp,
        observed_at=NOW,
        source=DataSourceStatus.LIVE,
    )


def _chl(lat: float, lon: float, value: float) -> ChlorophyllReading:
    return ChlorophyllReading(
        location=GeoPoint(lat=lat, lon=lon),
        chlorophyll_mg_m3=value,
        observed_at=NOW,
        source=DataSourceStatus.LIVE,
    )


class QualityClassificationTests(unittest.TestCase):
    def test_strong_front_plus_high_chl_is_high(self):
        self.assertEqual(_classify_quality(0.06, 0.6), PFZQuality.HIGH)

    def test_strong_front_plus_moderate_chl_is_moderate(self):
        self.assertEqual(_classify_quality(0.06, 0.3), PFZQuality.MODERATE)

    def test_moderate_front_plus_high_chl_is_moderate(self):
        self.assertEqual(_classify_quality(0.03, 0.6), PFZQuality.MODERATE)

    def test_moderate_front_plus_moderate_chl_is_low(self):
        self.assertEqual(_classify_quality(0.03, 0.3), PFZQuality.LOW)

    def test_below_bar_is_not_flagged(self):
        self.assertIsNone(_classify_quality(0.01, 0.1))
        self.assertIsNone(_classify_quality(0.06, 0.1))  # strong front, no chl
        self.assertIsNone(_classify_quality(0.03, None))  # missing chl counts as 0

    def test_missing_chlorophyll_reads_as_zero(self):
        # None chl -> treated as 0 mg/m3: never high_chl, never moderate_chl,
        # so even a strong front alone does not qualify.
        self.assertIsNone(_classify_quality(0.06, None))
        self.assertIsNone(_classify_quality(0.03, None))


class GradientTests(unittest.TestCase):
    def test_pair_within_range_produces_gradient_and_endpoints(self):
        gradients = compute_sst_gradients([_sst(10.0, 76.0, 28.0), _sst(10.0, 76.1, 27.5)])
        self.assertEqual(len(gradients), 1)
        g = gradients[0]
        # ~10.9 km apart at 10N for 0.1 deg lon -> ~0.046 C/km
        self.assertAlmostEqual(g["gradient_c_per_km"], 0.5 / 10.9, delta=0.01)
        self.assertEqual(g["p1"], GeoPoint(lat=10.0, lon=76.0))
        self.assertEqual(g["p2"], GeoPoint(lat=10.0, lon=76.1))
        self.assertAlmostEqual(g["midpoint"].lat, 10.0)
        self.assertAlmostEqual(g["midpoint"].lon, 76.05)

    def test_pairs_too_close_or_too_far_are_skipped(self):
        # same point (0 km) and points ~0.11 km apart are both skipped
        gradients = compute_sst_gradients([_sst(10.0, 76.0, 28.0), _sst(10.0, 76.0, 27.0)])
        self.assertEqual(gradients, [])
        far = [_sst(10.0, 76.0, 28.0), _sst(14.0, 76.0, 27.0)]  # ~444 km apart
        self.assertEqual(compute_sst_gradients(far), [])

    def test_identical_temperatures_yield_zero_gradient(self):
        gradients = compute_sst_gradients([_sst(10.0, 76.0, 28.0), _sst(10.0, 76.1, 28.0)])
        self.assertEqual(gradients[0]["gradient_c_per_km"], 0.0)


class ZonePolygonTests(unittest.TestCase):
    """Each zone is a non-uniform corridor around its own front segment."""

    def test_horizontal_front_gets_lat_extent_of_about_twice_half_width(self):
        polygon = _front_zone_polygon(GeoPoint(lat=10.0, lon=76.0), GeoPoint(lat=10.0, lon=76.1))
        self.assertEqual(len(polygon), 4)
        lats = sorted({p.lat for p in polygon})
        self.assertEqual(len(lats), 2)
        # corridor total width ~10 km -> ~0.09 deg latitude
        self.assertAlmostEqual(lats[1] - lats[0], (2 * ZONE_HALF_WIDTH_KM) / 111.0, delta=0.005)
        # long sides follow the segment endpoints
        self.assertEqual({p.lon for p in polygon}, {76.0, 76.1})

    def test_vertical_front_gets_lon_extent_instead(self):
        polygon = _front_zone_polygon(GeoPoint(lat=10.0, lon=76.0), GeoPoint(lat=10.2, lon=76.0))
        self.assertEqual(len(polygon), 4)
        lons = sorted({p.lon for p in polygon})
        self.assertAlmostEqual(lons[1] - lons[0], (2 * ZONE_HALF_WIDTH_KM) / 111.0, delta=0.006)
        self.assertEqual({p.lat for p in polygon}, {10.0, 10.2})

    def test_zones_from_different_fronts_have_different_shapes(self):
        horizontal = _front_zone_polygon(GeoPoint(lat=10.0, lon=76.0), GeoPoint(lat=10.0, lon=76.1))
        vertical = _front_zone_polygon(GeoPoint(lat=10.0, lon=76.0), GeoPoint(lat=10.2, lon=76.0))
        self.assertNotEqual(horizontal, vertical)

    def test_degenerate_segment_returns_empty(self):
        p = GeoPoint(lat=10.0, lon=76.0)
        self.assertEqual(_front_zone_polygon(p, p), [])

    def test_deterministic(self):
        p1, p2 = GeoPoint(lat=11.0, lon=75.0), GeoPoint(lat=11.3, lon=75.4)
        self.assertEqual(_front_zone_polygon(p1, p2), _front_zone_polygon(p1, p2))


class EngineEndToEndTests(unittest.TestCase):
    def test_compute_returns_advisory_with_zone_polygon(self):
        bundle = DerivedPFZEngine().compute(
            region="kerala coast",
            sst_readings=[_sst(10.0, 76.0, 28.0), _sst(10.0, 76.1, 27.5)],
            chl_readings=[_chl(10.0, 76.05, 0.6)],
            coastline_ref=GeoPoint(lat=10.0, lon=76.0),
        )
        self.assertEqual(bundle.source, DataSourceStatus.DERIVED)
        self.assertEqual(len(bundle.advisories), 1)
        advisory = bundle.advisories[0]
        self.assertEqual(advisory.quality, PFZQuality.MODERATE)
        self.assertIsNotNone(advisory.zone_polygon)
        self.assertEqual(len(advisory.zone_polygon), 4)
        self.assertGreater(advisory.distance_from_coast_km, 0)
        self.assertIn("corridor", advisory.notes)
        self.assertIn("NOT the official INCOIS PFZ advisory", bundle.disclaimer)

    def test_calm_ocean_yields_no_advisories_not_an_error(self):
        bundle = DerivedPFZEngine().compute(
            region="kerala coast",
            sst_readings=[_sst(10.0, 76.0, 28.0), _sst(10.0, 76.1, 28.0)],
            chl_readings=[_chl(10.0, 76.05, 0.1)],
        )
        self.assertEqual(bundle.advisories, [])

    def test_insufficient_readings_return_explained_empty_bundle(self):
        bundle = DerivedPFZEngine().compute(
            region="kerala coast", sst_readings=[_sst(10.0, 76.0, 28.0)], chl_readings=[]
        )
        self.assertEqual(bundle.advisories, [])
        self.assertIn("insufficient SST readings", bundle.disclaimer)


if __name__ == "__main__":
    unittest.main()
