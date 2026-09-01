"""Tests for the physics reference.

These check the ephemeris against facts that are true independently of the
implementation — the sun's declination at solstice, the geometry of solar
noon, the shape of the cotangent — rather than against numbers copied out of
a previous run. If the algorithm is wrong, a self-consistent test would agree
with it and tell us nothing.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app import ephemeris as eph

UTC = timezone.utc

# The demo location: a beach at a low northern latitude, west coast so the sun
# sets over the water. Longitude is east-positive throughout.
DEMO_LAT = 8.75
DEMO_LON = -83.5


def _solar_noon(lat: float, lon: float, day: datetime) -> datetime:
    """Brute-force the moment of maximum elevation, to the minute."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    return max(
        (start + timedelta(minutes=i) for i in range(1440)),
        key=lambda ts: eph.solar_position(lat, lon, ts).elevation_deg,
    )


class TestSolarPosition:
    def test_declination_at_solstices(self):
        """Peak noon elevation on the solstices pins the obliquity of the ecliptic."""
        for date, expected_decl in [
            (datetime(2026, 6, 21, tzinfo=UTC), 23.44),
            (datetime(2026, 12, 21, tzinfo=UTC), -23.44),
        ]:
            noon = _solar_noon(DEMO_LAT, DEMO_LON, date)
            elevation = eph.solar_position(DEMO_LAT, DEMO_LON, noon).elevation_deg
            # At solar noon: elevation = 90 - |latitude - declination|.
            implied_decl = DEMO_LAT - (90.0 - elevation) * (
                1 if DEMO_LAT > expected_decl else -1
            )
            assert implied_decl == pytest.approx(expected_decl, abs=0.3)

    def test_equinox_sun_overhead_at_equator(self):
        """On the equinox the subsolar point crosses the equator."""
        noon = _solar_noon(0.0, 0.0, datetime(2026, 3, 20, tzinfo=UTC))
        pos = eph.solar_position(0.0, 0.0, noon)
        assert pos.elevation_deg == pytest.approx(90.0, abs=0.6)

    def test_noon_azimuth_is_south_in_northern_summer_tropics(self):
        """North of the tropic, the noon sun sits due south."""
        lat = 40.0
        noon = _solar_noon(lat, 0.0, datetime(2026, 12, 21, tzinfo=UTC))
        assert eph.solar_position(lat, 0.0, noon).azimuth_deg == pytest.approx(180.0, abs=1.0)

    def test_azimuth_sweeps_eastward_through_the_day(self):
        """Morning sun in the east, afternoon sun in the west."""
        day = datetime(2026, 9, 8, tzinfo=UTC)
        noon = _solar_noon(DEMO_LAT, DEMO_LON, day)
        morning = eph.solar_position(DEMO_LAT, DEMO_LON, noon - timedelta(hours=4))
        afternoon = eph.solar_position(DEMO_LAT, DEMO_LON, noon + timedelta(hours=4))
        assert 45.0 < morning.azimuth_deg < 135.0
        assert 225.0 < afternoon.azimuth_deg < 315.0

    def test_night_is_below_the_horizon(self):
        noon = _solar_noon(DEMO_LAT, DEMO_LON, datetime(2026, 9, 8, tzinfo=UTC))
        midnight = eph.solar_position(DEMO_LAT, DEMO_LON, noon + timedelta(hours=12))
        assert midnight.elevation_deg < -6.0
        assert midnight.is_civil_daylight is False

    def test_naive_datetimes_are_read_as_utc(self):
        aware = datetime(2026, 9, 8, 21, 30, tzinfo=UTC)
        naive = datetime(2026, 9, 8, 21, 30)
        assert eph.solar_position(DEMO_LAT, DEMO_LON, naive) == eph.solar_position(
            DEMO_LAT, DEMO_LON, aware
        )

    def test_refraction_lifts_the_sun_at_the_horizon(self):
        """A sun geometrically at the horizon still appears above it."""
        day = datetime(2026, 9, 8, tzinfo=UTC)
        start = day.replace(tzinfo=UTC)
        samples = [
            eph.solar_position(DEMO_LAT, DEMO_LON, start + timedelta(minutes=i))
            for i in range(1440)
        ]
        near_horizon = min(samples, key=lambda p: abs(p.elevation_deg))
        # Refraction is worth roughly half a degree down there; it must not be
        # applied in the wrong direction.
        assert eph._refraction_correction(0.0) > 0.4


