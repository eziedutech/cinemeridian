import { useCallback, useEffect, useRef, useState } from "react";
import {
  json,
  type LoaderFunctionArgs,
  type MetaFunction,
  type SerializeFrom,
} from "@remix-run/node";
import { Link, useLoaderData, useRevalidator } from "@remix-run/react";

import { AgentTimeline, type TimelineEvent } from "~/components/AgentTimeline";
import { Info } from "~/components/Info";
import type { GridDifference } from "~/components/CutComparison";
import { Report } from "~/components/Report";
import { ReportSheet } from "~/components/ReportSheet";
import { ResultView, shortVersion, type ResultFact } from "~/components/ResultView";
import showcase from "~/showcase.json";
import { describeCall, describeOutcome } from "~/lib/narrate";
import { EvidencePair } from "~/components/EvidencePair";
import { Filmstrip } from "~/components/Filmstrip";
import { FindingsMap } from "~/components/FindingsMap";
import { FramePlayer } from "~/components/FramePlayer";
import { TakeDialog } from "~/components/TakeDialog";
import { apiBase, fetchFindings, type Finding } from "~/lib/api";
import { fetchTakes, type Take } from "~/lib/takes";

const SCENE_ID = "sc14";
const EDIT_VERSIONS = ["v13", "v14"];
const PROJECT = { id: "prod_tideline", name: "The Tide Line" };

export const meta: MetaFunction = () => [{ title: "CineMeridian - the scene the database was built on" }];

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const editVersion = url.searchParams.get("edit") ?? "v14";

  const [{ findings, error }, library] = await Promise.all([
    fetchFindings(editVersion, SCENE_ID),
    fetchTakes(SCENE_ID, editVersion),
  ]);

  return json({
    editVersion,
    findings,
    // One complete run, frozen by scripts/build_showcase.py, so the page opens
    // on a finished piece of work rather than a spinner. The findings beside it
    // are read live from ClickHouse, because those are cheap and are the thing
    // a reviewer would actually act on.
    showcase,
    error: error ?? library.error,
    takes: library.takes,
    framesPerTake: library.framesPerTake,
    bucket: library.bucket || (process.env.GCS_ASSET_BUCKET ?? "cinemeridian-assets"),
    apiBase: apiBase(),
  });
}

