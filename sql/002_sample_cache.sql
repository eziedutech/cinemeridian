-- CineMeridian - what has already been read from the clips we ship.
--
-- The six sample clips are fixed files. What a vision pass measures in them -
-- the bearing of a shadow, how long it is, what is lying on the ground between
-- one shot and the next - is a fact about those pixels, and it does not change
-- because somebody else pressed the button. Reading them again on every visit
-- costs twelve vision calls before the agent has started, and about three
-- minutes of somebody's patience.
--
-- So a run over our own clips writes what it read here, and a later visitor may
-- choose to start from it. Two things this is not. It is not a cache of
-- answers: the ephemeris is recomputed for the time and place given, the
-- candidate query runs again, and the agent investigates from nothing, so two
-- people who pick the same clips can still be told different things. And it
-- never holds a visitor's own footage: every row is keyed by the file name of a
-- clip we ship, and nobody else's file can collide with those.
--
-- ReplacingMergeTree on read_at because a later reading of the same fixed frame
-- supersedes the earlier one rather than accumulating beside it.

-- What one clip's own frames measured: the ingest pass, per clip.
CREATE TABLE IF NOT EXISTS cinemeridian.sample_clip_readings
(
    clip              String,        -- 'woman-1.mp4'
    role              Enum8('head' = 1, 'tail' = 2),
    read_at           DateTime,
    model             LowCardinality(String),
    -- The observations as the vision tool returned them, so the shape can grow
    -- without a migration. Parsed at the edge, never queried into.
    payload           String
)
ENGINE = ReplacingMergeTree(read_at)
ORDER BY (clip, role);

-- What the pair pass said about one join: same place, the light on each side,
-- and the cells that differ. Keyed by the ordered pair, because a cut from the
-- beach to the room is not the same reading as the room to the beach.
CREATE TABLE IF NOT EXISTS cinemeridian.sample_pair_readings
(
    outgoing          String,        -- 'woman-3.mp4'
    incoming          String,        -- 'woman-4.mp4'
    read_at           DateTime,
    model             LowCardinality(String),
    reads             UInt8,         -- how many readings agreed it
    payload           String
)
ENGINE = ReplacingMergeTree(read_at)
ORDER BY (outgoing, incoming);
