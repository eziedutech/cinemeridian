"""The forward-looking half: solve the same physics for a different unknown.

A continuity tool that only says "these two shots do not match" is a critic.
The interesting move is to run the arithmetic the other way.

    practical shot  ->  solve for *when*   ->  the window in which the sun
                                               returns to this geometry
    CG shot         ->  solve for *how much* ->  the key light values that
                                               will match the plate

Same ephemeris, two directions. That is why supporting CG costs almost nothing
here: the hard part was already done for the retrospective case.

Nothing in this module talks to a model. Prescribing a light rig is arithmetic,
and arithmetic belongs in code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import ephemeris as eph

UTC = timezone.utc


@dataclass(frozen=True)
class LightRig:
    """Key light values for a CG shot that has to cut against a practical one."""

    key_light_azimuth_deg: float
    key_light_elevation_deg: float
    key_light_temp_k: int
    key_light_softness: float
    shadow_direction_deg: float
    shadow_length_ratio: float
    reference_take_id: str
    reference_time: str
    note: str


@dataclass(frozen=True)
class MatchWindow:
    """A stretch of time when conditions return to a reference take's."""

    opens_at: str
    closes_at: str
    minutes: int
    max_azimuth_error_deg: float
    max_elevation_error_deg: float


def prescribe_light_rig(
    *,
    reference_take_id: str,
    reference_time_utc: datetime,
    latitude: float,
    longitude: float,
    camera_heading_deg: float,
    cloud_cover_pct: float = 0.0,
) -> LightRig:
    """The key light that matches a given practical take.

    Cloud cover feeds softness rather than direction: an overcast sky spreads
    the source without moving it, so the shadow still falls where the sun says
    it does — it just stops having an edge.
    """
    sun = eph.solar_position(latitude, longitude, reference_time_utc)
    softness = min(0.95, 0.15 + cloud_cover_pct / 100.0 * 0.8)

    if sun.elevation_deg < eph.RAKING_ELEVATION_DEG:
        note = (
            "The sun is within a few degrees of the horizon. Shadow length is "
            "changing faster than anything else in the frame, so match the "
            "reference time closely rather than the rig values alone."
        )
    elif eph.dominant_drift_axis(sun.elevation_deg) == "length":
        note = (
            "Past the point where length is the fragile axis: hold the "
            "elevation precisely, and treat azimuth as the looser of the two."
        )
    else:
        note = (
            "Sun is high, so bearing drifts fastest. Hold the azimuth "
            "precisely; small elevation errors will not read."
        )

    return LightRig(
        key_light_azimuth_deg=round(sun.azimuth_deg, 2),
        key_light_elevation_deg=round(sun.elevation_deg, 2),
        key_light_temp_k=sun.color_temp_k,
        key_light_softness=round(softness, 2),
        shadow_direction_deg=round(
            eph.shadow_direction_deg(sun.azimuth_deg, camera_heading_deg), 2
        ),
        shadow_length_ratio=round(sun.shadow_len_ratio, 3),
        reference_take_id=reference_take_id,
        reference_time=reference_time_utc.strftime("%Y-%m-%d %H:%M:%S"),
        note=note,
    )


def rig_error(
    *,
    submitted_azimuth_deg: float,
    submitted_elevation_deg: float,
    submitted_temp_k: int,
    prescribed: LightRig,
) -> dict[str, float]:
    """How far a submitted render config is from what the physics requires."""
    azimuth_error = abs(
        (submitted_azimuth_deg - prescribed.key_light_azimuth_deg + 180) % 360 - 180
    )
    return {
        "azimuth_error_deg": round(azimuth_error, 2),
        "elevation_error_deg": round(
            abs(submitted_elevation_deg - prescribed.key_light_elevation_deg), 2
        ),
        "temp_error_k": abs(submitted_temp_k - prescribed.key_light_temp_k),
    }


def find_match_windows(
    *,
    reference_time_utc: datetime,
    latitude: float,
    longitude: float,
    search_from: datetime,
    search_to: datetime,
    azimuth_tolerance_deg: float = 0.5,
    elevation_tolerance_deg: float = 0.5,
    step: timedelta = timedelta(minutes=1),
    max_windows: int = 12,
) -> list[MatchWindow]:
    """When will the sun return to this geometry?

    Computed locally rather than queried, so the same answer is available
    before the ephemeris table has been populated for a future range.

    A caution worth carrying into scheduling advice: sun geometry repeats when
    declination repeats, and declination is symmetric about a solstice. A shoot
    a few weeks from a solstice finds its match a few weeks the other side. A
    shoot near an equinox may not find one for months, and the honest answer
    then is that there is no pickup window this season.
    """
    reference = eph.solar_position(latitude, longitude, reference_time_utc)

    windows: list[MatchWindow] = []
    current_start: datetime | None = None
    worst_azimuth = 0.0
    worst_elevation = 0.0
    ts = search_from

    while ts <= search_to:
        sun = eph.solar_position(latitude, longitude, ts)
        azimuth_error = abs(
            (sun.azimuth_deg - reference.azimuth_deg + 180) % 360 - 180
        )
        elevation_error = abs(sun.elevation_deg - reference.elevation_deg)
        inside = (
            azimuth_error <= azimuth_tolerance_deg
            and elevation_error <= elevation_tolerance_deg
        )

        if inside:
            if current_start is None:
                current_start, worst_azimuth, worst_elevation = ts, 0.0, 0.0
            worst_azimuth = max(worst_azimuth, azimuth_error)
            worst_elevation = max(worst_elevation, elevation_error)
        elif current_start is not None:
            windows.append(
                MatchWindow(
                    opens_at=current_start.strftime("%Y-%m-%d %H:%M:%S"),
                    closes_at=(ts - step).strftime("%Y-%m-%d %H:%M:%S"),
                    minutes=int((ts - step - current_start).total_seconds() // 60) + 1,
                    max_azimuth_error_deg=round(worst_azimuth, 3),
                    max_elevation_error_deg=round(worst_elevation, 3),
                )
            )
            current_start = None
            if len(windows) >= max_windows:
                break
        ts += step

    return windows
