-- CineMeridian — the queries the agent chooses between.
--
-- Every one of these has been run against the loaded scene and returns what it
-- claims to. They are kept here rather than buried in prompts so the agent can
-- be given tested SQL, and so a reader can check the reasoning without running
-- anything.
--
-- The agent is not limited to these. It composes its own follow-ups, and the
-- interesting runs are the ones where it does. These are the starting points.
--
-- Parameters are written as {name:Type} for the ClickHouse parameterised
-- syntax.


-- ─────────────────────────────────────────────────────────────────────────────
-- A. Cross-take drift on cuts that are actually adjacent
--
-- The combinatorial core. Two takes only clash if the edit puts them next to
-- each other, and every edit version puts them somewhere different — which is
-- why this is recomputed per version rather than once at ingest.
--
-- Note it needs no vision at all: capture metadata joined to computed physics
-- is enough to rank the risky joins. Vision is spent later, on the few that
-- survive.

SELECT a.cut_position                                          AS cut_position,
       a.take_id                                               AS take_a,
       b.take_id                                               AS take_b,
       round(abs(ea.sun_azimuth_deg - eb.sun_azimuth_deg), 2)  AS azimuth_delta_deg,
       round(ea.shadow_len_ratio, 2)                           AS shadow_a,
       round(eb.shadow_len_ratio, 2)                           AS shadow_b,
       round(greatest(ea.shadow_len_ratio, eb.shadow_len_ratio)
             / greatest(least(ea.shadow_len_ratio, eb.shadow_len_ratio), 0.01), 2)
                                                               AS shadow_factor,
       abs(dateDiff('day', toDate(ta.started_at), toDate(tb.started_at)))
                                                               AS days_apart
FROM cinemeridian.edit_decisions a
INNER JOIN cinemeridian.edit_decisions b
        ON b.edit_version = a.edit_version
       AND b.cut_position = a.cut_position + 1
INNER JOIN cinemeridian.takes ta ON ta.take_id = a.take_id
INNER JOIN cinemeridian.takes tb ON tb.take_id = b.take_id
INNER JOIN cinemeridian.ephemeris ea
        ON ea.production_id = ta.production_id
       AND ea.ts = toStartOfMinute(toDateTime(ta.started_at))
INNER JOIN cinemeridian.ephemeris eb
        ON eb.production_id = tb.production_id
       AND eb.ts = toStartOfMinute(toDateTime(tb.started_at))
WHERE a.edit_version = {edit_version:String}
ORDER BY shadow_factor DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- B. Monotonic violation — a process running backwards in the cut
--
-- Footprints accumulate. Dew burns off. A tide line moves one way over an
-- afternoon. When the cut order disagrees with the direction, no single frame
-- is wrong and the sequence still cannot be right.
--
-- This is the query that most needs vision, and the one least troubled by
-- vision's inaccuracy: the absolute counts are approximate, the *ordering* is
-- not.

SELECT groupArray(cut_position) AS positions,
       groupArray(take_id)      AS takes,
       groupArray(n)            AS observed_sequence,
       observed_sequence = arraySort(observed_sequence) AS is_monotonic
FROM (
    SELECT e.cut_position, o.take_id, o.numeric_value AS n
    FROM cinemeridian.frame_observations o
    INNER JOIN cinemeridian.edit_decisions e ON e.take_id = o.take_id
    WHERE e.edit_version = {edit_version:String}
      AND o.entity        = {entity:String}
      AND o.attribute     = {attribute:String}
      AND o.monotonic_dir = 'increasing'
    ORDER BY e.cut_position
);


-- ─────────────────────────────────────────────────────────────────────────────
-- C. Perception against physics — catches mis-slated takes
--
-- What the frame shows, against what the sun was doing at the time written on
-- the slate. When they disagree, the slate may be the thing that is wrong.
--
-- The normalisation matters and was arrived at the hard way. Comparing a
-- measured shadow length directly against a computed one does not work: the
-- vision pass underestimates long shadows by roughly forty percent, and that
-- bias is the same size as the error being hunted. Comparing each take against
-- *the median of its own setup* cancels it, because framing is identical
-- within a setup and the bias travels with the framing.
--
-- frame_coverage_pct is the filter that matters here, not confidence. A shadow
-- occupying half a percent of frame produced a confidently reported and wildly
-- wrong measurement in testing.

