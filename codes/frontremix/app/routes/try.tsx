import { useCallback, useEffect, useRef, useState } from "react";
import { json, type LoaderFunctionArgs, type MetaFunction } from "@remix-run/node";
import { Link, useLoaderData, useSearchParams } from "@remix-run/react";

import { AgentTimeline, type TimelineEvent } from "~/components/AgentTimeline";
import type { Grid, GridDifference } from "~/components/CutComparison";
import { FilmRoll } from "~/components/FilmRoll";
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
import { apiBase, parseRows, type Finding } from "~/lib/api";
import {
  ClipTooLarge,
  ClipTooLong,
  DEFAULT_LIMITS,
  extractHeadAndTail,
  type ExtractedFrame,
} from "~/lib/extract";
import { composePair, DEFAULT_GRID } from "~/lib/gridpair";
import { keepForReport } from "~/lib/handoff";
import { describeCall, describeOutcome } from "~/lib/narrate";
import { readMp4Metadata, type Mp4Metadata } from "~/lib/mp4meta";

/** Matches MIN_TAKES and MAX_TAKES in app/tools/project.py. The ceiling is a
 *  quota rather than a limit of the code: every take costs two vision calls at
 *  ingest, and this project has already met "Resource exhausted" at six calls
 *  in quick succession. */
const MIN_TAKES = 2;
const MAX_TAKES = 6;

export const meta: MetaFunction = () => [{ title: "CineMeridian - analyse your clips" }];

export async function loader(_args: LoaderFunctionArgs) {
  return json({ apiBase: apiBase(), limits: DEFAULT_LIMITS });
}

type Project = {
  production_id: string;
  scene_id: string;
  edit_version: string;
  frames_stored: boolean;
  times_known: boolean;
  position_known: boolean;
};

type SceneChange = { from: number; to: number; note: string };

/** One join, with what the grid said changed across it. */
type Join = {
  from: number;
  to: number;
  grid: Grid;
  differences: GridDifference[];
};