export default function Console() {
  const data = useLoaderData<typeof loader>();
  const { editVersion, findings, error, takes, framesPerTake, bucket, apiBase, showcase } =
    data;
  const revalidator = useRevalidator();

  const [selected, setSelected] = useState<Finding | null>(null);
  const [openTake, setOpenTake] = useState<Take | null>(null);
  const [playing, setPlaying] = useState<Take | null>(null);
  const [focusTakeId, setFocusTakeId] = useState<string | null>(null);
  const [goToIndex, setGoToIndex] = useState<number | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  // A run takes minutes, and the first event can be twenty seconds out. A
  // panel with no clock on it is indistinguishable from a panel that has hung.
  useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.round((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [running]);

  const analyse = useCallback(
    async (focus?: Take) => {
      setRunning(true);
      setEvents([]);
      setOpenTake(null);
      setFocusTakeId(focus ? focus.take_id : null);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        // SSE over POST, so the request carries which cut to review.
        // EventSource cannot do that - it is GET only - which is why this
        // reads the stream by hand instead.
        const response = await fetch(`${apiBase}/api/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ edit_version: editVersion, scene_id: SCENE_ID }),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`analysis failed: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line; anything after the last
          // one is a partial frame and has to wait for more bytes.
          // The separator is CRLF CRLF, not LF LF. Splitting on "\n\n" matches
          // nothing at all against a \r\n\r\n stream, so every event gets
          // silently swallowed and the timeline sits empty for the whole run,
          // then goes blank again when it ends. Accept either ending.
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const event = parseFrame(frame);
            if (event) setEvents((current) => absorb(current, event));
          }
        }
      } catch (caught) {
        if (!controller.signal.aborted) {
          setEvents((current) => [
            ...current,
            {
              kind: "error",
              at: 0,
              text: caught instanceof Error ? caught.message : String(caught),
            },
          ]);
        }
      } finally {
        setRunning(false);
        abortRef.current = null;
        // The agent wrote its findings to ClickHouse during the run, so reload
        // them rather than reconstructing them from the stream.
        revalidator.revalidate();
      }
    },
    [apiBase, editVersion, revalidator],
  );

  const visible = findings.filter((f) => f.visible_in_cut).length;
  const high = findings.filter((f) => f.severity === "high").length;
  const inCut = takes.filter((t) => t.cut_position != null).length;

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1 className="wordmark">
            <Link to="/">
              <img src="/logocine.png" alt="CineMeridian" width={200} height={75} />
            </Link>
          </h1>
          <p className="tagline">Continuity intelligence for the shoot and the cut.</p>
        </div>
        <div className="scene-line">
          <Link to="/try">analyse your own clips</Link>
        </div>
      </header>

      {/* The same opening as the page for your own clips: what this page is,
          in one line, before anything asks to be operated. */}
      <section className="panel" style={{ marginTop: 24 }}>
        <h2>
          The takes this scene was shot in
          <Info>
            A scene is covered from several camera positions, called setups,
            and each setup is shot more than once. One of those attempts is a
            take. This scene has {takes.length} takes across 8 setups;{" "}
            {inCut} of them made cut {editVersion}. Every take is sampled at its
            head and its tail, because a cut joins one shot's tail to the next
            shot's head.
          </Info>
        </h2>
        <p className="hint" style={{ marginBottom: 0 }}>
          {takes.length} takes, {inCut} in cut {editVersion}, shot over five
          days. Five faults were planted in this scene on purpose, and the
          answer key is published in the repository.
        </p>
      </section>

      {error ? <p className="banner">Could not reach the API: {error}</p> : null}

      <Filmstrip
        takes={takes}
        bucket={bucket}
        framesPerTake={framesPerTake}
        apiBase={apiBase}
        goToIndex={goToIndex}
        onOpen={setOpenTake}
      />

      {/* The same shape as the page for somebody's own clips: what this run
          was given on the left, the answer and the way on from it on the
          right. Two pages that do the same work should not need to be learnt
          twice. */}
      <div className="setup-row">
        <section className="panel">
          <h2>
            The scene
            <Info>
              A scene of thirty takes across eight setups, shot over five days,
              with five faults planted in it and an answer key published in the
              repository. The review below is a frozen run, so the page opens
              without waiting; the button re-runs it live.
            </Info>
          </h2>
          <div className="form-row">
            <label className="field">
              <span>Cut</span>
              <select
                value={editVersion}
                onChange={(event) => {
                  window.location.href = `/scene?edit=${event.target.value}`;
                }}
              >
                {EDIT_VERSIONS.map((version) => (
                  <option key={version} value={version}>
                    {version}
                  </option>
                ))}
              </select>
            </label>

            <label className="field goto">
              <span>Go to take</span>
              <input
                type="number"
                min={1}
                max={takes.length}
                placeholder="1"
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setGoToIndex(
                    Number.isFinite(value) && value >= 1 && value <= takes.length
                      ? value - 1
                      : null,
                  );
                }}
              />
            </label>
          </div>

          <dl className="facts" style={{ marginTop: 18 }}>
            <div className="fact">
              <dt>Production</dt>
              <dd>{PROJECT.name}</dd>
            </div>
            <div className="fact">
              <dt>Takes</dt>
              <dd>
                {takes.length}, {inCut} in this cut
              </dd>
            </div>
            <div className="fact">
              <dt>Findings</dt>
              <dd>
                {findings.length} filed, {visible} visible at speed, {high} high
              </dd>
            </div>
          </dl>
        </section>

        <section className="panel act">
          <div className="act-body">
            <h2 style={{ marginBottom: 6 }}>
              {takes.length} takes, cut {editVersion}
            </h2>
            <p className="act-label">
              The answer
              <Info>
                The agent is asked to open with three sentences for an editor
                rather than an engineer: whether this cut can be locked, and if
                not, what is wrong. Everything it worked through is in the full
                report.
              </Info>
            </p>
            <Report markdown={shortVersion(showcase.report)} />
          </div>

          <div className="act-buttons">
            <button type="button" onClick={() => analyse()} disabled={running}>
              {running ? "Analysing…" : `Analyse cut ${editVersion} again`}
            </button>
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

      {reportOpen ? (
        <ReportSheet
          markdown={showcase.report}
          onClose={() => setReportOpen(false)}
          onExport={() =>
            window.open(`/report?edit=${editVersion}`, "_blank", "noopener")
          }
        />
      ) : null}

      {running || events.length > 0 ? (
        <AgentTimeline events={events} running={running} elapsed={elapsed} />
      ) : (
        <ResultView
          showAnswer={false}
          report={showcase.report}
          findings={findings}
          comparison={{
            outgoing: frameUrl(apiBase, bucket, showcase.pair.outgoing),
            incoming: frameUrl(apiBase, bucket, showcase.pair.incoming),
            grid: showcase.comparison.grid,
            // The frozen file is JSON, so its `present_in` is a plain string
            // until it is told what it actually is, and a run that marked
            // nothing leaves an array TypeScript cannot see a shape in.
            differences: showcase.comparison.differences as unknown as GridDifference[],
            fromLabel: `${showcase.pair.outgoing.setup} ${showcase.pair.outgoing.take}`,
            toLabel: `${showcase.pair.incoming.setup} ${showcase.pair.incoming.take}`,
          }}
          steps={showcase.steps.map(toTimelineEvent).filter(Boolean) as TimelineEvent[]}
          seconds={showcase.seconds}
          facts={sceneFacts(showcase, takes.length, findings.length)}
          selectedId={selected?.finding_id ?? null}
          onSelectFinding={setSelected}
          focusTakeId={focusTakeId}
          onClearFocus={() => setFocusTakeId(null)}
        />
      )}

      <footer className="disclosure">
        <strong>What is real here.</strong> Sun and moon positions are computed
        with the NOAA solar position algorithm and are real astronomy. The tide
        and the weather telemetry are <strong>simulated</strong>, from two
        harmonic constituents and a physical afternoon model, and are not a
        forecast for any place. All footage is synthetic and self-made; no film
        or broadcast material is used, and <em>The Tide Line</em> is not a real
        production. The agent only ever recommends: it does not modify the edit,
        submit a render, or mark its own findings reviewed.
      </footer>

      <EvidencePair
        finding={selected}
        apiBase={apiBase}
        bucket={bucket}
        framesPerTake={framesPerTake}
        onClose={() => setSelected(null)}
      />

      <TakeDialog
        take={openTake}
        bucket={bucket}
        framesPerTake={framesPerTake}
        apiBase={apiBase}
        analysing={running}
        onClose={() => setOpenTake(null)}
        onPlay={(take) => {
          setOpenTake(null);
          setPlaying(take);
        }}
        onAnalyse={(take) => analyse(take)}
      />

      <FramePlayer
        take={playing}
        bucket={bucket}
        framesPerTake={framesPerTake}
        apiBase={apiBase}
        onClose={() => setPlaying(null)}
      />
    </div>
  );
}


