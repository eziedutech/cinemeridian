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
  010_queries.sql            the tested queries, with the reasoning behind them
scripts/
  generate_telemetry.py      simulated environment data (ephemeris + weather)
  generate_production.py     the scene: takes, edits, render configs, answer key
  make_plates.py             base plates, from Gemini image models
  composite_variants.py      the controlled variables, composited exactly
  observe_frames.py          the perception pass
  apply_schema.py            create the schema (setup only)
  create_agent_user.py       the restricted user the agent runs as (setup only)
  load_data.py               load the CSVs (setup only)
  verify_mcp.py              prove ClickHouse is reached through MCP
  run_analysis.py            drive one investigation from the command line
  score_findings.py          score findings against the planted errors
codes/
  backpy/                    FastAPI + Google ADK agent
    app/
      agent.py               the agent, and its mcp-clickhouse toolset
      ephemeris.py           sun/moon/tide maths - pure, no dependencies
      prompts.py             the instruction, and the analysis task
      settings.py            configuration from the environment
      tools/
        vision.py            observe_frame(), adjudicate_pair()
        prescribe.py         light rig and match windows
        audit.py             the finding record
        agent_tools.py       what the agent is actually handed
    tests/
  frontremix/                Remix continuity console
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

Create the schema, then generate and load the simulated production data.
All three are **setup only** — runtime access goes through MCP:

```bash
python scripts/apply_schema.py
python scripts/create_agent_user.py
python scripts/generate_telemetry.py --out data/
python scripts/generate_production.py --out data/
python scripts/load_data.py --data data/
```

Then check that the part the track actually requires works:

```bash
python scripts/verify_mcp.py
```

A ClickHouse Cloud service sleeps when idle, and the first request after that
can take most of a minute. Warm it with `python scripts/apply_schema.py --check`
before a demo.

Render the frames and run the perception pass. This is the slow lane — one
Gemini call per frame, writing observations that the queries then work on:

```bash
python scripts/make_plates.py --setup su01 --candidates 3
python scripts/make_plates.py --rest
python scripts/composite_variants.py --all
python scripts/observe_frames.py --frames assets/frames --out data/ --upload
python scripts/load_data.py --data data/
```

Then run an analysis and score it against the planted errors:

```bash
python scripts/run_analysis.py --edit-version v14
python scripts/score_findings.py --edit-version v14
```

Run the tests:

```bash
python -m pytest codes/backpy
```

Both services locally — pick ports nothing else is using:

```bash
python -m uvicorn app.main:app --port 8090
```

```bash
npm --prefix codes/frontremix run build && CINEMERIDIAN_API_URL=http://127.0.0.1:8090 PORT=3100 npx --prefix codes/frontremix remix-serve codes/frontremix/build/server/index.js
```

## The agent cannot break anything

`mcp-clickhouse` runs read-only unless write access is enabled, and the agent
needs to write its own findings back through the same server it reads with.
That flag is all-or-nothing, so the boundary is a grant instead: the agent
connects as a ClickHouse user with `SELECT` across the database and `INSERT`
into `continuity_findings` alone. `DROP`, `TRUNCATE`, and writes to any other
table are refused by the server. Setup scripts use the admin user, which never
has a model attached to it.

## Measuring honestly

The continuity errors in the demo scene are planted by
`scripts/generate_production.py`, so there is an answer key
(`assets/ground_truth.json`) and "the agent found N of M" is a claim that can
be checked rather than asserted. The key never enters a prompt, a database
table, or a file path — if the answers leak through a path, the score means
nothing.

## What the vision pass can and cannot do

Measured against the answer key, Gemini reads shadow **direction** to within a
few degrees once a shadow is long enough to have one, and **underestimates
extreme lengths by roughly forty percent**. A shadow occupying half a percent of
frame produced a sixty-eight degree error reported at 0.90 confidence — so
`frame_coverage_pct` is the filter to trust, not `confidence`.

None of that sinks the design, and the reason is worth stating plainly: the
system compares takes against takes, never against absolute truth, so a
systematic bias cancels. The one place absolute values matter — a mis-slated
take — is handled by normalising each take against the median of its own setup,
because framing is identical within a setup and the bias travels with framing.

## Measuring honestly

The continuity errors in the demo scene are planted by
`scripts/generate_production.py`, so there is an answer key
(`assets/ground_truth.json`) and "the agent found N of M" is a claim that can
be checked rather than asserted. The key never enters a prompt, a database
table, or a file path — if the answers leak through a path, the score means
nothing.

On the current scene the agent finds **three of five** planted errors, plus one
finding nobody planted that is nevertheless real: a nineteen-degree elevation
jump across a cut. The two it misses are the mis-slated take and an asset
version drift.

## Status

Built and working: the physics engine and its tests, the ClickHouse schema with
the simulated production loaded, the restricted agent user, the synthetic asset
pipeline, the perception pass, the agent's investigation over MCP — verified
locally and inside the container — and the console. Cloud Run deployment is the
remaining step.

## Licence

MIT — see [LICENSE](LICENSE).
