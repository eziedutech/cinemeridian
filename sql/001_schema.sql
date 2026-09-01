-- CineMeridian — ClickHouse schema
--
-- Seven tables. The shape of each one is chosen for the question it has to
-- answer at demo speed, so the ORDER BY keys are the design, not decoration:
-- every hot query either scans a contiguous range or self-joins on a prefix.
--
-- Apply with:  clickhouse client --queries-file sql/001_schema.sql
-- (Setup only. At runtime the agent reaches ClickHouse through the
-- mcp-clickhouse MCP server, never through a direct client.)

CREATE DATABASE IF NOT EXISTS cinemeridian;

USE cinemeridian;


-- 1. Capture metadata ────────────────────────────────────────────────────────
-- One row per take. ReplacingMergeTree because slate data gets corrected: a
-- take re-ingested with a fixed timestamp must supersede the wrong one rather
-- than sit beside it.

CREATE TABLE IF NOT EXISTS takes
(
    take_id             String,
    production_id       String,
    scene_id            String,
    setup_id            String,          -- camera angle/position
    take_number         UInt8,
    shoot_day           UInt8,
    unit                LowCardinality(String),
    started_at          DateTime64(3),
    ended_at            DateTime64(3),
    latitude            Float64,
    longitude           Float64,
    camera_heading_deg  Float32,         -- turns a compass bearing into a frame-relative one
    lens_mm             Float32,
    source_kind         Enum8('practical' = 1, 'led_volume' = 2, 'cg_render' = 3),
    slate_verified      UInt8            -- 0 = timestamp not yet checked against the shadows
)
ENGINE = ReplacingMergeTree(ended_at)
ORDER BY (production_id, scene_id, take_id);


-- 2. Environment telemetry ───────────────────────────────────────────────────
-- The firehose: 1 Hz per station for the length of the shoot. Partitioned by
-- day so a single shoot day drops out of the scan, ordered by station then
-- time because every read is "this station, this window".

