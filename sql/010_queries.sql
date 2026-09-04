-- CineMeridian - the queries the agent chooses between.
--
-- Every one of these has been run and returns what it claims to. They are kept
-- here rather than buried in prompts so the agent can be given tested SQL, and
-- so a reader can check the reasoning without running anything.
--
-- There are two paths through this project and they use this file
-- differently. Read that before reading the queries.
--
--   * Somebody's own clips, which is what /example and /try both are. The
--     candidates are computed by ONE statement in
--     codes/backpy/app/tools/candidates.py, reproduced at the end of this file
--     as query F, and the agent is handed the rows rather than the SQL. It
--     writes its own follow-ups from there. This is the path a visitor takes
--     and the one the worked example records.
--
--   * The thirty-take demo scene at /scene, which has an edit list, several
--     setups and two cut versions. Queries A to E below are its starting
--     points, and the agent composes from them.
--
-- So A to E describe the deeper scene rather than the front door. Neither is a
-- pretence: both run against the same seven tables, through the same MCP
-- server, as the same restricted user.
--
-- Parameters are written as {name:Type} for the ClickHouse parameterised
-- syntax, except in F, which is built as a string with its three identifiers
-- quoted at the boundary.


-- ─────────────────────────────────────────────────────────────────────────────
-- A. Cross-take drift on cuts that are actually adjacent
--
-- The combinatorial core. Two takes only clash if the edit puts them next to
-- each other, and every edit version puts them somewhere different - which is
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
-- B. Monotonic violation - a process running backwards in the cut
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
-- C. Perception against physics - catches mis-slated takes
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
-- D. Match window - when will the sun return to this geometry?
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
-- E. Asset and LED volume drift - the errors that are invisible by nature
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


-- ──────────────────────────────────────────────────────────────────────────────
-- F. Every candidate in one statement - the query the front door runs
--
-- This is what /example and /try actually execute. Seven kinds of candidate for
-- every join in a cut, unioned into one shape of row, so the agent makes one
-- trip through MCP rather than one per take. That is the argument of the whole
-- project in a single statement: a self-join across the pairs is work a
-- database does in milliseconds and an agent would do badly in thirty round
-- trips.
--
--   sun_moved          how far the sun moved across the join, from capture
--                      times against the ephemeris. No vision at all.
--   drift              anything measured on both sides that changed: the tail
--                      of the outgoing take against the head of the incoming.
--   runs_backwards     one-way things going the wrong way. Footprints do not
--                      unwalk themselves between two shots.
--   slate_vs_sun       the frame disagrees with the clock the file claims,
--                      which is what an export timestamp looks like.
--   direction_vs_sun   the measured shadow bearing against the computed sun.
--   conditions_differ  the light regime changes across the join, and this one
--                      governs the rest: it decides whether the sun can be
--                      used as a clock here at all.
--   one_side_only      measured on one side and missing on the other. Usually
--                      the model not mentioning it; occasionally a prop that
--                      vanished across a cut, which nothing else would catch.
--
-- Reproduced from codes/backpy/app/tools/candidates.py with example
-- identifiers in place. A coverage floor of 1% is applied rather than a
-- confidence floor: confidence came back at 0.90 for a measurement that was
-- sixty-eight degrees wrong, and coverage did not.

WITH
cuts AS (
    SELECT
        ed.cut_position AS pos,
        ed.take_id      AS take_id,
        t.setup_id      AS setup_id,
        t.started_at    AS started_at,
        t.ended_at      AS ended_at
    FROM cinemeridian.edit_decisions AS ed
    INNER JOIN cinemeridian.takes AS t ON ed.take_id = t.take_id
    WHERE ed.edit_version = 'v_example' AND t.scene_id = 'sc_example'
),
joins AS (
    SELECT
        a.pos        AS pos_a,
        a.take_id    AS take_a,
        a.ended_at   AS moment_a,
        b.pos        AS pos_b,
        b.take_id    AS take_b,
        b.started_at AS moment_b
    FROM cuts AS a
    INNER JOIN cuts AS b ON b.pos = a.pos + 1
),
obs AS (
    SELECT
        fo.take_id            AS take_id,
        fo.story_beat         AS story_beat,
        fo.entity             AS entity,
        fo.attribute          AS attribute,
        fo.numeric_value      AS numeric_value,
        fo.value              AS value,
        fo.frame_coverage_pct AS coverage,
        fo.in_focus           AS in_focus,
        fo.monotonic_dir      AS monotonic_dir,
        fo.frame_ts           AS frame_ts,
        fo.frame_uri          AS frame_uri
    FROM cinemeridian.frame_observations AS fo
    WHERE fo.scene_id = 'sc_example'
)