function parseFrame(frame: string): TimelineEvent | null {
  let kind = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) kind = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!kind || !data) return null;

  let payload: Record<string, unknown> = {};
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }

  const at = Number(payload.elapsed_ms ?? 0);
  switch (kind) {
    case "tool_call": {
      const args = (payload.args ?? {}) as Record<string, string>;
      const name = String(payload.name ?? "");
      return {
        kind: "tool_call",
        at,
        name,
        text: describeCall(name, args),
        detail: trim(args.query ?? Object.values(args).join(" · "), 600),
      };
    }
    case "tool_result":
      return {
        kind: "tool_result",
        at,
        name: String(payload.name ?? ""),
        ok: payload.ok !== false,
        text: describeOutcome(
          payload.ok !== false,
          typeof payload.rows === "number" ? payload.rows : null,
          String(payload.detail ?? ""),
        ),
      };
    case "reasoning":
      return { kind: "reasoning", at, text: String(payload.text ?? "") };
    case "error":
      return { kind: "error", at, text: String(payload.detail ?? "") };
    case "started":
      return { kind: "started", at, text: `reviewing ${payload.edit_version}` };
    case "done":
      return { kind: "done", at, text: `finished in ${(at / 1000).toFixed(1)}s` };
    default:
      return null;
  }
}

