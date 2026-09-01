# CineMeridian

**Continuity intelligence for the shoot and the cut.**

A Gemini agent that guards a film's *physical* continuity — shadow direction and
length, colour temperature, wind, breath vapour, accumulating footprints, cloud,
tide — across takes, across edit versions, and across CG shots.

Built for **Agentic Cinema: The Blockbuster Hackathon**, ClickHouse track.

---

## Where things are

`git init` runs at the root of the working folder, so the repository root is also
the project root. Application code sits under `codes/` rather than at the top
level; `LICENSE` stays at the root because that is what the rules require and
what GitHub reads for the licence badge.

```
LICENSE                      MIT
README.md                    this file
.env.example                 configuration template
sql/
  001_schema.sql             the seven ClickHouse tables
scripts/
  generate_telemetry.py      simulated production data (ephemeris + weather)
codes/
  backpy/                    FastAPI + Google ADK agent
    app/
      ephemeris.py           sun/moon/tide maths - pure, no dependencies
      settings.py            configuration from the environment
    tests/
  frontnext/                 Next.js continuity console
assets/                      synthetic plates and frames
```

## The problem

People are excellent at comparing one pair of shots. Five things defeat them,
and none of them is about eyesight:

- **Combinatorial explosion.** Ten setups by eight takes, and the error only
  matters for the pairs that end up *adjacent in the cut* — which changes every
  time the edit is revised.
- **Time separation.** Coverage for one scene can be shot on day 3 and day 41.
- **Sub-threshold drift.** The audience feels something is wrong without being
  able to point at it.
- **Familiarity blindness.** An editor watches the same scene forty times and
  stops seeing it.
- **Things that are pure data.** Asset-version drift and LED volume state are
  not visible at all.

Continuity *looks* like a vision problem, so people build image comparators, and
those fail — because the real problem is combinatorial. CineMeridian uses vision
only to **turn pixels into facts**, and hands the actual work to an analytical
database.

## How it works

```
Gemini vision  ──►  structured observations  ──►  ClickHouse (via MCP)
   (perception)          (frame_observations)        (the combinatorial work)
                                                              │
computed physics ─────────────────────────────────────────────┤
   (ephemeris.py: sun, moon, simulated tide)                   │
                                                              ▼
                                              contradictions, ranked
                                                              │
                                          Gemini adjudication (targeted)
                                                              │
                                                              ▼
                                        recommendations, for human review
```

Two facts hold this together:

**Physics is the ground truth.** Sun and moon position follow deterministically
from (latitude, longitude, timestamp), so there is a correct answer that nobody
has to be asked for. A useful side effect: a mis-slated take exposes itself,
because its shadows do not match the ephemeris for the time written on the slate.

**One calculation, read two ways.** For a practical shot, solve for *when* —
the window in which conditions will match again, for pickups and reshoots. For a
CG shot, solve for *how much* — the key-light azimuth, elevation and colour
temperature that will match. Same arithmetic, opposite direction.

The agent **only ever recommends.** It does not modify an edit or submit a render
job. Every finding lands in a queue for human review.

## ClickHouse, through MCP

Every runtime query reaches ClickHouse through the `mcp-clickhouse` MCP server,
launched as a stdio subprocess and attached to the ADK agent as a toolset — not
through a database client in application code. The schema is seven tables
(`sql/001_schema.sql`); the `ORDER BY` keys are chosen so that the hot queries —
a self-join of observations of the same story beat across different takes, and a
range scan over precomputed ephemeris — each read a contiguous range.

## Honesty about the data

- All footage is **synthetic and self-made**. No real film or broadcast material
  is used anywhere.
- There is no real production data and no real crew.
- Sun and moon positions are real astronomy (NOAA solar position algorithm).
  **Tide is simulated** — two harmonic constituents against an arbitrary epoch.
  Weather telemetry is simulated too. Neither is a prediction for any real place,
  and nothing in the demo presents them as one.

## Running it

Requires Python 3.12+, `uv`, a Google Cloud project with Vertex AI enabled, and a
ClickHouse Cloud service in the same region (`us-central1`).

```bash
cp .env.example credentials/gcp.env    # then fill in, both files are gitignored
python -m pip install -r codes/backpy/requirements-dev.txt
```

Create the schema (setup only — runtime access goes through MCP):

```bash
clickhouse client --queries-file sql/001_schema.sql
```

Generate the simulated production data:

```bash
python scripts/generate_telemetry.py --out data/
```

Run the tests:

```bash
python -m pytest codes/backpy
```

## Status

Under active development for the hackathon. Working today: the physics engine
and its tests, the ClickHouse schema, and the simulated-data generator. The
agent, the vision tools, the console, and the Cloud Run deployment are in
progress.

## Licence

MIT — see [LICENSE](LICENSE).