-- 1. How far the sun moved across each join. No vision at all: capture times
--    against the computed ephemeris rank the risky joins on their own.
SELECT
    'sun_moved'                                        AS kind,
    j.take_a                                           AS take_a,
    j.take_b                                           AS take_b,
    'sun'                                              AS entity,
    'geometry'                                         AS attribute,
    toString(round(e1.sun_azimuth_deg, 1))             AS value_a,
    toString(round(e2.sun_azimuth_deg, 1))             AS value_b,
    toString(round(dateDiff('minute', j.moment_a, j.moment_b), 1)) AS gap,
    concat(
        'elevation ', toString(round(e1.sun_elevation_deg, 1)),
        ' to ', toString(round(e2.sun_elevation_deg, 1)),
        ', shadow ', toString(round(e1.shadow_len_ratio, 2)),
        'x to ', toString(round(e2.shadow_len_ratio, 2)), 'x'
    )                                                  AS detail,
    0.0                                                AS coverage,
    1                                                  AS in_focus,
    ''                                                 AS frame_uri
FROM joins AS j
INNER JOIN cinemeridian.ephemeris AS e1
    ON e1.production_id = 'try_example' AND e1.ts = toStartOfMinute(j.moment_a)
INNER JOIN cinemeridian.ephemeris AS e2
    ON e2.production_id = 'try_example' AND e2.ts = toStartOfMinute(j.moment_b)

UNION ALL

-- 2. Anything measured on both sides of a join that changed. The comparison is
--    the tail of the outgoing take against the head of the incoming one,
--    because those are the two frames the audience sees as one moment.
SELECT
    'drift'                       AS kind,
    j.take_a                      AS take_a,
    j.take_b                      AS take_b,
    oa.entity                     AS entity,
    oa.attribute                  AS attribute,
    coalesce(toString(oa.numeric_value), oa.value) AS value_a,
    coalesce(toString(ob.numeric_value), ob.value) AS value_b,
    toString(round(dateDiff('minute', j.moment_a, j.moment_b), 1)) AS gap,
    concat('coverage ', toString(round(oa.coverage, 1)), '% and ',
           toString(round(ob.coverage, 1)), '%')  AS detail,
    least(oa.coverage, ob.coverage)               AS coverage,
    least(oa.in_focus, ob.in_focus)               AS in_focus,
    oa.frame_uri                                  AS frame_uri
FROM joins AS j
INNER JOIN obs AS oa ON oa.take_id = j.take_a AND oa.story_beat = 2
INNER JOIN obs AS ob
    ON ob.take_id = j.take_b
   AND ob.story_beat = 1
   AND ob.entity = oa.entity
   AND ob.attribute = oa.attribute
WHERE
    oa.numeric_value IS NOT NULL
    AND ob.numeric_value IS NOT NULL
    AND abs(ob.numeric_value - oa.numeric_value) > 0.001
    AND least(oa.coverage, ob.coverage) >= 1.0

UNION ALL