/**
 * Fold a result into the call it answers, so the timeline reads one line per
 * action rather than two.
 *
 * Matched by name, walking backwards to the most recent call still waiting on
 * an answer. Tools run one at a time here, so that is unambiguous; if a result
 * ever arrives with no call to match, it is kept as its own row rather than
 * dropped, because a silently discarded event is worse than an odd looking one.
 */
function absorb(events: TimelineEvent[], incoming: TimelineEvent): TimelineEvent[] {
  if (incoming.kind !== "tool_result") return [...events, incoming];

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const candidate = events[index];
    if (
      candidate.kind === "tool_call" &&
      candidate.name === incoming.name &&
      candidate.ok === undefined
    ) {
      const merged = [...events];
      merged[index] = {
        ...candidate,
        ok: incoming.ok,
        outcome: incoming.text,
        took: incoming.at - candidate.at,
      };
      return merged;
    }
  }
  return [...events, incoming];
}

function trim(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}


/** Where the API will serve one demo frame from. */
function frameUrl(
  base: string,
  bucket: string,
  at: { setup: string; take: string; frame: number },
): string {
  const uri = `gs://${bucket}/frames/sc14/${at.setup}/${at.take}/f${String(at.frame).padStart(3, "0")}.jpg`;
  return `${base}/api/frame?uri=${encodeURIComponent(uri)}`;
}

/** A frozen step, in the shape the timeline renders. */
function toTimelineEvent(step: Record<string, unknown>): TimelineEvent | null {
  const at = Number(step.elapsed_ms ?? 0);
  const name = String(step.name ?? "");

  if (step.kind === "tool_call") {
    const args = (step.args ?? {}) as Record<string, string>;
    return {
      kind: "tool_call",
      at,
      name,
      text: describeCall(name, args),
      detail: trim(args.query ?? Object.values(args).join(" · "), 600),
    };
  }
  if (step.kind === "tool_result") {
    return {
      kind: "tool_result",
      at,
      name,
      ok: step.ok !== false,
      text: describeOutcome(
        step.ok !== false,
        typeof step.rows === "number" ? step.rows : null,
        String(step.detail ?? ""),
      ),
    };
  }
  return null;
}

/** What this scene had to work with, in the shape the shared view renders. */
function sceneFacts(
  // As it arrives from the loader rather than as it sits on disk: going
  // through JSON is what changes the empty arrays out from under it.
  frozen: SerializeFrom<typeof loader>["showcase"],
  takeCount: number,
  findingCount: number,
): ResultFact[] {
  const light = frozen.comparison.conditions?.outgoing;
  return [
    {
      label: "Takes in the scene",
      value: String(takeCount),
      info: "A real scene, shot over twelve days across eight camera setups. The agent's power is that it can ask across all of them at once.",
    },
    {
      label: "Findings filed",
      value: String(findingCount),
      info: "Each one written by the agent itself, through MCP, into a queue a person still has to work through.",
    },
    {
      label: "Light in the pair above",
      value: light ? `${light.regime.replace(/_/g, " ")}, looks like ${light.time_of_day.replace(/_/g, " ")}` : "not read",
      info: "Read from the frames alone, before any file was opened. Shadows running parallel are the sun; shadows spreading from a point are a lamp.",
    },
    {
      label: "Sun and tide",
      value: "computed, not stored",
      info: "Sun and moon positions come from the NOAA solar position algorithm and are real astronomy. The tide is simulated from two harmonic constituents and is not a forecast for any place.",
    },
    {
      label: "This run",
      value: frozen.built_at + " UTC",
      info: "One complete run, frozen so the page opens instantly. Re-made by scripts/build_showcase.py whenever the scene changes.",
    },
  ];
}
