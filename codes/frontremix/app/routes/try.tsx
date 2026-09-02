import { useCallback, useRef, useState } from "react";
import { json, type LoaderFunctionArgs } from "@remix-run/node";
import { Link, useLoaderData } from "@remix-run/react";

import { AgentTimeline, type TimelineEvent } from "~/components/AgentTimeline";
import { FilmRoll } from "~/components/FilmRoll";
import { FindingsMap } from "~/components/FindingsMap";
import { apiBase, type Finding } from "~/lib/api";
import {
  ClipTooLarge,
  ClipTooLong,
  DEFAULT_LIMITS,
  extractHeadAndTail,
  type ExtractedFrame,
} from "~/lib/extract";
import { composePair, DEFAULT_GRID } from "~/lib/gridpair";
import { describeCall, describeOutcome } from "~/lib/narrate";
import { readMp4Metadata, type Mp4Metadata } from "~/lib/mp4meta";

/** Matches MIN_TAKES and MAX_TAKES in app/tools/project.py. The ceiling is a
 *  quota rather than a limit of the code: every take costs two vision calls at
 *  ingest, and this project has already met "Resource exhausted" at six calls
 *  in quick succession. */
const MIN_TAKES = 2;
const MAX_TAKES = 6;

export async function loader(_args: LoaderFunctionArgs) {
  return json({ apiBase: apiBase(), limits: DEFAULT_LIMITS });
}

type Project = {
  production_id: string;
  scene_id: string;
  edit_version: string;
  frames_stored: boolean;
};

type SceneChange = { from: number; to: number; note: string };

type TakeState = {
  file: File | null;
  meta: Mp4Metadata | null;
  headFrame: ExtractedFrame | null;
  tailFrame: ExtractedFrame | null;
  duration: number;
  when: string;
};

const EMPTY: TakeState = {
  file: null,
  meta: null,
  headFrame: null,
  tailFrame: null,
  duration: 0,
  when: "",
};

/**
 * Bring your own footage, and let the agent investigate it.
 *
 * The demo scene is convincing because there is a database under it: thirty
 * takes, a hundred thousand ephemeris rows, telemetry. The agent's power is not
 * that it looks at pictures, it is that it can ask across all of that at once,
 * and a page that imitated the look of that from a couple of vision calls would
 * be a pretence.
 *
 * So this builds the real thing. Each clip becomes a take, the frames a cut
 * would touch become observations, an ephemeris is computed for the window the
 * files claim, and the order they are put in becomes the cut list. Then the
 * same agent runs on it, through the same MCP server, and what comes back is
 * the same console: what it asked, what it dismissed, and what it filed.
 */