WITH per_take AS (
    SELECT splitByChar('_', t.take_id)[2]         AS setup_id,
           t.take_id                              AS tid,
           o.numeric_value                        AS observed,
           eph.shadow_len_ratio                   AS predicted_from_slate,
           o.numeric_value / eph.shadow_len_ratio AS bias,
           o.frame_coverage_pct                   AS coverage_pct,
           o.confidence                           AS confidence
    FROM cinemeridian.frame_observations o
    INNER JOIN cinemeridian.takes t ON t.take_id = o.take_id
    INNER JOIN cinemeridian.ephemeris eph
            ON eph.production_id = t.production_id
           AND eph.ts = toStartOfMinute(toDateTime(t.started_at))
    WHERE o.entity     = 'primary_shadow'
      AND o.attribute  = 'length_ratio'
      AND t.source_kind = 'practical'
)
SELECT setup_id,
       tid                                        AS take_id,
       round(observed, 2)                         AS observed,
       round(predicted_from_slate, 2)             AS predicted_from_slate,
       round(bias / median(bias) OVER (PARTITION BY setup_id), 2) AS vs_setup_median,
       round(coverage_pct, 1)                     AS coverage_pct,
       round(confidence, 2)                       AS confidence
FROM per_take
WHERE coverage_pct >= {min_coverage_pct:Float32}
ORDER BY abs(vs_setup_median - 1) DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- D. Match window — when will the sun return to this geometry?
--
-- The prospective half. Because the ephemeris is precomputed by the minute,
-- this is a range scan rather than per-row arithmetic.
--
-- A caution that belongs with the answer: sun geometry repeats when declination
-- repeats, and declination is symmetric about a solstice. A shoot near a
-- solstice finds its match within weeks; a shoot near an equinox may not find
-- one for months, and saying so is more useful than returning nothing.

WITH ref AS (
    SELECT eph.sun_azimuth_deg AS az, eph.sun_elevation_deg AS el
    FROM cinemeridian.takes t
    INNER JOIN cinemeridian.ephemeris eph
            ON eph.production_id = t.production_id
           AND eph.ts = toStartOfMinute(toDateTime(t.started_at))
    WHERE t.take_id = {reference_take:String}
)
SELECT toDate(ts)                        AS day,
       min(ts)                           AS window_opens,
       max(ts)                           AS window_closes,
       count()                           AS minutes,
       round(max(abs(sun_azimuth_deg   - ref.az)), 3) AS worst_azimuth_error_deg,
       round(max(abs(sun_elevation_deg - ref.el)), 3) AS worst_elevation_error_deg
FROM cinemeridian.ephemeris
CROSS JOIN ref
WHERE production_id = {production_id:String}
  AND ts > {search_from:DateTime}
  AND abs(sun_azimuth_deg   - ref.az) <= {azimuth_tolerance_deg:Float32}
  AND abs(sun_elevation_deg - ref.el) <= {elevation_tolerance_deg:Float32}
GROUP BY day
ORDER BY day;


-- ─────────────────────────────────────────────────────────────────────────────
-- E. Asset and LED volume drift — the errors that are invisible by nature
--
-- Nobody can see that a background asset moved from v012 to v013 between
-- render submissions. It is not a perceptual problem at all, which is why a
-- purely visual continuity tool cannot touch it. core_hours_est turns the
-- finding into the only unit a production actually budgets in.

SELECT r.shot_id,
       r.render_version,
       JSONExtractString(r.asset_versions, {asset:String}) AS asset_version,
       r.volume_plate_id,
       r.volume_plate_frame,
       r.core_hours_est
FROM cinemeridian.shot_render_config r
INNER JOIN cinemeridian.takes t ON t.take_id = r.take_id
WHERE t.scene_id = {scene_id:String}
ORDER BY r.shot_id, r.render_version;
