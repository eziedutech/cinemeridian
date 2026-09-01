"""Physics reference for CineMeridian.

Everything a shot's lighting *must* obey, computed from (latitude, longitude,
timestamp) alone. No API calls, no third-party packages, no model inference -
the arithmetic is deterministic, so it belongs here and not in a prompt.

Two of these quantities are astronomy and one is a stand-in:

* Sun position and moon phase/position are real algorithms (NOAA solar
  position; a truncated lunar series) and are accurate to well within the
  tolerance any continuity question needs.
* ``tide_level_m`` is a **simulation** - two harmonic constituents with an
  arbitrary phase reference. It behaves like a tide and is labelled as
  simulated everywhere it surfaces. It is not a prediction for any real coast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# ── Constants ────────────────────────────────────────────────────────────────

#: Shadows run to infinity as the sun touches the horizon. Clamp so the ratio
#: stays a usable number; anything at or past this is reported as "raking".
MAX_SHADOW_RATIO = 20.0

#: Below this solar elevation the shadow direction is unusable for matching:
#: it swings fast and the shadow itself has no defined tip.
RAKING_ELEVATION_DEG = 3.0

_M2_PERIOD_H = 12.4206012  # principal lunar semi-diurnal
_S2_PERIOD_H = 12.0        # principal solar semi-diurnal


@dataclass(frozen=True)
class SolarPosition:
    """Where the sun is, and what that implies for the frame."""

    azimuth_deg: float          # 0 = north, 90 = east, clockwise
    elevation_deg: float        # refraction-corrected, negative below horizon
    shadow_len_ratio: float     # shadow length / object height
    color_temp_k: int           # correlated colour temperature of daylight
    is_civil_daylight: bool     # sun above -6 degrees


@dataclass(frozen=True)
class LunarPosition:
    azimuth_deg: float
    elevation_deg: float
    phase: float                # 0 = new, 0.5 = full, 1 = new again
    illuminated_fraction: float


# ── Time ─────────────────────────────────────────────────────────────────────

def julian_day(dt: datetime) -> float:
    """Julian Day for a timezone-aware (or assumed-UTC) datetime."""
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    y, m = dt.year, dt.month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    day_fraction = (
        dt.hour + dt.minute / 60 + (dt.second + dt.microsecond / 1e6) / 3600
    ) / 24
    return (
        math.floor(365.25 * (y + 4716))
        + math.floor(30.6001 * (m + 1))
        + dt.day
        + day_fraction
        + b
        - 1524.5
    )


def julian_century(jd: float) -> float:
    return (jd - 2451545.0) / 36525.0


# ── Sun - NOAA solar position algorithm ──────────────────────────────────────

def solar_position(lat_deg: float, lon_deg: float, dt: datetime) -> SolarPosition:
    """Sun azimuth and elevation. Longitude is east-positive."""
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    t = julian_century(julian_day(dt))

    # Geometric mean longitude and anomaly of the sun.
    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m = math.radians(mean_anom)
    equation_of_centre = (
        math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m) * 0.000289
    )
    true_long = mean_long + equation_of_centre
    # Apparent longitude: aberration plus the leading nutation term.
    apparent_long = (
        true_long - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))
    )

    mean_obliquity = 23.0 + (
        26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0
    ) / 60.0
    obliquity = mean_obliquity + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))

    declination = math.asin(
        math.sin(math.radians(obliquity)) * math.sin(math.radians(apparent_long))
    )

    # Equation of time, in minutes.
    var_y = math.tan(math.radians(obliquity / 2)) ** 2
    l0 = math.radians(mean_long)
    eq_time = 4 * math.degrees(
        var_y * math.sin(2 * l0)
        - 2 * eccentricity * math.sin(m)
        + 4 * eccentricity * var_y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * var_y * var_y * math.sin(4 * l0)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * m)
    )

    minutes_utc = dt.hour * 60 + dt.minute + dt.second / 60 + dt.microsecond / 6e7
    true_solar_time = (minutes_utc + eq_time + 4 * lon_deg) % 1440.0
    hour_angle = true_solar_time / 4 - 180.0

    lat = math.radians(lat_deg)
    ha = math.radians(hour_angle)
    cos_zenith = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(
        declination
    ) * math.cos(ha)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.acos(cos_zenith)
    elevation = 90.0 - math.degrees(zenith)
    elevation += _refraction_correction(elevation)

    sin_zenith = math.sin(zenith)
    if abs(sin_zenith) < 1e-9 or abs(math.cos(lat)) < 1e-9:
        # Sun overhead, or observer at a pole - azimuth is undefined.
        azimuth = 180.0
    else:
        cos_az = ((math.sin(lat) * cos_zenith) - math.sin(declination)) / (
            math.cos(lat) * sin_zenith
        )
        cos_az = max(-1.0, min(1.0, cos_az))
        az = math.degrees(math.acos(cos_az))
        azimuth = (az + 180.0) % 360.0 if hour_angle > 0 else (540.0 - az) % 360.0

    return SolarPosition(
        azimuth_deg=azimuth,
        elevation_deg=elevation,
        shadow_len_ratio=shadow_length_ratio(elevation),
        color_temp_k=daylight_color_temp_k(elevation),
        is_civil_daylight=elevation > -6.0,
    )


def _refraction_correction(elevation_deg: float) -> float:
    """Atmospheric refraction in degrees, per the NOAA approximation."""
    if elevation_deg > 85.0:
        return 0.0
    e = math.radians(elevation_deg)
    tan_e = math.tan(e)
    if elevation_deg > 5.0:
        corr = 58.1 / tan_e - 0.07 / tan_e**3 + 0.000086 / tan_e**5
    elif elevation_deg > -0.575:
        corr = 1735.0 + elevation_deg * (
            -518.2
            + elevation_deg * (103.4 + elevation_deg * (-12.79 + elevation_deg * 0.711))
        )
    else:
        corr = -20.774 / tan_e
    return corr / 3600.0


# ── What the sun does to the frame ───────────────────────────────────────────

def shadow_length_ratio(elevation_deg: float) -> float:
    """Shadow length as a multiple of object height: cot(elevation).

    This is the half of the physics that misleads people. The ratio is nearly
    flat through the middle of the day and then explodes as the sun drops -
    which is why a twenty-minute gap is harmless at noon and ruinous at 17:40.
    """
    if elevation_deg <= 0.0:
        return MAX_SHADOW_RATIO
    ratio = 1.0 / math.tan(math.radians(elevation_deg))
    return min(ratio, MAX_SHADOW_RATIO)


def shadow_direction_deg(sun_azimuth_deg: float, camera_heading_deg: float = 0.0) -> float:
    """Direction a shadow points, measured in the camera's frame.

    Shadows fall opposite the sun. Subtracting the camera heading turns a
    compass bearing into something an observer - human or Gemini - can
    actually read off a single frame, which is the only thing they can see.
    """
    return (sun_azimuth_deg + 180.0 - camera_heading_deg) % 360.0


def daylight_color_temp_k(elevation_deg: float) -> int:
    """Correlated colour temperature of daylight at a given sun elevation.

    Piecewise fit over the range that matters on a set: warm and low at
    sunset, climbing to overcast-daylight values overhead.
    """
    if elevation_deg <= -6.0:
        return 12000   # deep twilight, sky-only illumination
    if elevation_deg < 0.0:
        return int(round(9000 - (elevation_deg + 6.0) / 6.0 * 2500))
    if elevation_deg < 10.0:
        return int(round(2000 + elevation_deg / 10.0 * 2600))
    if elevation_deg < 30.0:
        return int(round(4600 + (elevation_deg - 10.0) / 20.0 * 900))
    return int(round(min(5800.0, 5500 + (elevation_deg - 30.0) / 60.0 * 300)))


def dominant_drift_axis(elevation_deg: float) -> str:
    """Which shadow property drifts faster right now: ``"length"`` or ``"direction"``.

    Same physics, opposite advice depending on the hour. Near the horizon the
    length runs away (cot blows up); high in the sky the bearing swings fastest
    while the length barely moves. The agent needs to warn about the right one.
    """
    return "length" if elevation_deg < 20.0 else "direction"


# ── Moon - truncated lunar series ────────────────────────────────────────────

def lunar_position(lat_deg: float, lon_deg: float, dt: datetime) -> LunarPosition:
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    jd = julian_day(dt)
    d = jd - 2451545.0

    # Mean elements.
    mean_long = math.radians((218.316 + 13.176396 * d) % 360.0)
    mean_anom = math.radians((134.963 + 13.064993 * d) % 360.0)
    mean_dist = math.radians((93.272 + 13.229350 * d) % 360.0)

    ecliptic_long = mean_long + math.radians(6.289) * math.sin(mean_anom)
    ecliptic_lat = math.radians(5.128) * math.sin(mean_dist)

    obliquity = math.radians(23.4397)
    ra = math.atan2(
        math.sin(ecliptic_long) * math.cos(obliquity)
        - math.tan(ecliptic_lat) * math.sin(obliquity),
        math.cos(ecliptic_long),
    )
    dec = math.asin(
        math.sin(ecliptic_lat) * math.cos(obliquity)
        + math.cos(ecliptic_lat) * math.sin(obliquity) * math.sin(ecliptic_long)
    )

    # Greenwich mean sidereal time -> local hour angle.
    gmst = math.radians((280.16 + 360.9856235 * d) % 360.0)
    hour_angle = gmst + math.radians(lon_deg) - ra

    lat = math.radians(lat_deg)
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(
        hour_angle
    )
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
    azimuth = (
        math.degrees(
            math.atan2(
                math.sin(hour_angle),
                math.cos(hour_angle) * math.sin(lat) - math.tan(dec) * math.cos(lat),
            )
        )
        + 180.0
    ) % 360.0

    phase = moon_phase(dt)
    return LunarPosition(
        azimuth_deg=azimuth,
        elevation_deg=elevation,
        phase=phase,
        illuminated_fraction=(1 - math.cos(2 * math.pi * phase)) / 2,
    )


def moon_phase(dt: datetime) -> float:
    """Phase as a fraction of the synodic month: 0 = new, 0.5 = full."""
    jd = julian_day(dt)
    return ((jd - 2451550.1) / 29.530588853) % 1.0


# ── Tide - SIMULATED, not a prediction ───────────────────────────────────────

def tide_level_m(
    dt: datetime,
    *,
    m2_amplitude_m: float = 1.1,
    s2_amplitude_m: float = 0.35,
    mean_level_m: float = 0.0,
    epoch: datetime | None = None,
) -> float:
    """Simulated tide height in metres above mean level.

    Two harmonic constituents (M2 lunar, S2 solar) summed against a fixed
    epoch. That is enough to produce a plausible semi-diurnal curve with a
    spring/neap beat - a waterline that moves the way an audience expects.

    It is **not** a prediction for any real location, and nothing downstream
    may present it as one.
    """
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    epoch = epoch or datetime(2026, 1, 1, tzinfo=timezone.utc)
    hours = (dt - epoch).total_seconds() / 3600.0
    return (
        mean_level_m
        + m2_amplitude_m * math.cos(2 * math.pi * hours / _M2_PERIOD_H)
        + s2_amplitude_m * math.cos(2 * math.pi * hours / _S2_PERIOD_H)
    )


# ── Bulk precompute - feeds the ClickHouse ``ephemeris`` table ───────────────

def ephemeris_series(
    production_id: str,
    lat_deg: float,
    lon_deg: float,
    start: datetime,
    end: datetime,
    step: timedelta = timedelta(minutes=1),
):
    """Yield one row per step, matching the ``ephemeris`` table column order.

    Precomputing the whole production window turns the match-window query -
    "when will these conditions repeat?" - into a plain range scan instead of
    per-row arithmetic.
    """
    ts = start
    while ts <= end:
        sun = solar_position(lat_deg, lon_deg, ts)
        moon = lunar_position(lat_deg, lon_deg, ts)
        yield {
            "ts": ts.replace(tzinfo=None),
            "production_id": production_id,
            "sun_azimuth_deg": round(sun.azimuth_deg, 4),
            "sun_elevation_deg": round(sun.elevation_deg, 4),
            "shadow_len_ratio": round(sun.shadow_len_ratio, 4),
            "daylight_color_temp_k": sun.color_temp_k,
            "moon_phase": round(moon.phase, 4),
            "moon_azimuth_deg": round(moon.azimuth_deg, 4),
            "tide_level_m": round(tide_level_m(ts), 4),
            "is_civil_daylight": int(sun.is_civil_daylight),
        }
        ts += step