CREATE TABLE IF NOT EXISTS env_telemetry
(
    ts               DateTime64(3),
    production_id    String,
    station_id       LowCardinality(String),
    wind_dir_deg     Float32,
    wind_speed_ms    Float32,
    temp_c           Float32,
    humidity_pct     Float32,
    dew_point_c      Float32,
    lux              UInt32,
    color_temp_k     UInt16,
    cloud_cover_pct  UInt8
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (production_id, station_id, ts);


-- 3. Computed ephemeris — THE GROUND TRUTH ───────────────────────────────────
-- Precomputed at one-minute resolution from app/ephemeris.py, for the whole
-- production window plus the pickup window ahead of it. Because it is
-- precomputed, "when will these conditions repeat?" is a range scan.
--
-- Note: sun and moon are real astronomy. tide_level_m is a SIMULATION and is
-- labelled as such everywhere it is shown.

CREATE TABLE IF NOT EXISTS ephemeris
(
    ts                     DateTime,
    production_id          String,
    sun_azimuth_deg        Float32,
    sun_elevation_deg      Float32,
    shadow_len_ratio       Float32,   -- cot(elevation), clamped near the horizon
    daylight_color_temp_k  UInt16,
    moon_phase             Float32,   -- 0 = new, 0.5 = full
    moon_azimuth_deg       Float32,
    tide_level_m           Float32,   -- simulated
    is_civil_daylight      UInt8
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (production_id, ts);


-- 4. Perceptual observations — the pixels-to-relational bridge ───────────────
-- Gemini's structured output, one row per (entity, attribute) per sampled
-- frame. Entity/attribute are LowCardinality because the vocabulary is small
-- and closed; the values are what vary.
--
-- ORDER BY puts story_beat before take_id on purpose: the core query joins
-- observations of the *same story moment* across *different takes*, so both
-- sides of that self-join read a contiguous range.

CREATE TABLE IF NOT EXISTS frame_observations
(
    obs_id              String,
    take_id             String,
    scene_id            String,
    story_beat          UInt16,        -- the story moment, so different takes line up
    frame_ts            DateTime64(3),
    frame_uri           String,        -- gs://...
    entity              LowCardinality(String),
    -- 'primary_shadow' | 'hair_a' | 'footprints' | 'background_cloud'
    -- | 'breath_vapour' | 'waterline'
    attribute           LowCardinality(String),
    -- 'direction_deg' | 'length_ratio' | 'count' | 'present'
    -- | 'warmth_k' | 'hardness' | 'height_m'
    value               String,
    numeric_value       Nullable(Float64),
    monotonic_dir       Enum8('none' = 0, 'increasing' = 1, 'decreasing' = 2),
    in_focus            UInt8,
    frame_coverage_pct  Float32,       -- how big in frame, i.e. whether anyone would see it
    confidence          Float32
)
ENGINE = MergeTree
PARTITION BY scene_id
ORDER BY (scene_id, story_beat, entity, attribute, take_id);


-- 5. Edit decisions ──────────────────────────────────────────────────────────
-- The cut order, one version at a time. This is the table that makes the
-- problem combinatorial: the same footage produces different adjacent pairs
-- in every edit version, so every version needs its own hunt.

CREATE TABLE IF NOT EXISTS edit_decisions
(
    edit_version   String,
    cut_position   UInt16,
    take_id        String,
    in_beat        UInt16,
    out_beat       UInt16,
    created_at     DateTime
)
ENGINE = MergeTree
ORDER BY (edit_version, cut_position);


-- 6. Render and virtual-stage configuration — the CG side ────────────────────
-- Replacing on submitted_at: a resubmitted render version replaces its
-- predecessor. core_hours_est is what makes an intercepted mismatch quotable
-- in the only unit a studio cares about.

CREATE TABLE IF NOT EXISTS shot_render_config
(
    shot_id                  String,
    take_id                  String,
    render_version           UInt16,
    key_light_azimuth_deg    Float32,
    key_light_elevation_deg  Float32,
    key_light_temp_k         UInt16,
    key_light_intensity      Float32,
    key_light_softness       Float32,
    volume_plate_id          String,     -- LED volume: the plate playing on the wall
    volume_plate_frame       UInt32,     -- playback index — drift here breaks the background
    volume_brightness_nits   UInt16,
    asset_versions           String,     -- JSON: {"beach_tree":"v013","boat":"v004"}
    submitted_at             DateTime,
    core_hours_est           Float32     -- savings if caught before the render lands
)
ENGINE = ReplacingMergeTree(submitted_at)
ORDER BY (shot_id, render_version);


-- 7. Agent findings — the audit trail ────────────────────────────────────────
-- Written back by the agent through the same MCP server it reads with. Every
-- row keeps the whole chain: what was observed, what physics expected, what
-- Gemini ruled, what to do about it. human_reviewed starts at 0 and only a
-- person moves it — the agent recommends and never acts.

CREATE TABLE IF NOT EXISTS continuity_findings
(
    finding_id            String,
    created_at            DateTime64(3),
    edit_version          String,
    scene_id              String,
    finding_type          LowCardinality(String),
    -- 'monotonic_violation' | 'cross_take_drift' | 'physics_mismatch'
    -- | 'asset_version_drift' | 'volume_plate_drift' | 'slate_error'
    severity              Enum8('info' = 1, 'low' = 2, 'medium' = 3, 'high' = 4),
    take_a                String,
    take_b                String,
    entity                String,
    attribute             String,
    observed_delta        String,
    computed_expectation  String,   -- what the physics said
    gemini_verdict        String,   -- the targeted visual adjudication
    recommendation        String,   -- actionable, and only ever a recommendation
    visible_in_cut        UInt8,
    human_reviewed        UInt8
)
ENGINE = MergeTree
ORDER BY (created_at);
