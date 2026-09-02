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
    it does - it just stops having an edge.
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


@dataclass(frozen=True)
class InferredCapture:
    """What the sun in a frame says about how the shot was taken."""

    camera_heading_deg: float | None
    heading_uncertainty_deg: float | None
    sun_azimuth_deg: float
    sun_elevation_deg: float
    expected_shadow_length_ratio: float
    observed_shadow_length_ratio: float | None
    length_agreement: float | None      # observed / expected, 1.0 is perfect
    timestamp_trustworthy: bool | None
    note: str


#: How far the vision pass is trusted to read a shadow's bearing, measured
#: against a known answer: about thirteen degrees on frames where the shadow
#: is long enough to have a direction at all. A heading recovered from one
#: frame inherits that, and saying so is the difference between a measurement
#: and a guess.
HEADING_UNCERTAINTY_DEG = 15.0

#: Below this the shadow is a stub under the subject and its bearing means
#: nothing, so no heading is offered at all.
MIN_RATIO_FOR_HEADING = 1.5

#: The vision pass underestimates long shadows by roughly forty percent, so a
#: measured length inside this band of the computed one is agreement, not a
#: discrepancy. Outside it, something is wrong - and in practice that something
#: is usually the timestamp.
LENGTH_AGREEMENT_LOW = 0.45
LENGTH_AGREEMENT_HIGH = 1.8


def infer_capture(
    *,
    captured_at_utc: datetime,
    latitude: float,
    longitude: float,
    observed_shadow_direction_deg: float | None,
    observed_shadow_length_ratio: float | None,
) -> InferredCapture:
    """Recover what the file does not carry, from the shadow it does.

    A video file usually knows when it was taken, and a phone usually writes
    where. Neither ever knows which way the camera was pointing, because that
    is a compass bearing somebody would have had to stand on set and record.

    The sun supplies it. A shadow falls opposite the sun, so its bearing in
    frame is the solar azimuth turned by the camera's heading:

        shadow_in_frame = (sun_azimuth + 180 - camera_heading) mod 360

    With time and position known the azimuth is computed, which leaves the
    heading as the only unknown in an equation with one observation. That is
    the piece of a camera report this can produce and a camera cannot.

    The same arithmetic run the other way checks the timestamp. Shadow length
    is the cotangent of solar elevation, so the time on the file predicts a
    length; if the frame disagrees by more than the vision pass's own error,
    the more likely explanation is that the timestamp is wrong.

    Deliberately not attempted: solving for latitude and longitude. It is real
    celestial navigation and it would be a good story, but elevation here comes
    from arctan(1/ratio) and the ratio carries roughly forty percent error,
    which at a ratio of two puts the elevation out by ten degrees and the
    position out by hundreds of kilometres. Good enough to sanity check,
    nowhere near good enough to locate.
    """
    sun = eph.solar_position(latitude, longitude, captured_at_utc)

    heading: float | None = None
    uncertainty: float | None = None
    if (
        observed_shadow_direction_deg is not None
        and observed_shadow_length_ratio is not None
        and observed_shadow_length_ratio >= MIN_RATIO_FOR_HEADING
    ):
        heading = (sun.azimuth_deg + 180.0 - observed_shadow_direction_deg) % 360.0
        uncertainty = HEADING_UNCERTAINTY_DEG

    agreement: float | None = None
    trustworthy: bool | None = None
    if observed_shadow_length_ratio:
        agreement = observed_shadow_length_ratio / max(sun.shadow_len_ratio, 1e-6)
        trustworthy = LENGTH_AGREEMENT_LOW <= agreement <= LENGTH_AGREEMENT_HIGH

    # Order matters here, and getting it wrong once produced exactly the wrong
    # answer: a frame whose timestamp was two hours out reported "the shadow is
    # too short to have a direction", because the heading branch ran first. A
    # disagreeing timestamp is the most important thing this function can say
    # and it does not depend on having recovered a heading, so it goes near the
    # top.
    if sun.elevation_deg <= 0:
        note = (
            "The sun is below the horizon at the time on this file. Either the "
            "timestamp is wrong or this was not shot in daylight, and no shadow "
            "measurement can be reconciled with it."
        )
    elif trustworthy is False:
        note = (
            f"The shadow is {agreement:.1f} times the length the file's timestamp "
            f"predicts. The frame and the timestamp disagree, and the timestamp is "
            f"the easier of the two to get wrong."
        )
    elif observed_shadow_length_ratio is None:
        note = (
            "No shadow was measured in this frame, so there is nothing here to "
            "recover a heading from or to check the timestamp against."
        )
    elif observed_shadow_direction_deg is None:
        note = (
            "The shadow's length was measured but not its direction, so the "
            "timestamp checks out but no camera heading can be recovered."
        )
    elif heading is None:
        note = (
            "The shadow is too short to have a reliable direction, so no camera "
            "heading is offered. Colour temperature and counts still compare."
        )
    else:
        note = (
            f"Camera heading recovered from the shadow: {heading:.0f} degrees, give "
            f"or take {uncertainty:.0f}. Nothing in the file carries this."
        )

    return InferredCapture(
        camera_heading_deg=None if heading is None else round(heading, 1),
        heading_uncertainty_deg=uncertainty,
        sun_azimuth_deg=round(sun.azimuth_deg, 2),
        sun_elevation_deg=round(sun.elevation_deg, 2),
        expected_shadow_length_ratio=round(sun.shadow_len_ratio, 3),
        observed_shadow_length_ratio=observed_shadow_length_ratio,
        length_agreement=None if agreement is None else round(agreement, 2),
        timestamp_trustworthy=trustworthy,
        note=note,
    )


