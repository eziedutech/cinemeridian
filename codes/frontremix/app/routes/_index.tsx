import { useCallback, useRef, useState } from "react";
import { json, type LoaderFunctionArgs } from "@remix-run/node";
import { useLoaderData, useRevalidator } from "@remix-run/react";

import { AgentTimeline, type TimelineEvent } from "~/components/AgentTimeline";
import { EvidencePair } from "~/components/EvidencePair";
import { FindingsMap } from "~/components/FindingsMap";
import { apiBase, fetchFindings, type Finding } from "~/lib/api";

const SCENE_ID = "sc14";
const EDIT_VERSIONS = ["v13", "v14"];

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const editVersion = url.searchParams.get("edit") ?? "v14";
  const { findings, error } = await fetchFindings(editVersion, SCENE_ID);

  return json({
    editVersion,
    findings,
    error,
    apiBase: apiBase(),
    bucket: process.env.GCS_ASSET_BUCKET ?? "cinemeridian-assets",
  });
}

export default function Console() {
  const { editVersion, findings, error, apiBase, bucket } =
    useLoaderData<typeof loader>();
  const revalidator = useRevalidator();

  const [selected, setSelected] = useState<Finding | null>(null);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const analyse = useCallback(async () => {
    setRunning(true);
    setEvents([]);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // SSE over POST, so the request carries which cut to review. EventSource
      // cannot do that — it is GET only — which is why this reads the stream
      // by hand instead.
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
        const frames = buffer.split("\n\n");
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
      // them rather than trying to reconstruct them from the stream.
      revalidator.revalidate();
    }
  }, [apiBase, editVersion, revalidator]);

  const visible = findings.filter((f) => f.visible_in_cut).length;
  const high = findings.filter((f) => f.severity === "high").length;

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
          <strong>The Tide Line</strong> · scene {SCENE_ID} · 8 setups, 30 takes
          <br />
          8.75°N 83.5°W · shot 3–15 December 2026
        </div>
      </header>

      <div className="panel" style={{ marginTop: 24 }}>
        <div className="controls">
          {EDIT_VERSIONS.map((version) => (
            <a key={version} href={`/?edit=${version}`}>
              <button
                type="button"
                className="ghost"
                aria-pressed={version === editVersion}
              >
                cut {version}
              </button>
            </a>
          ))}
          <button type="button" onClick={analyse} disabled={running}>
            {running ? "Analysing…" : `Analyse ${editVersion}`}
          </button>
          <span className="stat-row" style={{ marginLeft: "auto" }}>
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
        </div>
        {error ? (
          <p className="hint" style={{ marginTop: 12, marginBottom: 0 }}>
            Could not reach the API: {error}
          </p>
        ) : null}
      </div>

      <div className="columns">
        <div>
          <FindingsMap
            findings={findings}
            selectedId={selected?.finding_id ?? null}
            onSelect={setSelected}
          />
          <EvidencePair finding={selected} apiBase={apiBase} bucket={bucket} />
        </div>
        <div>
          <AgentTimeline events={events} running={running} />
        </div>
      </div>

      <footer className="disclosure">
        <strong>What is real here.</strong> Sun and moon positions are computed
        with the NOAA solar position algorithm and are real astronomy. The tide
        and the weather telemetry are <strong>simulated</strong> — two harmonic
        constituents and a physical afternoon model — and are not a forecast for
        any place. All footage is synthetic and self-made; no film or broadcast
        material is used. The agent only ever recommends: it does not modify the
        edit, submit a render, or mark its own findings reviewed.
      </footer>
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