class TestShadowGeometry:
    def test_shadow_equals_height_at_45_degrees(self):
        assert eph.shadow_length_ratio(45.0) == pytest.approx(1.0)

    def test_shadow_grows_as_the_sun_drops(self):
        ratios = [eph.shadow_length_ratio(e) for e in (60, 45, 30, 15, 5)]
        assert ratios == sorted(ratios)

    def test_shadow_ratio_is_clamped_below_the_horizon(self):
        assert eph.shadow_length_ratio(0.0) == eph.MAX_SHADOW_RATIO
        assert eph.shadow_length_ratio(-10.0) == eph.MAX_SHADOW_RATIO

    def test_shadow_falls_opposite_the_sun(self):
        assert eph.shadow_direction_deg(90.0) == pytest.approx(270.0)
        assert eph.shadow_direction_deg(350.0) == pytest.approx(170.0)

    def test_camera_heading_rotates_the_shadow_into_frame(self):
        """Same sun, camera turned 90 degrees — the shadow reads 90 degrees over."""
        world = eph.shadow_direction_deg(247.0, camera_heading_deg=0.0)
        turned = eph.shadow_direction_deg(247.0, camera_heading_deg=90.0)
        assert (world - turned) % 360.0 == pytest.approx(90.0)

    def test_drift_axis_flips_with_sun_height(self):
        """The advice inverts: length matters low, direction matters high."""
        assert eph.dominant_drift_axis(6.0) == "length"
        assert eph.dominant_drift_axis(55.0) == "direction"

    def test_length_drifts_faster_than_direction_near_sunset(self):
        """The claim behind dominant_drift_axis, checked against the real curve.

        Measured in the units a viewer actually perceives: shadow length in
        object-heights (a shadow going from 0.1x to 0.2x is invisible; 6x to
        12x is a different shot), and bearing in degrees.
        """
        day = datetime(2026, 9, 8, tzinfo=UTC)
        noon = _solar_noon(DEMO_LAT, DEMO_LON, day)

        def drift(t0, minutes=20):
            a = eph.solar_position(DEMO_LAT, DEMO_LON, t0)
            b = eph.solar_position(DEMO_LAT, DEMO_LON, t0 + timedelta(minutes=minutes))
            return (
                abs(b.shadow_len_ratio - a.shadow_len_ratio),
                abs((b.azimuth_deg - a.azimuth_deg + 180) % 360 - 180),
            )

        noon_len, noon_dir = drift(noon)
        dusk_len, dusk_dir = drift(noon + timedelta(hours=5, minutes=20))
        assert dusk_len > noon_len * 10     # length runs away near the horizon
        assert noon_dir > dusk_dir * 3      # bearing swings fastest overhead


class TestColorTemperature:
    def test_warm_at_sunset_cool_overhead(self):
        assert eph.daylight_color_temp_k(1.0) < 3000
        assert eph.daylight_color_temp_k(60.0) > 5000

    def test_monotonic_through_daylight(self):
        temps = [eph.daylight_color_temp_k(e) for e in range(0, 90, 5)]
        assert temps == sorted(temps)


class TestMoon:
    def test_phase_cycles_over_a_synodic_month(self):
        t0 = datetime(2026, 9, 8, tzinfo=UTC)
        assert eph.moon_phase(t0) == pytest.approx(
            eph.moon_phase(t0 + timedelta(days=29.530588853)), abs=0.001
        )

    def test_full_moon_is_fully_illuminated(self):
        t0 = datetime(2026, 9, 8, tzinfo=UTC)
        # Walk to the next full moon and check the illumination agrees.
        for i in range(0, 30 * 24):
            ts = t0 + timedelta(hours=i)
            if abs(eph.moon_phase(ts) - 0.5) < 0.002:
                assert eph.lunar_position(DEMO_LAT, DEMO_LON, ts).illuminated_fraction > 0.99
                break
        else:
            pytest.fail("no full moon within a month")

    def test_elevation_stays_in_range(self):
        t0 = datetime(2026, 9, 8, tzinfo=UTC)
        for i in range(0, 24 * 30, 7):
            pos = eph.lunar_position(DEMO_LAT, DEMO_LON, t0 + timedelta(hours=i))
            assert -90.0 <= pos.elevation_deg <= 90.0
            assert 0.0 <= pos.azimuth_deg < 360.0


class TestSimulatedTide:
    def test_semi_diurnal_period(self):
        """Two highs a day: the level repeats after one M2 cycle."""
        t0 = datetime(2026, 9, 8, tzinfo=UTC)
        assert eph.tide_level_m(t0) == pytest.approx(
            eph.tide_level_m(t0 + timedelta(hours=eph._M2_PERIOD_H)), abs=0.05
        )

    def test_range_stays_within_the_summed_amplitudes(self):
        t0 = datetime(2026, 9, 8, tzinfo=UTC)
        levels = [eph.tide_level_m(t0 + timedelta(minutes=i)) for i in range(0, 60 * 24 * 15)]
        assert max(levels) <= 1.1 + 0.35 + 1e-9
        assert min(levels) >= -(1.1 + 0.35) - 1e-9

    def test_spring_neap_beat_exists(self):
        """M2 and S2 drift in and out of phase — the daily range must vary."""
        t0 = datetime(2026, 9, 8, tzinfo=UTC)
        daily_ranges = []
        for day in range(15):
            samples = [
                eph.tide_level_m(t0 + timedelta(days=day, minutes=i)) for i in range(0, 1440, 10)
            ]
            daily_ranges.append(max(samples) - min(samples))
        assert max(daily_ranges) - min(daily_ranges) > 0.5


class TestSeries:
    def test_row_shape_matches_the_clickhouse_table(self):
        start = datetime(2026, 9, 8, 21, 0, tzinfo=UTC)
        rows = list(
            eph.ephemeris_series(
                "prod_tideline", DEMO_LAT, DEMO_LON, start, start + timedelta(minutes=4)
            )
        )
        assert len(rows) == 5
        assert list(rows[0]) == [
            "ts",
            "production_id",
            "sun_azimuth_deg",
            "sun_elevation_deg",
            "shadow_len_ratio",
            "daylight_color_temp_k",
            "moon_phase",
            "moon_azimuth_deg",
            "tide_level_m",
            "is_civil_daylight",
        ]
        assert rows[0]["ts"].tzinfo is None      # ClickHouse DateTime is naive
        assert rows[0]["production_id"] == "prod_tideline"
        assert isinstance(rows[0]["is_civil_daylight"], int)
