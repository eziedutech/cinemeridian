import { useState } from "react";
import { json, type LoaderFunctionArgs, type MetaFunction } from "@remix-run/node";
import { Link, useLoaderData } from "@remix-run/react";

import type { Grid, GridDifference } from "~/components/CutComparison";
import { Info } from "~/components/Info";
import { Report } from "~/components/Report";
import { ReportSheet } from "~/components/ReportSheet";
import {
  ResultView,
  shortVersion,
  type Comparison,
  type FindingGroup,
  type ResultFact,
} from "~/components/ResultView";
import type { TimelineEvent } from "~/components/AgentTimeline";
import { apiBase, frameUrl, type Finding } from "~/lib/api";
import example from "~/example.json";

export const meta: MetaFunction = () => [
  { title: "CineMeridian - a worked example" },
];

export async function loader(_args: LoaderFunctionArgs) {
  return json({ apiBase: apiBase(), example });
}

/**
 * One real review, kept.
 *
 * This is not a second product built to look like the page for your own clips.
 * It is a run of that page: six sample clips went through the same browser
 * decode, the same ingest into ClickHouse, the same agent through the same MCP
 * server, and the result was lifted out and committed. Everything below is
 * rendered by the components that page uses, from that recording.
 *
 * The alternative was what stood here before: a scene of thirty synthetic
 * takes with its own vocabulary, its own layout and its own edit versions,
 * which taught a visitor a system they would never meet again. That scene is
 * still worth showing for what it holds - a hundred thousand rows of computed
 * ephemeris and a shooting week of telemetry - so it moved to /scene.
 */