/** What one frame said about its own light, before any file was consulted. */
type Conditions = {
  regime: string;
  shadows_are: string;
  time_of_day: string;
  opening_in_frame: boolean | null;
  opening_is_bright: boolean | null;
  lamps_visibly_on: boolean | null;
  sun_is_usable: boolean;
};

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
  const [report, setReport] = useState("");
  const [conditions, setConditions] = useState<Array<Conditions | null>>([]);
  const [joins, setJoins] = useState<Join[]>([]);
  const [runStartedAt, setRunStartedAt] = useState("");
  const [reportOpen, setReportOpen] = useState(false);
  const [confirmRerun, setConfirmRerun] = useState(false);
  const [showSteps, setShowSteps] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const ordered = slots
    .map((id) => takes[id])
    .filter((take): take is TakeState => !!take?.headFrame && !!take?.tailFrame);

  // The clips, and nothing else. A time and a position each buy one class of
  // check and are worth having; neither is worth standing between somebody and
  // an answer, and a tool that demands what the person already knows invites
  // the obvious question of what it is for.
  const ready = ordered.length >= MIN_TAKES;
  const hasTimes = ordered.length > 0 && ordered.every((take) => take.when !== "");
  const hasPlace = lat !== "" && lon !== "";

  // Worked out once and used twice: the list under the answer, and the tabs on
  // the comparison panel.
  const comparisons = describeJoins(joins, ordered, sceneChanges, findings, project);
  const joinNotes = comparisons.map((join) => ({
    key: join.key,
    from: join.from,
    to: join.to,
    tone: join.tone ?? "clean",
    status: join.status ?? "",
  }));

  const findingGroups = groupFindings(comparisons, findings, project);

  const skipped: string[] = [];
  if (!hasPlace) skipped.push("everything that needs the sun's position");
  if (!hasTimes) skipped.push("everything that needs the clock");

  const analyse = useCallback(async () => {
    if (!ready) return;
    setProblem(null);
    setProject(null);
    setEvents([]);
    setFindings([]);
    setRunStartedAt("");
    setSceneChanges([]);
    setConditions([]);
    setJoins([]);
    setReport("");

    const startedAtMs = Date.now();
    const ticking = window.setInterval(
      () => setElapsed(Math.round((Date.now() - startedAtMs) / 1000)),
      1000,
    );

    try {
      setStage("Reading the light and checking each join is inside one scene");
      const seen = await findSceneChanges(apiBase, ordered);
      setSceneChanges(seen.changes);
      setConditions(seen.conditions);
      setJoins(seen.joins);

      setStage(`Reading ${ordered.length} clips and writing them into ClickHouse`);
      const created = await createProject(
        apiBase,
        ordered,
        lat,
        lon,
        storeFrames,
        seen.conditions,
      );
      setProject(created);

      setStage("The agent is investigating");
      // Kept alongside the state setter because the handoff below needs the
      // finished text in this scope, and a setter does not hand it back.
      let written = "";
      const startedAt = await streamAnalysis(
        apiBase,
        created,
        lat === "" ? null : Number(lat),
        lon === "" ? null : Number(lon),
        setEvents,
        (markdown) => {
          written = markdown;
          setReport(markdown);
        },
      );
      setRunStartedAt(startedAt);

      setStage("Collecting what it filed");
      setFindings(await fetchProjectFindings(apiBase, created, startedAt));

      // The findings are rows anyone can fetch again; the answer and the note
      // of what the run was given live only in this tab, so the printable page
      // is handed them here rather than asked to invent them.
      keepForReport({
        production: created.production_id,
        scene: created.scene_id,
        edit: created.edit_version,
        report: written,
        facts: runFacts(created, seen.conditions).map(({ label, value }) => ({
          label,
          value,
        })),
        seconds: Math.round((Date.now() - startedAtMs) / 1000),
        takeCount: ordered.length,
        place: hasPlace ? `${lat}, ${lon}` : null,
      });
    } catch (caught) {
      setProblem(caught instanceof Error ? caught.message : String(caught));
    } finally {
      window.clearInterval(ticking);
      setStage(null);
    }
  }, [ready, ordered, lat, lon, storeFrames, apiBase]);

  const [searchParams] = useSearchParams();
  const [loadingSamples, setLoadingSamples] = useState<string | null>(null);

  // Arriving from the front page with a choice already made. The clips are
  // fetched through the API's proxy rather than from a public bucket, decoded
  // here like any other file, and from that point on nothing knows they were
  // ours rather than somebody's own.
  useEffect(() => {
    const wanted = (searchParams.get("clips") ?? "").split(",").filter(Boolean);
    if (wanted.length === 0) return;

    let live = true;
    (async () => {
      try {
        setLoadingSamples(`Fetching ${wanted.length} sample clips`);
        const listed = await fetch(`${apiBase}/api/samples`).then((r) => r.json());
        // The version goes in the address rather than a cache header: a clip
        // that has been re-stamped is a different file, and without this every
        // browser that has seen the old one keeps it for another day.
        const byFile: Record<string, string> = {};
        for (const clip of listed.clips ?? []) {
          byFile[clip.file] = clip.version
            ? `${clip.uri}?v=${encodeURIComponent(clip.version)}`
            : clip.uri;
        }

        const loaded: Record<number, TakeState> = {};
        for (const [index, file] of wanted.entries()) {
          const uri = byFile[file];
          if (!uri) continue;
          setLoadingSamples(`Fetching sample ${index + 1} of ${wanted.length}`);
          const [objectUri, version] = uri.split("?v=");
          const response = await fetch(
            `${apiBase}/api/frame?uri=${encodeURIComponent(objectUri)}` +
              (version ? `&v=${version}` : ""),
          );
          if (!response.ok) continue;
          const blob = await response.blob();
          const asFile = new File([blob], file, { type: "video/mp4" });

          setLoadingSamples(`Reading sample ${index + 1} of ${wanted.length}`);
          const meta = await readMp4Metadata(asFile);
          const extracted = await extractHeadAndTail(asFile, { limits });
          if (!live) return;

          loaded[index + 1] = {
            file: asFile,
            meta,
            headFrame: extracted.frames.find((f) => f.role === "head") ?? null,
            tailFrame:
              [...extracted.frames].reverse().find((f) => f.role === "tail") ?? null,
            duration: extracted.durationSeconds,
            when: meta.recordedAt ? toLocalInput(meta.recordedAt) : "",
          };
        }

        if (!live) return;
        setSlots(wanted.map((_, index) => index + 1));
        setTakes(loaded);
      } catch (caught) {
        if (live) {
          setProblem(
            caught instanceof Error ? caught.message : "The sample clips could not be loaded.",
          );
        }
      } finally {
        if (live) setLoadingSamples(null);
      }
    })();

    return () => {
      live = false;
    };
    // Once, on arrival. Re-running this would replace clips somebody has since
    // swapped out by hand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
            <Link to="/">
              <img src="/logocine.png" alt="CineMeridian" width={200} height={75} />
            </Link>
          </h1>
          <p className="tagline">Bring your own footage.</p>
        </div>
        <div className="scene-line">
          <Link to="/example">the analysed example</Link>
        </div>
      </header>

      <section className="panel" style={{ marginTop: 24 }}>
        <h2>
          Add your clips, in the order they would be cut
          <Info>
            Each clip becomes a take in ClickHouse, with the measurements read
            from the two frames a cut actually touches and an ephemeris computed
            for the time and place your files claim. Then the same agent that
            reviews the demo scene is pointed at it, through the same MCP
            server. The clips are never uploaded: your browser decodes them and
            sends two frames per take.
          </Info>
        </h2>
        <p className="hint" style={{ marginBottom: 0 }}>
          {MIN_TAKES} to {MAX_TAKES} clips, each under{" "}
          {(limits.maxBytes / 1024 / 1024).toFixed(0)} MB and {limits.maxSeconds}s.
          Daylight, outdoors, with something casting a shadow.
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

      <div className="setup-row">
      <section className="panel">
        <h2>
          Where this was filmed
          <Info>
            One place for the whole scene, which is what a scene means, filled
            in from any clip that carried a position. It is asked for rather
            than worked out: recovering a position from shadows is real
            celestial navigation, but the shadow length this reads carries about
            forty percent error, which puts an answer out by hundreds of
            kilometres.
          </Info>
        </h2>

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
          {placeNote ? (
            <span className="hint" style={{ margin: 0, alignSelf: "center" }}>
              {placeNote}
            </span>
          ) : null}
        </div>

        <label className="choice">
          <input
            type="checkbox"
            checked={storeFrames}
            onChange={(event) => setStoreFrames(event.target.checked)}
          />
          <span>
            <b>
              Let the agent look at the frames
              <Info>
                Without them the agent can read your measurements but cannot see
                the pictures, and it says so in its own report: the visual
                adjudication has nothing to point at. Either way the clips
                themselves are never uploaded.
              </Info>
            </b>
            <em>Two frames per take, kept 24 hours, then deleted automatically.</em>
          </span>
        </label>
      </section>

      {/* The count, what the run will and will not be able to check, and the
          way to start it. Once there is an answer this same box carries it,
          because the place somebody pressed for a verdict is where they look
          for one. */}
      <section className="panel act">
        <div className="act-body">
          <h2 style={{ marginBottom: 6 }}>
            {ready ? (
              <>
                {ordered.length} takes, {ordered.length - 1}{" "}
                {ordered.length === 2 ? "join" : "joins"}
              </>
            ) : (
              "Not enough clips yet"
            )}
          </h2>

          {report ? (
            <>
              <p className="act-label">
                The answer
                <Info>
                  The agent is asked to open with three sentences for an editor
                  rather than an engineer: whether these shots can be joined,
                  and if not, what is wrong. Everything it worked through to get
                  there is in the full report.
                </Info>
              </p>
              <Report markdown={shortVersion(report)} />

              {/* The agent writes about what it found. This says what it
                  looked at, which is the half a reader cannot check: five
                  joins, and what became of each. Without it a page showing one
                  comparison and one finding leaves the other four joins
                  ambiguous between clean and never read. */}
              {joinNotes.length > 1 ? (
                <ul className="join-notes">
                  {joinNotes.map((note) => (
                    <li key={note.key} className={`tone-${note.tone}`}>
                      <b>
                        Take {note.from} <span aria-hidden="true">→</span>{" "}
                        {note.to}
                      </b>
                      <span>{note.status}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : (
            <p className="hint" style={{ margin: 0 }}>
              {!ready
                ? `Add ${MIN_TAKES} clips or more, in the order they would be cut.`
                : skipped.length === 0
                  ? "Every check runs: the light, the ground, and the sun."
                  : `Runs without ${joinNicely(skipped)}. Fill in the time or the position to add those.`}
            </p>
          )}
        </div>

        <div className="act-buttons">
          {/* Asking first only once there is something to lose. A run takes
              minutes and reads every frame again, and the second press is
              usually somebody exploring the button rather than asking for
              another investigation. */}
          <button
            type="button"
            onClick={() => (report ? setConfirmRerun(true) : void analyse())}
            disabled={!ready || !!stage}
          >
            {stage ? "Analysing…" : report ? "Analyse again" : "Analyse this cut"}
          </button>
          {report ? (
            <button
              type="button"
              className="ghost"
              onClick={() => setReportOpen(true)}
            >
              Show the full report
            </button>
          ) : null}
        </div>
      </section>
      </div>

      {/* The samples are fetched and decoded before anything can be chosen, and
          that used to be a line of text at the bottom of a long page, out of
          sight of the button that had just been pressed. */}
      {loadingSamples ? (
        <FilmRoll
          stage={loadingSamples}
          elapsed={0}
          frames={[]}
          showClock={false}
          note="The clips are fetched once and decoded here in your browser. A few seconds."
        />
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
          events={events}
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

      <ResultView
        report={report}
        showAnswer={false}
        findings={findings}
        comparisons={comparisons}
        groups={findingGroups}
        steps={events}
        seconds={elapsed}
        facts={runFacts(project, conditions)}
      />

      {reportOpen ? (
        <ReportSheet
          markdown={report}
          onClose={() => setReportOpen(false)}
          onExport={
            project
              ? () =>
                  window.open(
                    `/report?edit=${encodeURIComponent(project.edit_version)}` +
                      `&scene=${encodeURIComponent(project.scene_id)}` +
                      (runStartedAt
                        ? `&since=${encodeURIComponent(runStartedAt)}`
                        : ""),
                    "_blank",
                    "noopener",
                  )
              : undefined
          }
        />
      ) : null}

      {confirmRerun ? (
        <div
          className="sheet-over"
          role="dialog"
          aria-modal="true"
          onClick={() => setConfirmRerun(false)}
        >
          <div
            className="sheet confirm"
            onClick={(event) => event.stopPropagation()}
          >
            <h2>Run this cut again?</h2>
            <p>
              The review on this page will be replaced: the answer, the joins,
              the findings list, and the report behind that button. It takes a
              few minutes, and every frame is read again.
            </p>
            <p className="hint">
              What the last run filed stays in the database under its own
              project, {project ? <code>{project.production_id}</code> : null}, and
              the new run is written separately. Nothing is deleted there.
            </p>
            <div className="form-row" style={{ marginTop: 18 }}>
              <button
                type="button"
                onClick={() => {
                  setConfirmRerun(false);
                  void analyse();
                }}
              >
                Run it again
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => setConfirmRerun(false)}
              >
                Keep what I have
              </button>
            </div>
          </div>
        </div>
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
  const [playing, setPlaying] = useState(false);

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
        <button
          type="button"
          className="ghost small"
          onClick={() => inputRef.current?.click()}
          disabled={!!busy}
        >
          {current.file ? "Choose a different clip" : "Choose a clip"}
        </button>

        {/* The file name told nobody anything. A still from the clip says which
            shot this is at a glance, and the clip itself is one press away,
            played from the copy already in the browser. */}
        {current.file ? (
          <button
            type="button"
            className="clip-thumb"
            onClick={() => setPlaying(true)}
            disabled={!current.headFrame}
          >
            <span className="clip-thumb-still">
              {current.headFrame ? (
                <img src={current.headFrame.dataUrl} alt="" />
              ) : (
                <span className="clip-thumb-blank" />
              )}
              <span className="clip-thumb-play" aria-hidden="true">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                  <path d="M5.6 4 12 8l-6.4 4V4Z" fill="currentColor" />
                </svg>
              </span>
            </span>
            <em>{current.file.name}</em>
          </button>
        ) : null}
      </div>

      {/* Decoding holds the page for a second or two on a big clip, so the
          shape of what is coming is drawn rather than described. */}
      {busy ? (
        <div className="clip-pair waiting-pair" style={{ marginTop: 16 }}>
          {[0, 1].map((index) => (
            <div key={index}>
              <span className="waiting-frame" />
              <span className="waiting-line waiting-title" />
            </div>
          ))}
          <p className="empty" style={{ gridColumn: "1 / -1", margin: 0 }}>
            <span className="pulse" aria-hidden="true" />
            {busy}…
          </p>
        </div>
      ) : null}
      {problem ? <p className="banner">{problem}</p> : null}

      {current.headFrame && current.tailFrame && !busy ? (
        <>
          <div className="clip-pair" style={{ marginTop: 16 }}>
            {([current.headFrame, current.tailFrame] as const).map((frame, index) => (
              <figure key={index}>
                <img
                  className="frame-strip"
                  src={frame.dataUrl}
                  alt={`${frame.role} frame`}
                />
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

      {playing && current.file ? (
        <ClipPlayer file={current.file} onClose={() => setPlaying(false)} />
      ) : null}
    </section>
  );
}

/**
 * The clip itself, played from the file the browser already has.
 *
 * An object URL rather than an upload: this page promises the footage never
 * leaves the machine, and playing it back is not the place to make that untrue.
 * The URL is revoked when the sheet closes, or the browser holds the whole file
 * in memory for as long as the tab is open.
 */
function ClipPlayer({ file, onClose }: { file: File; onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const made = URL.createObjectURL(file);
    setUrl(made);
    return () => URL.revokeObjectURL(made);
  }, [file]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-over" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="sheet player" onClick={(event) => event.stopPropagation()}>
        <div className="sheet-head">
          <h2 className="sheet-title">{file.name}</h2>
          <button type="button" className="ghost small" onClick={onClose}>
            Close
          </button>
        </div>
        {url ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption
          <video className="clip-video" src={url} controls autoPlay playsInline />
        ) : (
          <span className="waiting-frame" style={{ height: 320 }} />
        )}
      </div>
    </div>
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
async function findSceneChanges(
  base: string,
  takes: TakeState[],
): Promise<{
  changes: SceneChange[];
  conditions: Array<Conditions | null>;
  joins: Join[];
}> {
  const changes: SceneChange[] = [];
  const conditions: Array<Conditions | null> = takes.map(() => null);
  const joins: Join[] = [];

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

    const answer = (await response.json()) as {
      same_place: boolean;
      place_note: string;
      grid?: Grid;
      differences?: GridDifference[];
      conditions?: { outgoing: Conditions | null; incoming: Conditions | null };
    };
    joins.push({
      from: index + 1,
      to: index + 2,
      grid: answer.grid ?? DEFAULT_GRID,
      differences: answer.differences ?? [],
    });
    if (!answer.same_place) {
      changes.push({ from: index + 1, to: index + 2, note: answer.place_note });
    }
    // The same call already read what is lighting both frames. Carrying that
    // forward rather than asking again saves twenty seconds a join.
    conditions[index] = conditions[index] ?? answer.conditions?.outgoing ?? null;
    conditions[index + 1] = answer.conditions?.incoming ?? null;
  }
  return { changes, conditions, joins };
}

async function createProject(
  base: string,
  takes: TakeState[],
  lat: string,
  lon: string,
  storeFrames: boolean,
  conditions: Array<Conditions | null>,
): Promise<Project> {
  const form = new FormData();
  form.append("store_frames", String(storeFrames));
  // Sent only when known. An empty field would arrive as a position of zero,
  // which is a real place in the Gulf of Guinea and a confident wrong answer.
  if (lat !== "" && lon !== "") {
    form.append("latitude", lat);
    form.append("longitude", lon);
  }

  takes.forEach((take, index) => {
    const n = index + 1;
    form.append(`take_${n}_head`, take.headFrame!.blob, `t${n}_head.jpg`);
    form.append(`take_${n}_tail`, take.tailFrame!.blob, `t${n}_tail.jpg`);
    form.append(`take_${n}_duration`, String(take.duration));
    if (take.when !== "") {
      form.append(`take_${n}_recorded_at`, new Date(take.when).toISOString());
    }

    const light = conditions[index];
    if (light) {
      form.append(`take_${n}_regime`, light.regime);
      form.append(`take_${n}_time_of_day`, light.time_of_day);
      form.append(`take_${n}_shadows_are`, light.shadows_are);
      for (const key of ["opening_in_frame", "opening_is_bright", "lamps_visibly_on"] as const) {
        if (light[key] !== null) form.append(`take_${n}_${key}`, String(light[key]));
      }
    }
  });

  const response = await fetch(`${base}/api/project`, { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(`the API said ${response.status}: ${(await response.text()).slice(0, 300)}`);
  }
  return (await response.json()) as Project;
}

async function fetchProjectFindings(
  base: string,
  project: Project,
  since: string,
): Promise<Finding[]> {
  const response = await fetch(
    `${base}/api/findings?edit_version=${encodeURIComponent(
      project.edit_version,
    )}&scene_id=${encodeURIComponent(project.scene_id)}` +
      (since ? `&since=${encodeURIComponent(since)}` : ""),
  );
  if (!response.ok) return [];

  // Through the same reader the rest of the console uses. What comes back is
  // an MCP envelope with the columns and rows inside a string, not the plain
  // object this once expected, so the panel stayed empty however many findings
  // the agent had written.
  const body = (await response.json()) as { result?: unknown };
  return parseRows<Finding>(body.result);
}

/** Run the agent, pushing each step into the timeline as it arrives. */
async function streamAnalysis(
  base: string,
  project: Project,
  latitude: number | null,
  longitude: number | null,
  onEvent: (update: (current: TimelineEvent[]) => TimelineEvent[]) => void,
  onReport: (markdown: string) => void,
): Promise<string> {
  const response = await fetch(`${base}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      edit_version: project.edit_version,
      scene_id: project.scene_id,
      production_id: project.production_id,
      ...(latitude === null || longitude === null ? {} : { latitude, longitude }),
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`the agent could not be reached: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // The server's own clock, so the findings shown afterwards are this run's
  // and not every run this project has ever had.
  let startedAt = "";

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
      if (!event) continue;
      // The report is the answer, not a line in the log. Filed between
      // "counting the rows" and "reading the review queue" it carried the same
      // weight as the bookkeeping that produced it.
      if (event.kind === "started" && event.detail) startedAt = event.detail;
      if (event.kind === "reasoning") onReport(event.text);
      else onEvent((current) => absorb(current, event));
    }
  }
  return startedAt;
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
      return {
        kind: "started",
        at,
        text: "reviewing your cut",
        detail: String(payload.at_utc ?? ""),
      };
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


/**
 * The run in five numbers.
 *
 * A person who has just waited six minutes wants to know what happened before
 * they want to know each thing that happened. The full list is a click away and
 * stays that way, because it is evidence rather than reading.
 */
function ProcessSummary({
  events,
  seconds,
}: {
  events: TimelineEvent[];
  seconds: number;
}) {
  const calls = events.filter((event) => event.kind === "tool_call");
  const count = (name: string) => calls.filter((event) => event.name === name).length;
  const failed = calls.filter((event) => event.ok === false).length;

  const stats: Array<[string, string]> = [
    ["Steps", String(calls.length)],
    ["Questions to the database", String(count("run_query"))],
    ["Frames looked at", String(count("adjudicate_cut") * 2)],
    ["Physics calls", String(count("compute_light_rig") + count("compute_render_error"))],
    ["Time", seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`],
  ];
  if (failed > 0) stats.push(["Steps that failed", String(failed)]);

  return (
    <dl className="facts" style={{ marginTop: 16 }}>
      {stats.map(([label, value]) => (
        <div className="fact" key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}




/**
 * Every join, as the shared view wants them, and what happened at each.
 *
 * The status is worked out here rather than asked of the model: whether a join
 * was ruled a scene change, whether the agent filed anything against it, and
 * whether the grid marked a cell are all facts this page already holds. A
 * reader who sees one comparison and one finding needs to know that the other
 * joins were read and came back clean, which is a different claim from not
 * having been read.
 */
function describeJoins(
  joins: Join[],
  takes: TakeState[],
  changes: SceneChange[],
  findings: Finding[],
  project: Project | null,
): Array<Comparison & { key: string; from: number; to: number }> {
  return joins.flatMap((join) => {
    const outgoing = takes[join.from - 1]?.tailFrame?.dataUrl;
    const incoming = takes[join.to - 1]?.headFrame?.dataUrl;
    if (!outgoing || !incoming) return [];

    const sceneChange = changes.some(
      (change) => change.from === join.from && change.to === join.to,
    );
    // Both ends, not either. A finding about the join between takes two and
    // three names both of them, and matching on one end alone credited it to
    // the join before it as well, which said two joins had a finding when the
    // agent had filed exactly one.
    const filed = project
      ? findings.filter((finding) => {
          const from = takeId(project, join.from);
          const to = takeId(project, join.to);
          return (
            (finding.take_a === from && finding.take_b === to) ||
            (finding.take_a === to && finding.take_b === from)
          );
        }).length
      : 0;

    const tone: Comparison["tone"] = sceneChange
      ? "scene"
      : filed > 0 || join.differences.length > 0
        ? "marked"
        : "clean";

    return [
      {
        key: `${join.from}-${join.to}`,
        from: join.from,
        to: join.to,
        outgoing,
        incoming,
        grid: join.grid,
        differences: join.differences,
        fromLabel: `Take ${join.from}`,
        toLabel: `Take ${join.to}`,
        tone,
        status: sceneChange
          ? "a scene change, so not checked for continuity"
          : filed > 0
            ? `${filed} ${filed === 1 ? "finding" : "findings"} filed`
            : join.differences.length > 0
              ? `${join.differences.length} marked on the grid`
              : "nothing found",
      },
    ];
  });
}

/**
 * The filed list, split by the join each finding belongs to.
 *
 * A cut of three takes has two joins, and a list that only shows the join that
 * produced something says nothing about the other. Whether it was read and
 * came back clean, or was skipped as a scene change, or was never reached, are
 * three different answers, and the page holds all three already.
 */
function groupFindings(
  comparisons: Array<Comparison & { key: string; from: number; to: number }>,
  findings: Finding[],
  project: Project | null,
): FindingGroup[] {
  if (!project || comparisons.length === 0) return [];

  const claimed = new Set<string>();

  const groups: FindingGroup[] = comparisons.map((join) => {
    const from = takeId(project, join.from);
    const to = takeId(project, join.to);
    const mine = findings.filter(
      (finding) =>
        (finding.take_a === from && finding.take_b === to) ||
        (finding.take_a === to && finding.take_b === from),
    );
    mine.forEach((finding) => claimed.add(finding.finding_id));

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

  // Findings that name one take rather than a join: a slate that disagrees
  // with the sun belongs to a take, not to the cut beside it.
  const loose = findings.filter((finding) => !claimed.has(finding.finding_id));
  if (loose.length > 0) {
    groups.push({
      key: "loose",
      label: "Not about one join",
      status: `${loose.length} filed`,
      tone: "marked",
      findings: loose,
      note: "",
    });
  }

  return groups;
}

/** The id the ingest gives a take, so a finding can be traced back to a join. */
function takeId(project: Project, position: number): string {
  return `${project.scene_id}_t${String(position).padStart(2, "0")}`;
}

/** What this project was given, in the shape the shared view renders. */
function runFacts(
  project: Project | null,
  conditions: Array<Conditions | null>,
): ResultFact[] {
  if (!project) return [];

  const facts: ResultFact[] = conditions
    .map((light, index): ResultFact | null =>
      light
        ? {
            label: `Take ${index + 1}, light`,
            value: `${light.regime.replace(/_/g, " ")}, looks like ${light.time_of_day.replace(/_/g, " ")}`,
            info: light.sun_is_usable
              ? "Shadows run parallel here, which is the sun. The sun can be used as a clock, indoors or out: a beam through a window obeys the same arithmetic as a beach."
              : "Shadows spread out from a point here, which is a lamp rather than the sun, so nothing about the time of day can be read from them.",
          }
        : null,
    )
    .filter((fact): fact is ResultFact => fact !== null);

  facts.push({
    label: "Sun checks",
    value: project.position_known ? "ran" : "skipped, no position was given",
    info: "The sun's angle cannot be computed without knowing where you stood. Without a position these checks are absent because they could not run, not because nothing was wrong.",
  });
  facts.push({
    label: "Clock checks",
    value: project.times_known ? "ran" : "skipped, no capture times were given",
  });
  facts.push({
    label: "Frames kept",
    value: project.frames_stored ? "yes, for 24 hours" : "no",
    info: "Without stored frames the agent can read your measurements but cannot see the pictures, and its visual adjudication has nothing to point at.",
  });
  facts.push({
    label: "Project",
    value: project.production_id,
    info: "Written into the same tables the demo scene lives in, under its own production, so nothing of yours mixes with anything of ours.",
  });

  return facts;
}