#: How far the ratio of two measured shadow lengths may sit from the ratio the
#: sun requires before the cut is called suspect.
#:
#: This band is tighter than the single-frame one above, and deliberately so.
#: A lone measurement carries the vision pass's whole error, since it reads long
#: shadows about forty percent short, so judging it needs a wide band. Compare
#: two frames instead and that bias appears in both halves of the fraction and
#: cancels. What is left is the noise, not the bias, and a narrower band is
#: honest. This is the same reason the rest of the system compares takes with
#: takes rather than against absolute truth.
CUT_RATIO_LOW = 0.60
CUT_RATIO_HIGH = 1.67


@dataclass(frozen=True)
class CutVerdict:
    """Whether the light on two shots agrees that they cut together."""

    verdict: str                              # matched, suspect, unmeasurable
    headline: str
    detail: str
    minutes_apart: float
    sun_elevation_change_deg: float
    sun_azimuth_change_deg: float
    expected_length_ratio: float | None       # what the sun requires, B over A
    observed_length_ratio: float | None       # what the frames show, B over A
    ratio_agreement: float | None             # observed over expected, 1.0 is perfect
    camera_heading_change_deg: float | None
    detectable_from_minutes: float | None     # smallest timing error this could see


def compare_cut(
    *,
    outgoing: InferredCapture,
    incoming: InferredCapture,
    outgoing_at_utc: datetime,
    incoming_at_utc: datetime,
    latitude: float,
    longitude: float,
) -> CutVerdict:
    """Does the light on the last moment of one shot match the first of the next?

    This is the question the whole project exists to answer, asked of two
    ordinary files rather than of a shot library. The outgoing frame is the
    final moment of the shot being cut away from; the incoming frame is the
    first moment of the shot being cut to. On screen those two moments are
    adjacent, so an audience reads them as one continuous instant, and the sun
    has to agree.

    The test that carries the weight is the ratio of shadow lengths. Solar
    elevation fixes what that ratio must be between the two moments the files
    claim, and the measurement's own bias divides out of it, so a disagreement
    here is a disagreement about time itself: two shots presented as continuous
    that were in fact lit hours apart.

    Bearing is reported but never used to condemn a cut. A shadow's direction
    in frame mixes the sun's bearing with the camera's, and a camera is
    expected to move between shots, since that is what coverage is. So the
    bearings are turned into the heading change and handed over as a fact about
    the staging, not as evidence of a fault.
    """
    minutes = (incoming_at_utc - outgoing_at_utc).total_seconds() / 60.0
    elevation_change = incoming.sun_elevation_deg - outgoing.sun_elevation_deg
    azimuth_change = (
        incoming.sun_azimuth_deg - outgoing.sun_azimuth_deg + 180.0
    ) % 360.0 - 180.0

    heading_change: float | None = None
    if outgoing.camera_heading_deg is not None and incoming.camera_heading_deg is not None:
        heading_change = round(
            (incoming.camera_heading_deg - outgoing.camera_heading_deg + 180.0) % 360.0
            - 180.0,
            1,
        )

    floor = detection_floor_minutes(
        outgoing_at_utc=outgoing_at_utc,
        incoming_at_utc=incoming_at_utc,
        latitude=latitude,
        longitude=longitude,
    )

    common = {
        "detectable_from_minutes": floor,
        "minutes_apart": round(minutes, 1),
        "sun_elevation_change_deg": round(elevation_change, 2),
        "sun_azimuth_change_deg": round(azimuth_change, 2),
        "camera_heading_change_deg": heading_change,
    }

    # Below the horizon there is no shadow to reason about, and pretending
    # otherwise would produce a confident number out of nothing.
    if outgoing.sun_elevation_deg <= 0 or incoming.sun_elevation_deg <= 0:
        return CutVerdict(
            verdict="unmeasurable",
            headline="The sun is down in at least one of these frames.",
            detail=(
                "Shadow geometry needs the sun above the horizon. At the times "
                "these files claim, it is not, so there is nothing here to check. "
                "Whatever is lighting these shots, it is not the sun."
            ),
            expected_length_ratio=None,
            observed_length_ratio=None,
            ratio_agreement=None,
            **common,
        )

    expected = incoming.expected_shadow_length_ratio / outgoing.expected_shadow_length_ratio
    out_len = outgoing.observed_shadow_length_ratio
    in_len = incoming.observed_shadow_length_ratio

    if not out_len or not in_len:
        if not out_len and not in_len:
            missing = "either frame"
        elif not out_len:
            missing = "the outgoing frame"
        else:
            missing = "the incoming frame"
        return CutVerdict(
            verdict="unmeasurable",
            headline=f"No shadow could be measured in {missing}.",
            detail=(
                "Nothing in view cast a shadow the vision pass could measure, so "
                "there is no length to compare. This is ordinary on close shots, "
                "in overcast light, and indoors. It is not a finding either way. "
                "The check simply did not run."
            ),
            expected_length_ratio=round(expected, 3),
            observed_length_ratio=None,
            ratio_agreement=None,
            **common,
        )

    observed = in_len / out_len
    agreement = observed / expected

    if CUT_RATIO_LOW <= agreement <= CUT_RATIO_HIGH:
        # A pass is only worth as much as the test's reach, so the reach is
        # stated in the same breath. Without it "consistent" would sound like
        # a clean bill of health on a cut where nothing could have failed.
        if floor is None:
            return CutVerdict(
                verdict="unmeasurable",
                headline="At this time of day the check has no reach.",
                detail=(
                    "Shadow length barely moves around the middle of the day, and "
                    "at these two moments it moves less than the measurement can "
                    "resolve, whatever the timestamps said. Nothing separates a "
                    "right time from a wrong one here, so there is no verdict to "
                    "give. Frames closer to sunrise or sunset would carry one."
                ),
                expected_length_ratio=round(expected, 3),
                observed_length_ratio=round(observed, 3),
                ratio_agreement=round(agreement, 3),
                **common,
            )
        return CutVerdict(
            verdict="matched",
            headline="The light across this cut is consistent.",
            detail=(
                f"The sun requires the shadows to change by a factor of "
                f"{expected:.2f} over the {abs(minutes):.1f} minutes these files "
                f"claim; they change by {observed:.2f}. Within the measurement's "
                f"own error, these two moments are lit by the same afternoon. "
                f"What that is worth: a timestamp here would have to be wrong by "
                f"more than {floor:.0f} minutes before this test could see it, so "
                f"a smaller error is not ruled out, it is invisible."
            ),
            expected_length_ratio=round(expected, 3),
            observed_length_ratio=round(observed, 3),
            ratio_agreement=round(agreement, 3),
            **common,
        )

    direction = "longer" if observed > expected else "shorter"
    return CutVerdict(
        verdict="suspect",
        headline="The light across this cut does not agree with the clock.",
        detail=(
            f"The timestamps put these moments {abs(minutes):.1f} minutes apart, "
            f"which requires the shadows to change by a factor of {expected:.2f}. "
            f"They change by {observed:.2f}, {direction} than the sun allows. "
            f"Either these shots were not filmed when the files say they were, or "
            f"they were not filmed on the same day. Cut together they will read as "
            f"one instant, and the shadows will say otherwise."
        ),
        expected_length_ratio=round(expected, 3),
        observed_length_ratio=round(observed, 3),
        ratio_agreement=round(agreement, 3),
        **common,
    )