export default function Example() {
  const { apiBase, example } = useLoaderData<typeof loader>();
  const [reportOpen, setReportOpen] = useState(false);

  const findings = example.findings as unknown as Finding[];
  const events = example.events as unknown as TimelineEvent[];
  const frame = (uri: string) => frameUrl(apiBase, uri);

  const comparisons: Array<Comparison & { key: string; from: number; to: number }> =
    example.joins.flatMap((join) => {
      const outgoing = example.takes.find((take) => take.position === join.from);
      const incoming = example.takes.find((take) => take.position === join.to);
      if (!outgoing || !incoming) return [];

      const sceneChange = example.sceneChanges.some(
        (change) => change.from === join.from && change.to === join.to,
      );
      const filed = findings.filter((finding) => belongsTo(finding, join.from, join.to));
      const tone = sceneChange
        ? ("scene" as const)
        : filed.length > 0 || join.differences.length > 0
          ? ("marked" as const)
          : ("clean" as const);

      return [
        {
          key: `${join.from}-${join.to}`,
          from: join.from,
          to: join.to,
          outgoing: frame(outgoing.tail),
          incoming: frame(incoming.head),
          grid: join.grid as Grid,
          differences: join.differences as unknown as GridDifference[],
          fromLabel: `Take ${join.from}`,
          toLabel: `Take ${join.to}`,
          tone,
          status: sceneChange
            ? "a scene change, so not checked for continuity"
            : filed.length > 0
              ? `${filed.length} ${filed.length === 1 ? "finding" : "findings"} filed`
              : join.differences.length > 0
                ? `${join.differences.length} marked on the grid`
                : "nothing found",
        },
      ];
    });

  const groups: FindingGroup[] = comparisons.map((join) => {
    const mine = findings.filter((finding) => belongsTo(finding, join.from, join.to));
    return {
      key: join.key,
      label: `${join.fromLabel} to ${join.toLabel}`,
      status: join.status ?? "",
      tone: join.tone ?? "clean",
      findings: mine,
      note:
        join.tone === "scene"
          ? "These two shots are not the same place, so this is a scene change rather than a cut inside a scene. Continuity rules do not apply across it, and nothing was checked."
          : join.differences.length > 0
            ? "The grid marked a difference here, and the agent read it and did not think it worth filing. What it marked is on the frames above."
            : "Read, and nothing was filed. The two frames agreed on the light, the ground and the sun.",
    };
  });

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1 className="wordmark">
            <Link to="/">
              <img src="/logocine.png" alt="CineMeridian" width={200} height={75} />
            </Link>
          </h1>
          <p className="tagline">A review that was really run, kept as it came.</p>
        </div>
        <div className="scene-line">
          <Link to="/try">analyse your own clips</Link>
        </div>
      </header>

      <section className="panel" style={{ marginTop: 24 }}>
        <h2>
          Six clips, in the order they would be cut
          <Info>
            These are the six sample clips, put through the page for your own
            footage: decoded in a browser, written into ClickHouse as takes with
            the measurements read from the two frames a cut touches, and
            investigated by the agent through the MCP server. What you see below
            is that run, recorded. Press the same buttons yourself with the same
            clips and you will get another run of the same shape.
          </Info>
        </h2>
        <p className="hint" style={{ marginBottom: 0 }}>
          Each clip answers one question: two that should agree, a bag that
          appears, a shot flipped so its shadow points the wrong way, a room
          with the sun through the window, and the same room after dark.
        </p>
      </section>

      <div className="take-list">
        {example.takes.map((take) => (
          <section className="panel take-card" key={take.position}>
            <div className="take-head">
              <h2>Take {take.position}</h2>
              <span className="hint" style={{ margin: 0 }}>
                {take.name}
              </span>
            </div>

            <div className="clip-pair" style={{ marginTop: 16 }}>
              {(["head", "tail"] as const).map((role, index) => (
                <figure key={role}>
                  <img
                    className="frame-strip"
                    src={frame(take[role])}
                    alt={`${role} frame of take ${take.position}`}
                  />
                  <figcaption>
                    <b>{index === 0 ? "First frame" : "Last frame"}</b>
                    {index === 0 ? "0.00s" : `${take.duration.toFixed(2)}s`}
                    <span>
                      {index === 0
                        ? "what the previous cut lands on"
                        : "what the next cut leaves from"}
                    </span>
                  </figcaption>
                </figure>
              ))}
            </div>

            <p className="hint" style={{ marginTop: 12, marginBottom: 0 }}>
              Recorded at {take.when.replace("T", " ")}, read from the file
            </p>
          </section>
        ))}
      </div>

      <div className="setup-row">
        <section className="panel">
          <h2>
            Where this was filmed
            <Info>
              One place for the whole scene, which is what a scene means. A
              position buys everything that needs the sun; a capture time buys
              everything that needs the clock. This run was given both.
            </Info>
          </h2>
          <dl className="facts">
            <div className="fact">
              <dt>Position</dt>
              <dd>
                {example.place
                  ? `${example.place.latitude}, ${example.place.longitude}`
                  : "not given"}
              </dd>
            </div>
            <div className="fact">
              <dt>Project</dt>
              <dd>{example.project.production_id}</dd>
            </div>
            <div className="fact">
              <dt>Model</dt>
              <dd>{example.project.model}</dd>
            </div>
            <div className="fact">
              <dt>This run</dt>
              <dd>{example.startedAt} UTC</dd>
            </div>
          </dl>
        </section>

        <section className="panel act">
          <div className="act-body">
            <h2 style={{ marginBottom: 6 }}>
              {example.takes.length} takes, {example.joins.length} joins
            </h2>
            <p className="act-label">
              The answer
              <Info>
                The agent is asked to open with three sentences for an editor
                rather than an engineer: whether these shots can be joined, and
                if not, what is wrong. Everything it worked through is in the
                full report.
              </Info>
            </p>
            <Report markdown={shortVersion(example.report)} />

            <ul className="join-notes">
              {comparisons.map((join) => (
                <li key={join.key} className={`tone-${join.tone}`}>
                  <b>
                    Take {join.from} <span aria-hidden="true">→</span> {join.to}
                  </b>
                  <span>{join.status}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="act-buttons">
            <Link to="/try" className="button-link">
              Run this on your own clips
            </Link>
            <button
              type="button"
              className="ghost"
              onClick={() => setReportOpen(true)}
            >
              Show the full report
            </button>
          </div>
        </section>
      </div>

      {example.sceneChanges.length > 0 ? (
        <section className="panel">
          <h2>Joins that are scene changes</h2>
          <p className="hint">
            Continuity is a rule about a scene, so these are not faults and were
            not treated as any. A beach followed by a room disagrees about
            everything, and all of it is intended.
          </p>
          {example.sceneChanges.map((change) => (
            <div className="verdict" key={`${change.from}-${change.to}`}>
              <div>
                <b>
                  Take {change.from} to take {change.to}
                </b>
                <p>{change.note}</p>
              </div>
            </div>
          ))}
        </section>
      ) : null}

      <ResultView
        report={example.report}
        showAnswer={false}
        findings={findings}
        comparisons={comparisons}
        groups={groups}
        steps={events}
        seconds={example.seconds}
        facts={runFacts(example)}
      />

      {reportOpen ? (
        <ReportSheet
          markdown={example.report}
          onClose={() => setReportOpen(false)}
        />
      ) : null}

      <footer className="disclosure">
        <strong>Where this came from.</strong> A real run of the page for your
        own clips, recorded on {example.startedAt} UTC and kept so this page
        opens without waiting. Sun and moon positions are computed with the NOAA
        solar position algorithm and are real astronomy. All footage is
        synthetic and self-made. The agent only ever recommends: it does not
        modify an edit, submit a render, or mark its own findings reviewed.{" "}
        <Link to="/scene">
          The scene the database was built on is still here.
        </Link>
      </footer>
    </div>
  );
}

/** A finding belongs to the join that names both of its takes, and to no other. */
function belongsTo(finding: Finding, from: number, to: number): boolean {
  const ends = [finding.take_a, finding.take_b].map((id) => id.slice(-3));
  return ends.includes(`t0${from}`) && ends.includes(`t0${to}`);
}

/** What this run was given, in the shape the shared view renders. */
function runFacts(recording: typeof example): ResultFact[] {
  const facts: ResultFact[] = recording.conditions
    .map((light, index): ResultFact | null =>
      light
        ? {
            label: `Take ${index + 1}, light`,
            value: `${light.regime.replace(/_/g, " ")}, looks like ${light.time_of_day.replace(/_/g, " ")}`,
            info:
              light.regime === "artificial"
                ? "Shadows spread out from a point here, which is a lamp rather than the sun, so nothing about the time of day can be read from them."
                : "Shadows run parallel here, which is the sun. The sun can be used as a clock, indoors or out: a beam through a window obeys the same arithmetic as a beach.",
          }
        : null,
    )
    .filter((fact): fact is ResultFact => fact !== null);

  facts.push(
    {
      label: "Sun checks",
      value: recording.place ? "ran" : "absent, no position was given",
      info: "The sun's angle cannot be computed without knowing where you stood.",
    },
    {
      label: "Clock checks",
      value: "ran",
      info: "Every clip carried the time it was recorded, written into the file.",
    },
    {
      label: "Frames kept",
      value: "yes, and kept for this page",
      info: "The agent could look at the pictures rather than only read the measurements.",
    },
  );

  return facts;
}