-- 3. Things that only accumulate, running backwards. Footprints do not unwalk
--    themselves between two shots, so a count that falls is either a
--    continuity error or a mis-ordered edit.
SELECT
    'runs_backwards'              AS kind,
    j.take_a                      AS take_a,
    j.take_b                      AS take_b,
    oa.entity                     AS entity,
    oa.attribute                  AS attribute,
    toString(oa.numeric_value)    AS value_a,
    toString(ob.numeric_value)    AS value_b,
    toString(round(dateDiff('minute', j.moment_a, j.moment_b), 1)) AS gap,
    concat('declared ', toString(oa.monotonic_dir))                AS detail,
    least(oa.coverage, ob.coverage)                                AS coverage,
    least(oa.in_focus, ob.in_focus)                                AS in_focus,
    oa.frame_uri                                                   AS frame_uri
FROM joins AS j
INNER JOIN obs AS oa ON oa.take_id = j.take_a AND oa.story_beat = 2
INNER JOIN obs AS ob
    ON ob.take_id = j.take_b
   AND ob.story_beat = 1
   AND ob.entity = oa.entity
   AND ob.attribute = oa.attribute
WHERE
    oa.numeric_value IS NOT NULL
    AND ob.numeric_value IS NOT NULL
    AND (
        (toString(oa.monotonic_dir) = 'increasing' AND ob.numeric_value < oa.numeric_value)
     OR (toString(oa.monotonic_dir) = 'decreasing' AND ob.numeric_value > oa.numeric_value)
    )

UNION ALL

-- 4. What the frames show against what the slate says. Any shadow
--    measurement will do, not only a length: the sharpest case is a shadow
--    measured at all at a moment the sun was below the horizon, and no amount
--    of tolerance explains that. It means the timestamp is wrong rather than
--    the shot, and it is exactly what a file stamped with its export time
--    rather than its filming time looks like.
SELECT
    'slate_vs_sun'                       AS kind,
    o.take_id                            AS take_a,
    ''                                   AS take_b,
    o.entity                             AS entity,
    o.attribute                          AS attribute,
    toString(o.numeric_value)            AS value_a,
    -- Only a length has a computed counterpart. A direction or a hardness has
    -- none, and printing the ephemeris shadow length beside them would be
    -- comparing a bearing to a ratio, which is worse than leaving it empty.
    if(toString(o.attribute) = 'length_ratio',
       toString(round(e.shadow_len_ratio, 2)), '')  AS value_b,
    ''                                              AS gap,
    concat('at ', formatDateTime(o.frame_ts, '%Y-%m-%d %H:%i:%S'),
           ' the sun was ', toString(round(e.sun_elevation_deg, 1)), ' degrees up',
           if(e.sun_elevation_deg <= 0,
              ', BELOW THE HORIZON, so nothing here could have cast a shadow',
              ''))                                  AS detail,
    o.coverage                           AS coverage,
    o.in_focus                           AS in_focus,
    o.frame_uri                          AS frame_uri
FROM obs AS o
INNER JOIN cinemeridian.ephemeris AS e
    ON e.production_id = 'try_example' AND e.ts = toStartOfMinute(o.frame_ts)
WHERE
    o.entity = 'primary_shadow'
    AND o.numeric_value IS NOT NULL

UNION ALL

-- 5. How far the shadow swung against how far the sun did. This is the check
--    that survives when a reading came back without a length: a shadow that
--    turned five degrees while the sun moved one has either had the camera
--    moved under it, which is ordinary, or is not at the time it claims, which
--    is not. Both are worth a look, and the arithmetic needs no vision beyond
--    the two directions already measured.
SELECT
    'direction_vs_sun'            AS kind,
    j.take_a                      AS take_a,
    j.take_b                      AS take_b,
    'primary_shadow'              AS entity,
    'swing_vs_sun'                AS attribute,
    toString(round(abs(ob.numeric_value - oa.numeric_value), 1))     AS value_a,
    toString(round(abs(e2.sun_azimuth_deg - e1.sun_azimuth_deg), 1)) AS value_b,
    toString(round(dateDiff('minute', j.moment_a, j.moment_b), 1))   AS gap,
    concat('shadow turned ',
           toString(round(abs(ob.numeric_value - oa.numeric_value), 1)),
           ' degrees while the sun turned ',
           toString(round(abs(e2.sun_azimuth_deg - e1.sun_azimuth_deg), 1)),
           '; a camera move explains this, a wrong time also would')  AS detail,
    least(oa.coverage, ob.coverage)  AS coverage,
    least(oa.in_focus, ob.in_focus)  AS in_focus,
    oa.frame_uri                     AS frame_uri
