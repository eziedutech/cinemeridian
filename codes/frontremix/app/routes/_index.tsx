import { useCallback, useEffect, useRef, useState } from "react";
import { json, type LoaderFunctionArgs } from "@remix-run/node";
import { useLoaderData, useRevalidator } from "@remix-run/react";

import { AgentTimeline, type TimelineEvent } from "~/components/AgentTimeline";
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
    error: error ?? library.error,
    takes: library.takes,
    framesPerTake: library.framesPerTake,
    bucket: library.bucket || (process.env.GCS_ASSET_BUCKET ?? "cinemeridian-assets"),
    apiBase: apiBase(),
  });
}

export default function Console() {
  const data = useLoaderData<typeof loader>();
  const { editVersion, findings, error, takes, framesPerTake, bucket, apiBase } = data;
  const revalidator = useRevalidator();

  const [selected, setSelected] = useState<Finding | null>(null);
  const [openTake, setOpenTake] = useState<Take | null>(null);
  const [playing, setPlaying] = useState<Take | null>(null);
  const [focusTakeId, setFocusTakeId] = useState<string | null>(null);
  const [goToIndex, setGoToIndex] = useState<number | null>(null);
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
            if (event) setEvents((current) => [...current, event]);
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
            Cine<span>Meridian</span>
          </h1>
          <p className="tagline">Continuity intelligence for the shoot and the cut.</p>
        </div>
        <div className="scene-line">
          8.75°N 83.5°W
          <br />
          shot 3 to 15 December 2026
        </div>
      </header>

      <section className="bar project-bar">
        <label className="field">
          <span>Project</span>
          <select defaultValue={PROJECT.id}>
            <option value={PROJECT.id}>{PROJECT.name}</option>
          </select>
        </label>

        <button
          type="button"
          className="ghost"
          disabled
          title="Upload your own footage and analyse it. Not built yet."
        >
          New project
        </button>

        <label className="field">
          <span>Cut</span>
          <select
            value={editVersion}
            onChange={(event) => {
              window.location.href = `/?edit=${event.target.value}`;
            }}
          >
            {EDIT_VERSIONS.map((version) => (
              <option key={version} value={version}>
                {version}
              </option>
            ))}
          </select>
        </label>

        <div className="bar-right">
          <button type="button" onClick={() => analyse()} disabled={running}>
            {running ? "Analysing…" : `Analyse cut ${editVersion}`}
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() =>
              window.open(`/report?edit=${editVersion}`, "_blank", "noopener")
            }
            disabled={findings.length === 0}
          >
            Export PDF
          </button>
        </div>
      </section>

      <section className="bar takes-bar">
        <span className="field-label">
          Takes
          <Info>
            A scene is covered from several camera positions, called setups, and
            each setup is shot more than once. One of those attempts is a take.
            This scene has {takes.length} takes across 8 setups; {inCut} of them
            made cut {editVersion}. Every take is sampled at its head and its
            tail, because a cut joins one shot's tail to the next shot's head.
          </Info>
        </span>
        <span className="count">{takes.length}</span>
        <span className="count-sub">{inCut} in this cut</span>

        <label className="field goto">
          <span>Go to</span>
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

        <span className="stat-row bar-right">
          <span className="stat">
            <b>{findings.length}</b>
            <span>findings</span>
          </span>
          <span className="stat">
            <b>{visible}</b>
            <span>visible in cut</span>
          </span>
          <span className="stat">
            <b>{high}</b>
            <span>high severity</span>
          </span>
        </span>
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

      <div className="columns">
        <div>
          <FindingsMap
            findings={findings}
            selectedId={selected?.finding_id ?? null}
            focusTakeId={focusTakeId}
            onSelect={setSelected}
            onClearFocus={() => setFocusTakeId(null)}
          />
        </div>
        <div>
          <AgentTimeline events={events} running={running} elapsed={elapsed} />
        </div>
      </div>

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

function Info({ children }: { children: React.ReactNode }) {
  return (
    <span className="info" tabIndex={0} role="note">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true">
        <circle cx="12" cy="12" r="9.2" />
        <path d="M12 11v5.4" strokeLinecap="round" />
        <path d="M12 7.6h.01" strokeLinecap="round" />
      </svg>
      <span className="tip">{children}</span>
    </span>
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
      const detail = args.query ?? Object.values(args).join(" · ");
      return { kind: "tool_call", at, text: `${payload.name}  ${trim(detail, 400)}` };
    }
    case "tool_result":
      return { kind: "tool_result", at, text: trim(String(payload.result ?? ""), 240) };
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

function trim(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
