"""Every contradiction worth looking at, in one query.

The agent used to find these itself, and finding them cost sixteen round trips
to the model: list the tables, read the cut list, read the takes, read the
observations, read the ephemeris, join them by hand. Twenty calls at roughly
eighteen seconds each is six minutes, and almost none of that was judgement.

None of it needs a model either. Which takes end up adjacent, how far the sun
moved between them, which measurements disagree across a join and which
accumulating thing runs backwards are all joins over rows we already hold. A
database is better at that than a language model, faster by four orders of
magnitude, and it gives the same answer every time.

So the arithmetic is done here and handed over, and the agent is left with the
part that actually needs it: deciding which of these an editor should be shown,
looking at the frames where looking would settle it, and saying why it dismissed
the rest. That is the work, and it was never the bottleneck.

What this deliberately does not do is decide anything. Every row it emits is a
candidate, not a finding, and several of them will be nothing.
"""

from __future__ import annotations

import ast
import json
from typing import Any

#: A measurement covering less of the frame than this is not something an
#: audience could notice, and the demo scene measured what happens when you
#: trust one anyway: a sixty-eight degree error reported at 0.90 confidence.
#: Coverage is the filter worth having; confidence is not.
MIN_COVERAGE_PCT = 1.0


def candidate_query(*, edit_version: str, scene_id: str, production_id: str) -> str:
    """One statement, four kinds of candidate, one shape of row.

    Written as a union rather than four queries so the whole thing is a single
    trip through MCP. Every column is aliased explicitly: this project has
    already been bitten by ClickHouse returning `t.take_id` where the caller
    expected `take_id`, and by a JOIN quietly renaming half the result.
    """
    edit = _text(edit_version)
    scene = _text(scene_id)
    production = _text(production_id)

    return f"""
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
    WHERE ed.edit_version = {edit} AND t.scene_id = {scene}
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
    WHERE fo.scene_id = {scene}
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
    ON e1.production_id = {production} AND e1.ts = toStartOfMinute(j.moment_a)
INNER JOIN cinemeridian.ephemeris AS e2
    ON e2.production_id = {production} AND e2.ts = toStartOfMinute(j.moment_b)

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
    AND least(oa.coverage, ob.coverage) >= {MIN_COVERAGE_PCT}

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
    ON e.production_id = {production} AND e.ts = toStartOfMinute(o.frame_ts)
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
    ON e1.production_id = {production} AND e1.ts = toStartOfMinute(j.moment_a)
INNER JOIN cinemeridian.ephemeris AS e2
    ON e2.production_id = {production} AND e2.ts = toStartOfMinute(j.moment_b)
WHERE
    oa.numeric_value IS NOT NULL
    AND ob.numeric_value IS NOT NULL

ORDER BY kind, take_a, entity, attribute
""".strip()


def as_table(result: str) -> str:
    """Render the query's answer as something a prompt can carry.

    A table rather than JSON, because the agent reads it once and never parses
    it, and because rows of aligned text survive a prompt better than nested
    braces. Falls back to the raw string when the shape is not what was
    expected: a malformed table is a worse thing to hide than to show.
    """
    columns, rows = _rows_from(result)
    if not columns:
        return result[:4000]
    if not rows:
        return "(no candidates: nothing in this cut disagrees with anything else)"

    widths = [
        max(len(column), *(len(str(row[index])) for row in rows))
        for index, column in enumerate(columns)
    ]
    lines = ["  ".join(column.ljust(widths[i]) for i, column in enumerate(columns))]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def count_of(result: str) -> int:
    return len(_rows_from(result)[1])


def _rows_from(result: str) -> tuple[list[str], list[list[Any]]]:
    """Dig the columns and rows out of an MCP tool result.

    The result is the repr of a Python dictionary whose text field holds the
    real answer as a JSON string. Two layers of quoting, and the outer one is
    Python's, so it is read with Python's own reader rather than by hunting for
    braces. An earlier version pattern-matched its way to the JSON and fell over
    the first apostrophe it met, which is a thing model-written text is full of:
    "the camera's heading" was enough to report zero candidates from twelve.

    Nothing here raises. A table that cannot be parsed should cost a prompt its
    summary, not take an investigation down.
    """
    payload: Any = None
    try:
        payload = ast.literal_eval(result.strip())
    except Exception:  # noqa: BLE001
        payload = None

    for candidate in _json_strings(payload):
        try:
            parsed = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(parsed, dict) and "columns" in parsed:
            return list(parsed.get("columns") or []), list(parsed.get("rows") or [])

    return [], []


def _json_strings(payload: Any) -> list[str]:
    """Every string in the envelope that might be the answer.

    The shape of that envelope has already shifted once between mcp-clickhouse
    versions, so rather than reaching for a known path this walks what it was
    given and hands back anything that could be JSON. Cheap, and it survives the
    next rearrangement.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.lstrip().startswith("{"):
                found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    return found


def _text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
