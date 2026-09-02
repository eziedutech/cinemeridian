"""Tests for the physics reference.

These check the ephemeris against facts that are true independently of the
implementation - the sun's declination at solstice, the geometry of solar
noon, the shape of the cotangent - rather than against numbers copied out of
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
        """Same sun, camera turned 90 degrees - the shadow reads 90 degrees over."""
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
        """M2 and S2 drift in and out of phase - the daily range must vary."""
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


class TestCaptureInference:
    """Recovering a camera heading from the shadow it casts.

    These are round trips against known answers: take a heading, compute the
    shadow it would produce, hand only the shadow back, and see whether the
    heading comes out again. If the arithmetic is wrong in one direction it is
    wrong in the other, so the test asserts against headings chosen here rather
    than against anything the code produced.
    """

    def _direction_for(self, heading: float, when: datetime) -> tuple[float, float]:
        sun = eph.solar_position(DEMO_LAT, DEMO_LON, when)
        return eph.shadow_direction_deg(sun.azimuth_deg, heading), sun.shadow_len_ratio

    def test_recovers_a_known_camera_heading(self):
        from app.tools.prescribe import infer_capture

        when = datetime(2026, 12, 4, 22, 30, tzinfo=UTC)  # 16:30 local, long shadow
        for heading in (0.0, 90.0, 195.0, 270.0, 348.0):
            direction, ratio = self._direction_for(heading, when)
            result = infer_capture(
                captured_at_utc=when,
                latitude=DEMO_LAT,
                longitude=DEMO_LON,
                observed_shadow_direction_deg=direction,
                observed_shadow_length_ratio=ratio,
            )
            assert result.camera_heading_deg == pytest.approx(heading, abs=0.5)

    def test_declines_a_heading_when_the_shadow_is_a_stub(self):
        """A short shadow has no usable bearing, and saying so beats guessing."""
        from app.tools.prescribe import infer_capture

        when = datetime(2026, 12, 4, 19, 0, tzinfo=UTC)  # 13:00 local, sun high
        direction, ratio = self._direction_for(270.0, when)
        assert ratio < 1.5
        result = infer_capture(
            captured_at_utc=when,
            latitude=DEMO_LAT,
            longitude=DEMO_LON,
            observed_shadow_direction_deg=direction,
            observed_shadow_length_ratio=ratio,
        )
        assert result.camera_heading_deg is None
        assert "too short" in result.note

    def test_a_correct_timestamp_is_trusted(self):
        from app.tools.prescribe import infer_capture

        when = datetime(2026, 12, 4, 22, 30, tzinfo=UTC)
        _, ratio = self._direction_for(270.0, when)
        result = infer_capture(
            captured_at_utc=when,
            latitude=DEMO_LAT,
            longitude=DEMO_LON,
            observed_shadow_direction_deg=None,
            observed_shadow_length_ratio=ratio,
        )
        assert result.timestamp_trustworthy is True

    def test_a_wrong_timestamp_is_caught(self):
        """The planted slate error, run through the inference instead.

        The file says 15:19 local; the frame was shot at 16:29. The shadow at
        the real time is more than twice what the stated time predicts, which
        is far outside what the vision pass's own error could explain.
        """
        from app.tools.prescribe import infer_capture

        real = datetime(2026, 12, 14, 22, 29, tzinfo=UTC)
        claimed = datetime(2026, 12, 14, 21, 19, tzinfo=UTC)
        _, real_ratio = self._direction_for(95.0, real)

        result = infer_capture(
            captured_at_utc=claimed,
            latitude=DEMO_LAT,
            longitude=DEMO_LON,
            observed_shadow_direction_deg=None,
            observed_shadow_length_ratio=real_ratio,
        )
        assert result.timestamp_trustworthy is False
        assert result.length_agreement > 2.0
        assert "timestamp" in result.note

    def test_says_so_when_the_sun_is_down(self):
        from app.tools.prescribe import infer_capture

        result = infer_capture(
            captured_at_utc=datetime(2026, 12, 4, 6, 0, tzinfo=UTC),  # midnight local
            latitude=DEMO_LAT,
            longitude=DEMO_LON,
            observed_shadow_direction_deg=120.0,
            observed_shadow_length_ratio=3.0,
        )
        assert result.sun_elevation_deg < 0
        assert "below the horizon" in result.note


class TestCutComparison:
    """Two clips, cut together. Does the sun agree they are one moment?

    Every case here is built the same way: pick real times and a real place,
    let the ephemeris say what the shadows must be, feed those back in as if a
    camera had recorded them, and check the verdict. Where a case is meant to
    fail, the failure is introduced by lying about *when*, not by inventing an
    impossible shadow, because a wrong time is the mistake an edit actually
    makes.
    """

    LAT = 8.75
    LON = -83.5

    def capture(self, moment, *, measured_ratio=None, bias=1.0, heading=90.0):
        """An InferredCapture as the pipeline would build one for `moment`.

        `bias` stands in for the vision pass reading shadows short: it scales
        the measurement the way the model does in practice, so a test can show
        the bias dividing out of a comparison even while it wrecks an absolute
        reading.
        """
        from app.tools.prescribe import infer_capture

        sun = eph.solar_position(self.LAT, self.LON, moment)
        true_ratio = sun.shadow_len_ratio
        if measured_ratio is not None:
            observed = measured_ratio
        else:
            observed = None if true_ratio is None else true_ratio * bias

        direction = None
        if observed:
            direction = (sun.azimuth_deg + 180.0 - heading) % 360.0

        return infer_capture(
            captured_at_utc=moment,
            latitude=self.LAT,
            longitude=self.LON,
            observed_shadow_direction_deg=direction,
            observed_shadow_length_ratio=observed,
        )

    def test_honest_cut_passes(self):
        """Two shots filmed four minutes apart, labelled four minutes apart."""
        from app.tools.prescribe import compare_cut

        a = datetime(2026, 12, 14, 19, 0, tzinfo=timezone.utc)
        b = a + timedelta(minutes=4)
        result = compare_cut(
            outgoing=self.capture(a),
            incoming=self.capture(b),
            outgoing_at_utc=a,
            incoming_at_utc=b,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "matched"
        assert result.ratio_agreement == pytest.approx(1.0, abs=0.02)

    def test_vision_bias_does_not_break_an_honest_cut(self):
        """The model reads both shadows forty percent short. It still passes.

        This is the whole argument for comparing rather than measuring. Either
        frame judged alone looks badly wrong; judged against each other they
        are exactly right, because the same error is in both.
        """
        from app.tools.prescribe import compare_cut

        a = datetime(2026, 12, 14, 19, 0, tzinfo=timezone.utc)
        b = a + timedelta(minutes=6)
        result = compare_cut(
            outgoing=self.capture(a, bias=0.6),
            incoming=self.capture(b, bias=0.6),
            outgoing_at_utc=a,
            incoming_at_utc=b,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "matched"
        assert result.ratio_agreement == pytest.approx(1.0, abs=0.02)

    def test_shots_filmed_hours_apart_are_caught(self):
        """The cut claims four minutes. The light is three hours of afternoon.

        The incoming frame really was shot at 22:00 and carries 22:00's long
        shadow, but the edit presents it as following 19:04. The comparison is
        against what 19:04 requires, and it does not fit.
        """
        from app.tools.prescribe import compare_cut

        a = datetime(2026, 12, 14, 19, 0, tzinfo=timezone.utc)
        claimed_b = a + timedelta(minutes=4)
        actually_shot = datetime(2026, 12, 14, 22, 0, tzinfo=timezone.utc)

        long_shadow = eph.solar_position(self.LAT, self.LON, actually_shot).shadow_len_ratio

        result = compare_cut(
            outgoing=self.capture(a),
            incoming=self.capture(claimed_b, measured_ratio=long_shadow),
            outgoing_at_utc=a,
            incoming_at_utc=claimed_b,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "suspect"
        assert result.observed_length_ratio > result.expected_length_ratio
        assert "not filmed when the files say" in result.detail

    def test_no_measurable_shadow_is_not_a_finding(self):
        """Silence from the vision pass must not read as a pass or a failure."""
        from app.tools.prescribe import compare_cut

        a = datetime(2026, 12, 14, 19, 0, tzinfo=timezone.utc)
        b = a + timedelta(minutes=4)
        result = compare_cut(
            outgoing=self.capture(a),
            incoming=self.capture(b, measured_ratio=0.0),
            outgoing_at_utc=a,
            incoming_at_utc=b,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "unmeasurable"
        assert "incoming frame" in result.headline
        assert result.ratio_agreement is None

    def test_night_is_declined_rather_than_guessed(self):
        from app.tools.prescribe import compare_cut

        a = datetime(2026, 12, 15, 6, 0, tzinfo=timezone.utc)
        b = a + timedelta(minutes=3)
        result = compare_cut(
            outgoing=self.capture(a),
            incoming=self.capture(b),
            outgoing_at_utc=a,
            incoming_at_utc=b,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "unmeasurable"
        assert "sun is down" in result.headline

    def test_camera_move_is_reported_not_condemned(self):
        """A forty degree pan between shots is staging, not a continuity error.

        Late afternoon deliberately: a heading is only offered when the shadow
        is long enough to have a bearing worth reading, and at midday it is
        not. The refusal is the point of that rule, so this test works with it
        rather than around it.
        """
        from app.tools.prescribe import compare_cut

        a = datetime(2026, 12, 14, 21, 30, tzinfo=timezone.utc)
        b = a + timedelta(minutes=4)
        result = compare_cut(
            outgoing=self.capture(a, heading=90.0),
            incoming=self.capture(b, heading=130.0),
            outgoing_at_utc=a,
            incoming_at_utc=b,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "matched"
        assert result.camera_heading_change_deg == pytest.approx(40.0, abs=1.0)


class TestDetectionFloor:
    """How wrong a timestamp has to be before the shadows could show it.

    Shadow length is the cotangent of solar elevation, so it is nearly flat
    through the middle of the day and steepens sharply towards the horizon.
    The practical consequence is that the same cut is a searching test in the
    last hour of light and almost no test at noon, and a verdict that does not
    say which one it just made is offering an assurance it never earned.
    """

    LAT = 8.75
    LON = -83.5

    def floor(self, hour: int, minute: int = 0, gap_minutes: int = 7):
        from app.tools.prescribe import detection_floor_minutes

        start = datetime(2026, 12, 3, hour, minute, tzinfo=timezone.utc)
        return detection_floor_minutes(
            outgoing_at_utc=start,
            incoming_at_utc=start + timedelta(minutes=gap_minutes),
            latitude=self.LAT,
            longitude=self.LON,
        )

    def test_late_light_sees_far_more_than_midday(self):
        near_dusk = self.floor(22, 40)
        midday = self.floor(18, 0)
        assert near_dusk is not None and midday is not None
        assert near_dusk < midday / 4

    def test_a_pass_says_how_far_it_could_see(self):
        """A verdict of matched must name its own reach, or it overclaims."""
        from app.tools.prescribe import compare_cut, infer_capture

        start = datetime(2026, 12, 3, 22, 40, tzinfo=timezone.utc)
        later = start + timedelta(minutes=7)

        def capture(moment):
            sun = eph.solar_position(self.LAT, self.LON, moment)
            return infer_capture(
                captured_at_utc=moment,
                latitude=self.LAT,
                longitude=self.LON,
                observed_shadow_direction_deg=None,
                observed_shadow_length_ratio=sun.shadow_len_ratio,
            )

        result = compare_cut(
            outgoing=capture(start),
            incoming=capture(later),
            outgoing_at_utc=start,
            incoming_at_utc=later,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert result.verdict == "matched"
        assert result.detectable_from_minutes is not None
        assert f"{result.detectable_from_minutes:.0f} minutes" in result.detail

    def test_the_planted_slate_error_sits_above_its_own_floor(self):
        """The 63 minute error this project plants is one the test can see.

        Worth pinning down, because it is the difference between catching that
        error and merely happening to catch it. If a change to the tolerance
        band ever pushes the floor above 63 minutes, the demo would still pass
        while the check quietly stopped working.
        """
        from app.tools.prescribe import detection_floor_minutes

        outgoing = datetime(2026, 12, 14, 22, 22, 10, tzinfo=timezone.utc)
        incoming = datetime(2026, 12, 14, 21, 19, 10, tzinfo=timezone.utc)
        floor = detection_floor_minutes(
            outgoing_at_utc=outgoing,
            incoming_at_utc=incoming,
            latitude=self.LAT,
            longitude=self.LON,
        )
        assert floor is not None
        assert floor < 63