FROM joins AS j
INNER JOIN obs AS oa
    ON oa.take_id = j.take_a AND oa.story_beat = 2
   AND oa.entity = 'primary_shadow' AND oa.attribute = 'direction_deg'
INNER JOIN obs AS ob
    ON ob.take_id = j.take_b AND ob.story_beat = 1
   AND ob.entity = 'primary_shadow' AND ob.attribute = 'direction_deg'
INNER JOIN cinemeridian.ephemeris AS e1
    ON e1.production_id = 'try_example' AND e1.ts = toStartOfMinute(j.moment_a)
INNER JOIN cinemeridian.ephemeris AS e2
    ON e2.production_id = 'try_example' AND e2.ts = toStartOfMinute(j.moment_b)
WHERE
    oa.numeric_value IS NOT NULL
    AND ob.numeric_value IS NOT NULL

UNION ALL

-- 6. The light itself changing across a join. A room lit by a beam through a
--    window on one side of a cut and by lamps on the other is not a subtle
--    thing an audience might miss; it is two different times of day presented
--    as one moment. This is also the check that decides whether the sun can be
--    used as a clock at all, so its answer governs the four above it.
SELECT
    'conditions_differ'           AS kind,
    j.take_a                      AS take_a,
    j.take_b                      AS take_b,
    oa.entity                     AS entity,
    oa.attribute                  AS attribute,
    oa.value                      AS value_a,
    ob.value                      AS value_b,
    toString(round(dateDiff('minute', j.moment_a, j.moment_b), 1)) AS gap,
    concat('read from the frames themselves, before any file was consulted')  AS detail,
    100.0                         AS coverage,
    1                             AS in_focus,
    ''                            AS frame_uri
FROM joins AS j
INNER JOIN obs AS oa ON oa.take_id = j.take_a AND oa.story_beat = 2
INNER JOIN obs AS ob
    ON ob.take_id = j.take_b
   AND ob.story_beat = 1
   AND ob.entity = oa.entity
   AND ob.attribute = oa.attribute
WHERE
    oa.entity IN ('lighting', 'opening', 'lamps')
    AND oa.value != ob.value

UNION ALL

-- 7. Something measured on one side of a join and not the other. Weaker than
--    the rest and labelled so: with one reading per frame at ingest, a missing
--    row usually means the model did not mention it rather than that the thing
--    was gone. Worth surfacing anyway, because the one time it is real it is a
--    prop that vanished across a cut, and nothing else here would catch that.
SELECT
    'one_side_only'               AS kind,
    j.take_a                      AS take_a,
    j.take_b                      AS take_b,
    oa.entity                     AS entity,
    oa.attribute                  AS attribute,
    coalesce(toString(oa.numeric_value), oa.value)  AS value_a,
    'not measured'                                  AS value_b,
    toString(round(dateDiff('minute', j.moment_a, j.moment_b), 1)) AS gap,
    'weak: one reading per frame, so this is as likely a missed reading as a missing thing' AS detail,
    oa.coverage                   AS coverage,
    oa.in_focus                   AS in_focus,
    oa.frame_uri                  AS frame_uri
FROM joins AS j
INNER JOIN obs AS oa ON oa.take_id = j.take_a AND oa.story_beat = 2
LEFT JOIN obs AS ob
    ON ob.take_id = j.take_b
   AND ob.story_beat = 1
   AND ob.entity = oa.entity
   AND ob.attribute = oa.attribute
WHERE
    ob.take_id = ''
    AND oa.entity NOT IN ('lighting', 'opening', 'lamps')
    AND oa.coverage >= 1.0

ORDER BY kind, take_a, entity, attribute