def detection_floor_minutes(
    *,
    outgoing_at_utc: datetime,
    incoming_at_utc: datetime,
    latitude: float,
    longitude: float,
    limit_minutes: int = 720,
) -> float | None:
    """How wrong a timestamp would have to be before this cut could show it.

    A comparison that cannot fail is not a pass, and this is the number that
    says which one just happened. Shadow length changes slowly around the
    middle of the day and quickly near the horizon, so the same seven minute
    cut is a searching test in the last hour of light and no test at all at
    noon. Reporting "consistent" without saying which of those it was would be
    offering an assurance the measurement never actually made.

    So: hold the outgoing frame still, slide the incoming one, and find the
    smallest error that would push the required change outside the band the
    verdict uses. Below that, a wrong timestamp is indistinguishable from a
    right one, and the honest word is inconclusive rather than matched.

    Returns None when no error of any size inside the search window would show,
    which happens when both moments sit where the sun's height barely moves.
    """
    reference = _expected_ratio(outgoing_at_utc, incoming_at_utc, latitude, longitude)
    if reference is None:
        return None

    for minutes in range(1, limit_minutes + 1):
        for direction in (1, -1):
            shifted = _expected_ratio(
                outgoing_at_utc,
                incoming_at_utc + timedelta(minutes=direction * minutes),
                latitude,
                longitude,
            )
            if shifted is None:
                continue
            drift = shifted / reference
            if drift < CUT_RATIO_LOW or drift > CUT_RATIO_HIGH:
                return float(minutes)
    return None


def _expected_ratio(
    outgoing_at_utc: datetime,
    incoming_at_utc: datetime,
    latitude: float,
    longitude: float,
) -> float | None:
    """Shadow length at the incoming moment over the outgoing one."""
    out = eph.solar_position(latitude, longitude, outgoing_at_utc)
    into = eph.solar_position(latitude, longitude, incoming_at_utc)
    if out.elevation_deg <= 0 or into.elevation_deg <= 0:
        return None
    if not out.shadow_len_ratio:
        return None
    return into.shadow_len_ratio / out.shadow_len_ratio