export default function TryYourClips() {
  const { apiBase, limits } = useLoaderData<typeof loader>();

  const [slots, setSlots] = useState<number[]>([1, 2]);
  const [takes, setTakes] = useState<Record<number, TakeState>>({});
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [storeFrames, setStoreFrames] = useState(true);

  const [stage, setStage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [sceneChanges, setSceneChanges] = useState<SceneChange[]>([]);
  const [elapsed, setElapsed] = useState(0);

  const ordered = slots
    .map((id) => takes[id])
    .filter((take): take is TakeState => !!take?.headFrame && !!take?.tailFrame);

  const missing: string[] = [];
  if (ordered.length < MIN_TAKES) missing.push(`at least ${MIN_TAKES} clips`);
  if (ordered.length >= MIN_TAKES && ordered.some((take) => take.when === "")) {
    missing.push("the time each clip was recorded");
  }
  if (lat === "" || lon === "") missing.push("the position where you filmed");
  const ready = missing.length === 0;

  const analyse = useCallback(async () => {
    if (!ready) return;
    setProblem(null);
    setProject(null);
    setEvents([]);
    setFindings([]);
    setSceneChanges([]);

    const startedAt = Date.now();
    const ticking = window.setInterval(
      () => setElapsed(Math.round((Date.now() - startedAt) / 1000)),
      1000,
    );

    try {
      setStage("Checking whether each join is inside one scene");
      setSceneChanges(await findSceneChanges(apiBase, ordered));

      setStage(`Reading ${ordered.length} clips and writing them into ClickHouse`);
      const created = await createProject(apiBase, ordered, lat, lon, storeFrames);
      setProject(created);

      setStage("The agent is investigating");
      await streamAnalysis(apiBase, created, Number(lat), Number(lon), setEvents);

      setStage("Collecting what it filed");
      setFindings(await fetchProjectFindings(apiBase, created));
    } catch (caught) {
      setProblem(caught instanceof Error ? caught.message : String(caught));
    } finally {
      window.clearInterval(ticking);
      setStage(null);
    }
  }, [ready, ordered, lat, lon, storeFrames, apiBase]);

  const [placeNote, setPlaceNote] = useState<string | null>(null);

  const useMyPosition = useCallback(() => {
    if (!navigator.geolocation) {
      setPlaceNote("This browser will not say where it is.");
      return;
    }
    setPlaceNote("Asking your browser where it is…");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLat(position.coords.latitude.toFixed(5));
        setLon(position.coords.longitude.toFixed(5));
        setPlaceNote(
          "That is where you are now, not where the clip was filmed. Change it if they differ.",
        );
      },
      (error) => setPlaceNote(`Your browser would not say: ${error.message}`),
      { timeout: 10_000 },
    );
  }, []);

  const adoptPlace = useCallback((meta: Mp4Metadata) => {
    const { latitude, longitude } = meta;
    if (latitude == null || longitude == null) return;
    setLat((current) => (current === "" ? latitude.toFixed(5) : current));
    setLon((current) => (current === "" ? longitude.toFixed(5) : current));
  }, []);

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1 className="wordmark">
            Cine<span>Meridian</span>
          </h1>
          <p className="tagline">Bring your own footage.</p>
        </div>
        <div className="scene-line">
          <Link to="/">back to the demo scene</Link>
        </div>
      </header>

      <section className="panel" style={{ marginTop: 24 }}>
        <h2>What this does</h2>
        <p className="hint" style={{ maxWidth: "78ch" }}>
          Add your clips in the order they would be cut. Each becomes a take in
          ClickHouse, along with the measurements read from the two frames a cut
          actually touches and an ephemeris computed for the time and place your
          files claim. Then the same agent that reviews the demo scene is pointed
          at it, through the same MCP server, and you watch it work.
        </p>
        <p className="hint" style={{ maxWidth: "78ch", marginBottom: 0 }}>
          Between {MIN_TAKES} and {MAX_TAKES} clips, up to{" "}
          {(limits.maxBytes / 1024 / 1024).toFixed(0)} MB and {limits.maxSeconds}{" "}
          seconds each. Daylight and outdoors, with something casting a shadow.
          The clips themselves are never uploaded: your browser decodes them and
          sends two frames per take.
        </p>
      </section>

      <div className="take-list">
        {slots.map((id, index) => (
          <TakeCard
            key={id}
            position={index + 1}
            limits={limits}
            state={takes[id]}
            onChange={(next) => setTakes((current) => ({ ...current, [id]: next }))}
            onPlace={adoptPlace}
            onRemove={
              slots.length > MIN_TAKES
                ? () => setSlots((current) => current.filter((slot) => slot !== id))
                : undefined
            }
          />
        ))}
      </div>

      {slots.length < MAX_TAKES ? (
        <button
          type="button"
          className="add-take"
          onClick={() => setSlots((current) => [...current, Math.max(...current) + 1])}
        >
          + Add a take
        </button>
      ) : (
        <p className="hint">
          {MAX_TAKES} is the ceiling, and it is a quota rather than a rule: every
          take costs two vision calls before the agent has started.
        </p>
      )}

      <section className="panel">
        <h2>Where this was filmed</h2>
        <p className="hint">
          One place for the whole scene, which is what a scene means. Filled in
          from any clip that carried a position.
        </p>

        <div className="form-row">
          <label className="field">
            <span>Latitude</span>
            <input
              type="number"
              step="0.0001"
              placeholder="8.75"
              value={lat}
              onChange={(event) => setLat(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Longitude</span>
            <input
              type="number"
              step="0.0001"
              placeholder="-83.5"
              value={lon}
              onChange={(event) => setLon(event.target.value)}
            />
          </label>
        </div>

        {/* Offered rather than inferred. A position could be guessed from the
            picture, and the guess would be confident and wrong; it could be
            recovered from the shadow, and that is real celestial navigation
            carrying hundreds of kilometres of error. Neither is worth putting
            in a field somebody will then trust. What a browser knows about
            where it is standing is at least a measurement. */}
        <div className="form-row" style={{ marginTop: 14 }}>
          <button type="button" className="ghost small" onClick={useMyPosition}>
            Use where I am now
          </button>
          <button
            type="button"
            className="ghost small"
            onClick={() => {
              setLat("8.75");
              setLon("-83.5");
            }}
          >
            Use the demo location
          </button>
          <span className="hint" style={{ margin: 0, alignSelf: "center" }}>
            {placeNote ??
              "Your browser knows where it is standing, which helps if you filmed nearby. The demo location is Costa Rica, for trying the machinery on anything."}
          </span>
        </div>

        <label className="choice">
          <input
            type="checkbox"
            checked={storeFrames}
            onChange={(event) => setStoreFrames(event.target.checked)}
          />
          <span>
            <b>Let the agent look at the frames</b>
            <em>
              Two frames per take are kept for 24 hours, then deleted
              automatically. Without them the agent can read your measurements
              but cannot see the pictures, and it says so in its own report: the
              visual adjudication has nothing to point at. Either way the clips
              themselves are never uploaded.
            </em>
          </span>
        </label>
      </section>

      {ordered.length >= MIN_TAKES ? (
        <section className="panel act">
          <div>
            <h2 style={{ marginBottom: 6 }}>
              {ordered.length} takes, {ordered.length - 1}{" "}
              {ordered.length === 2 ? "join" : "joins"}
            </h2>
            <p className="hint" style={{ margin: 0, maxWidth: "70ch" }}>
              {ready
                ? "Writes the project, then runs the agent over it. Four to six minutes, and every step is shown."
                : `Still needs ${joinNicely(missing)}.`}
            </p>
          </div>
          <button type="button" onClick={analyse} disabled={!ready || !!stage}>
            {stage ? "Analysing…" : "Analyse this cut"}
          </button>
        </section>
      ) : null}

      {stage ? (
        <FilmRoll
          stage={stage}
          elapsed={elapsed}
          frames={ordered.flatMap((take) =>
            [take.headFrame?.dataUrl, take.tailFrame?.dataUrl].filter(
              (url): url is string => !!url,
            ),
          )}
          latest={[...events].reverse().find((event) => event.kind === "tool_call")?.text}
        />
      ) : null}
      {problem ? <p className="banner">{problem}</p> : null}

      {sceneChanges.length > 0 ? (
        <section className="panel">
          <h2>Joins that are scene changes</h2>
          <p className="hint">
            Continuity is a rule about a scene, so these are not faults and were
            not treated as any. A beach followed by a street disagrees about
            everything, and all of it is intended.
          </p>
          {sceneChanges.map((change) => (
            <div className="verdict" key={`${change.from}-${change.to}`}>
              <div>
                <b>
                  Take {change.from} to take {change.to}
                </b>
                <p>{change.note || "These two frames are not the same place."}</p>
              </div>
            </div>
          ))}
        </section>
      ) : null}

      {project ? (
        <section className="panel">
          <h2>Your project</h2>
          <p className="hint" style={{ marginBottom: 12 }}>
            Written into the same tables the demo scene lives in, under its own
            production, so nothing of yours mixes with anything of ours.
          </p>
          <dl className="facts">
            <div className="fact">
              <dt>Production</dt>
              <dd>{project.production_id}</dd>
            </div>
            <div className="fact">
              <dt>Cut version</dt>
              <dd>{project.edit_version}</dd>
            </div>
            <div className="fact">
              <dt>Frames kept</dt>
              <dd>{project.frames_stored ? "yes, for 24 hours" : "no"}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      {events.length > 0 ? (
        <AgentTimeline events={events} running={stage === "The agent is investigating"} elapsed={elapsed} />
      ) : null}

      {findings.length > 0 ? (
        <section className="panel">
          <h2>What it filed</h2>
          <FindingsMap
            findings={findings}
            selectedId={null}
            focusTakeId={null}
            onSelect={() => undefined}
            onClearFocus={() => undefined}
          />
        </section>
      ) : null}

      <footer className="disclosure">
        <strong>Where your footage goes.</strong> The clips are decoded by your
        own browser and never uploaded. Two frames per take are sent so Gemini
        can measure them, and kept for 24 hours only if you asked for that above.
        Sun and moon positions are computed with the NOAA solar position
        algorithm and are real astronomy.
      </footer>
    </div>
  );
}

function TakeCard({
  position,
  limits,
  state,
  onChange,
  onRemove,
  onPlace,
}: {
  position: number;
  limits: typeof DEFAULT_LIMITS;
  state: TakeState | undefined;
  onChange: (next: TakeState) => void;
  onRemove?: () => void;
  onPlace: (meta: Mp4Metadata) => void;
}) {
  const current = state ?? EMPTY;
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const choose = useCallback(
    async (file: File) => {
      setProblem(null);
      try {
        setBusy("Reading what the file already knows");
        const meta = await readMp4Metadata(file);
        onPlace(meta);

        setBusy("Decoding and lifting the frames a cut would touch");
        const extracted = await extractHeadAndTail(file, {
          limits,
          onProgress: (done, total) => setBusy(`Lifting frames, ${done} of ${total}`),
        });

        onChange({
          file,
          meta,
          headFrame: extracted.frames.find((frame) => frame.role === "head") ?? null,
          tailFrame:
            [...extracted.frames].reverse().find((frame) => frame.role === "tail") ?? null,
          duration: extracted.durationSeconds,
          when: meta.recordedAt ? toLocalInput(meta.recordedAt) : "",
        });
      } catch (caught) {
        setProblem(
          caught instanceof ClipTooLarge || caught instanceof ClipTooLong
            ? caught.message
            : caught instanceof Error
              ? caught.message
              : "This file could not be read.",
        );
      } finally {
        setBusy(null);
      }
    },
    [limits, onChange, onPlace],
  );

  return (
    <section className="panel take-card">
      <div className="take-head">
        <h2>Take {position}</h2>
        {onRemove ? (
          <button type="button" className="ghost small" onClick={onRemove}>
            Remove
          </button>
        ) : null}
      </div>

      <div className="drop">
        <input
          ref={inputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          onChange={(event) => {
            const picked = event.target.files?.[0];
            if (picked) void choose(picked);
          }}
          hidden
        />
        <button type="button" onClick={() => inputRef.current?.click()} disabled={!!busy}>
          {current.file ? "Choose a different clip" : "Choose a clip"}
        </button>
        {current.file ? (
          <span className="hint" style={{ margin: 0 }}>
            {current.file.name}
          </span>
        ) : null}
      </div>

      {busy ? (
        <p className="empty">
          <span className="pulse" aria-hidden="true" />
          {busy}…
        </p>
      ) : null}
      {problem ? <p className="banner">{problem}</p> : null}

      {current.headFrame && current.tailFrame ? (
        <>
          <div className="clip-pair" style={{ marginTop: 16 }}>
            {([current.headFrame, current.tailFrame] as const).map((frame, index) => (
              <figure key={index}>
                <img src={frame.dataUrl} alt={`${frame.role} frame`} />
                <figcaption>
                  <b>{index === 0 ? "First frame" : "Last frame"}</b>
                  {frame.at.toFixed(2)}s
                  <span>
                    {index === 0
                      ? "what the previous cut lands on"
                      : "what the next cut leaves from"}
                  </span>
                </figcaption>
              </figure>
            ))}
          </div>

          <label
            className="field"
            style={{ flexDirection: "column", alignItems: "flex-start" }}
          >
            <span>
              Recorded at (your local time)
              {current.meta?.recordedAt ? ", read from the file" : ", not in this file"}
            </span>
            <input
              type="datetime-local"
              value={current.when}
              onChange={(event) => onChange({ ...current, when: event.target.value })}
            />
          </label>
        </>
      ) : null}
    </section>
  );
}

/**
 * Which joins are scene changes rather than cuts inside a scene.
 *
 * Asked before anything expensive happens, because continuity is a rule about a
 * scene: two shots in different places disagree about everything and none of it
 * is a fault. One reading per join, since whether two frames are the same place
 * is a far easier question than counting marks on sand, and five joins read
 * three times each would be fifteen calls before the work started.
 */
async function findSceneChanges(base: string, takes: TakeState[]): Promise<SceneChange[]> {
  const changes: SceneChange[] = [];

  for (let index = 0; index + 1 < takes.length; index += 1) {
    const outgoing = takes[index].tailFrame;
    const incoming = takes[index + 1].headFrame;
    if (!outgoing || !incoming) continue;

    const body = new FormData();
    body.append("pair", await composePair(outgoing.blob, incoming.blob), "pair.jpg");
    body.append("columns", String(DEFAULT_GRID.columns));
    body.append("rows", String(DEFAULT_GRID.rows));
    body.append("reads", "1");

    // A join that could not be checked is not a join that failed. Carrying on
    // is the cheaper mistake: refusing an analysis somebody asked for leaves
    // them nothing, while a scene change wrongly analysed produces findings
    // they can dismiss by looking.
    const response = await fetch(`${base}/api/ground`, { method: "POST", body });
    if (!response.ok) continue;

    const answer = (await response.json()) as { same_place: boolean; place_note: string };
    if (!answer.same_place) {
      changes.push({ from: index + 1, to: index + 2, note: answer.place_note });
    }
  }
  return changes;
}

async function createProject(
  base: string,
  takes: TakeState[],
  lat: string,
  lon: string,
  storeFrames: boolean,
): Promise<Project> {
  const form = new FormData();
  form.append("latitude", lat);
  form.append("longitude", lon);
  form.append("store_frames", String(storeFrames));

  takes.forEach((take, index) => {
    const n = index + 1;
    form.append(`take_${n}_head`, take.headFrame!.blob, `t${n}_head.jpg`);
    form.append(`take_${n}_tail`, take.tailFrame!.blob, `t${n}_tail.jpg`);
    form.append(`take_${n}_recorded_at`, new Date(take.when).toISOString());
    form.append(`take_${n}_duration`, String(take.duration));
  });

  const response = await fetch(`${base}/api/project`, { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(`the API said ${response.status}: ${(await response.text()).slice(0, 300)}`);
  }
  return (await response.json()) as Project;
}

async function fetchProjectFindings(base: string, project: Project): Promise<Finding[]> {
  const response = await fetch(
    `${base}/api/findings?edit_version=${encodeURIComponent(
      project.edit_version,
    )}&scene_id=${encodeURIComponent(project.scene_id)}`,
  );
  if (!response.ok) return [];

  const body = (await response.json()) as {
    result?: { rows?: unknown[][]; columns?: string[] };
  };
  const result = body.result;
  if (!result?.rows || !result?.columns) return [];

  return result.rows.map((row) => {
    const finding: Record<string, unknown> = {};
    result.columns!.forEach((column, index) => {
      finding[column.split(".").pop() ?? column] = row[index];
    });
    return finding as unknown as Finding;
  });
}

/** Run the agent, pushing each step into the timeline as it arrives. */
async function streamAnalysis(
  base: string,
  project: Project,
  latitude: number,
  longitude: number,
  onEvent: (update: (current: TimelineEvent[]) => TimelineEvent[]) => void,
): Promise<void> {
  const response = await fetch(`${base}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      edit_version: project.edit_version,
      scene_id: project.scene_id,
      production_id: project.production_id,
      latitude,
      longitude,
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`the agent could not be reached: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // The server separates frames with CRLF CRLF. Splitting on "\n\n" alone
    // parses nothing at all, silently, which is exactly what happened once.
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const event = parseFrame(frame);
      if (event) onEvent((current) => absorb(current, event));
    }
  }
}

function parseFrame(frame: string): TimelineEvent | null {
  let kind = "";
  let data = "";
  for (const line of frame.split(/\r?\n/)) {
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
      return { kind: "started", at, text: "reviewing your cut" };
    case "done":
      return { kind: "done", at, text: `finished in ${(at / 1000).toFixed(1)}s` };
    default:
      return null;
  }
}

/** Fold a result into the call it answers, so one action is one line. */
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

function joinNicely(parts: string[]): string {
  if (parts.length === 0) return "nothing";
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function trim(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}

/** `datetime-local` wants the browser's own timezone, without a suffix. */
function toLocalInput(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
