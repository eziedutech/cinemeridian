import { useCallback, useRef, useState } from "react";
import { json, type LoaderFunctionArgs } from "@remix-run/node";
import { Link, useLoaderData } from "@remix-run/react";

import { SunIcon } from "~/components/Icons";
import { apiBase } from "~/lib/api";
import {
  ClipTooLarge,
  ClipTooLong,
  DEFAULT_LIMITS,
  extractHeadAndTail,
  type ExtractedFrame,
} from "~/lib/extract";
import { composePair, DEFAULT_GRID } from "~/lib/gridpair";
import { readMp4Metadata, type Mp4Metadata } from "~/lib/mp4meta";

export async function loader(_args: LoaderFunctionArgs) {
  return json({ apiBase: apiBase(), limits: DEFAULT_LIMITS });
}

type Inferred = {
  camera_heading_deg: number | null;
  heading_uncertainty_deg: number | null;
  sun_azimuth_deg: number;
  sun_elevation_deg: number;
  expected_shadow_length_ratio: number;
  observed_shadow_length_ratio: number | null;
  length_agreement: number | null;
  timestamp_trustworthy: boolean | null;
  note: string;
};

type FrameResult = {
  role: string;
  moment: string;
  reads: number;
  observations: Array<Record<string, unknown>>;
  inferred: Inferred;
};

type CompareResult = {
  latitude: number;
  longitude: number;
  model: string;
  reads_expected: number;
  verdict: {
    verdict: "matched" | "suspect" | "unmeasurable";
    headline: string;
    detail: string;
    minutes_apart: number;
    sun_elevation_change_deg: number;
    sun_azimuth_change_deg: number;
    expected_length_ratio: number | null;
    observed_length_ratio: number | null;
    ratio_agreement: number | null;
    camera_heading_change_deg: number | null;
    detectable_from_minutes: number | null;
  };
  frames: FrameResult[];
};

type GroundDifference = {
  cell: string;
  what: string;
  present_in: "outgoing" | "incoming";
  seen_in_reads: number;
  box: { x: number; y: number; width: number; height: number };
};

type GroundResult = {
  grid: { columns: number; rows: number };
  reads: number;
  reads_expected: number;
  agreement_needed: number;
  model: string;
  differences: GroundDifference[];
};

/**
 * Point the tool at two shots of your own and ask whether they cut together.
 *
 * The order matters and is the whole idea. The first clip is the shot being
 * cut away from, so what counts in it is its *last* moment; the second is the
 * shot being cut to, so what counts is its *first*. Those two moments land
 * next to each other on screen and an audience reads them as one continuous
 * instant, which means the sun in them has to agree.
 *
 * Neither video is uploaded. Both are decoded here in the browser and only the
 * two frames the cut actually joins are sent.
 */
export default function TryYourClip() {
  const { apiBase, limits } = useLoaderData<typeof loader>();

  const outgoing = useClipSlot(limits);
  const incoming = useClipSlot(limits);

  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [ground, setGround] = useState<GroundResult | null>(null);

  // Whichever clip volunteers a position first fills the shared pair. A cut
  // inside a scene is one place, so asking for it twice would be asking a
  // person to type the same thing twice.
  const adoptPlace = useCallback((meta: Mp4Metadata) => {
    const { latitude, longitude } = meta;
    if (latitude == null || longitude == null) return;
    setLat((current) => (current === "" ? latitude.toFixed(5) : current));
    setLon((current) => (current === "" ? longitude.toFixed(5) : current));
  }, []);

  const compare = useCallback(async () => {
    const from = outgoing.tailFrame;
    const to = incoming.headFrame;
    if (!from || !to) return;

    setProblem(null);
    setResult(null);
    setBusy("Measuring the shadow in each frame and asking the sun");

    try {
      const body = new FormData();
      body.append("outgoing", from.blob, "outgoing.jpg");
      body.append("incoming", to.blob, "incoming.jpg");
      body.append("outgoing_recorded_at", new Date(outgoing.when).toISOString());
      body.append("incoming_recorded_at", new Date(incoming.when).toISOString());
      body.append("latitude", lat);
      body.append("longitude", lon);
      body.append("outgoing_at_seconds", String(from.at));
      body.append("incoming_at_seconds", String(to.at));

      const response = await fetch(`${apiBase}/api/compare`, { method: "POST", body });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`the API said ${response.status}: ${detail.slice(0, 300)}`);
      }
      setResult((await response.json()) as CompareResult);
    } catch (caught) {
      setProblem(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }, [
    outgoing.tailFrame,
    outgoing.when,
    incoming.headFrame,
    incoming.when,
    lat,
    lon,
    apiBase,
  ]);

  const checkGround = useCallback(async () => {
    const from = outgoing.tailFrame;
    const to = incoming.headFrame;
    if (!from || !to) return;

    setProblem(null);
    setGround(null);
    setBusy("Laying both frames under one grid and asking what changed");

    try {
      const body = new FormData();
      body.append("pair", await composePair(from.blob, to.blob), "pair.jpg");
      body.append("columns", String(DEFAULT_GRID.columns));
      body.append("rows", String(DEFAULT_GRID.rows));

      const response = await fetch(`${apiBase}/api/ground`, { method: "POST", body });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`the API said ${response.status}: ${detail.slice(0, 300)}`);
      }
      setGround((await response.json()) as GroundResult);
    } catch (caught) {
      setProblem(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }, [outgoing.tailFrame, incoming.headFrame, apiBase]);

  const bothLoaded = !!outgoing.tailFrame && !!incoming.headFrame;

  // A disabled button that will not say why is a dead end, and this page had
  // one: two clips would load, both checks sat greyed out, and nothing on
  // screen named the thing that was missing.
  const missing: string[] = [];
  if (!bothLoaded) missing.push("both clips");
  if (bothLoaded && (outgoing.when === "" || incoming.when === "")) {
    missing.push("the time each clip was recorded");
  }
  if (lat === "" || lon === "") missing.push("the position where you filmed");

  const ready = bothLoaded && missing.length === 0;

  const working = busy ?? outgoing.busy ?? incoming.busy;

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1 className="wordmark">
            Cine<span>Meridian</span>
          </h1>
          <p className="tagline">Point it at two shots of your own.</p>
        </div>
        <div className="scene-line">
          <Link to="/">back to the demo scene</Link>
        </div>
      </header>

      <section className="panel" style={{ marginTop: 24 }}>
        <h2>What this does</h2>
        <p className="hint" style={{ maxWidth: "78ch" }}>
          Give it the two shots either side of a cut, in the order they would be
          cut. It takes the <strong>last</strong> moment of the first and the{" "}
          <strong>first</strong> moment of the second, because those are the two
          frames a cut actually joins, and asks whether the sun agrees they
          belong to the same afternoon. Neither video is uploaded: both are
          decoded by your own browser, and only those two frames are sent.
        </p>
        <p className="hint" style={{ maxWidth: "78ch", marginBottom: 0 }}>
          Daylight and outdoors, with something in frame casting a shadow. No
          shadow means nothing to work from, and it will say so rather than
          guess. Up to {(limits.maxBytes / 1024 / 1024).toFixed(0)} MB and{" "}
          {limits.maxSeconds} seconds each.
        </p>
      </section>

      <div className="clip-pair">
        <ClipCard
          slot={outgoing}
          onMeta={adoptPlace}
          title="Shot A, cut away from"
          uses="tail"
          usesLabel="Its last moment is the one the cut uses."
        />
        <ClipCard
          slot={incoming}
          onMeta={adoptPlace}
          title="Shot B, cut to"
          uses="head"
          usesLabel="Its first moment is the one the cut uses."
        />
      </div>

      {bothLoaded ? (
        <p className="next-step">
          <strong>Both clips are in.</strong> Two checks below, and they are
          independent.{" "}
          <em>Check the ground</em> is ready now and needs nothing further.{" "}
          <em>Check the light</em> needs {joinNicely(missing)}, because the sun&apos;s
          angle cannot be computed without knowing when and where you stood.
        </p>
      ) : null}

      <section className="panel">
        <h2>Where this was filmed</h2>
        <p className="hint">
          One place for both shots, which is what a cut inside a scene means.
          Filled in from either file if it carried a position.
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
          <button type="button" onClick={compare} disabled={!ready || !!working}>
            {busy ? "Working…" : "Check the light"}
          </button>
          {!ready && bothLoaded ? (
            <span className="hint" style={{ margin: 0 }}>
              still needs {joinNicely(missing)}
            </span>
          ) : null}
        </div>

        <p className="hint" style={{ marginTop: 14, marginBottom: 0 }}>
          The position is asked for rather than worked out. Recovering one from
          shadows is real celestial navigation, but the shadow length this reads
          carries about forty percent error, which puts a position out by
          hundreds of kilometres. Enough to sanity check a location, nowhere near
          enough to find one, and it does not pretend otherwise.
        </p>
      </section>

      {working ? (
        <p className="empty">
          <span className="pulse" aria-hidden="true" />
          {working}…
        </p>
      ) : null}
      {problem ? <p className="banner">{problem}</p> : null}

      <section className="panel">
        <h2>What changed on the ground</h2>
        <p className="hint" style={{ maxWidth: "78ch" }}>
          A separate question, asked separately. The sun says when a shot was
          filmed and will not be argued with; this says what is lying on the
          ground, and is a judgement. It needs no times and no position, only the
          two frames, so it works on footage that carries no metadata at all.
        </p>
        <div className="form-row">
          <button
            type="button"
            className="ghost"
            onClick={checkGround}
            disabled={!outgoing.tailFrame || !incoming.headFrame || !!working}
          >
            Check the ground
          </button>
          <span className="hint" style={{ margin: 0 }}>
            Both frames are laid side by side under one {DEFAULT_GRID.columns} by{" "}
            {DEFAULT_GRID.rows} grid and read {3} times. A cell is reported only
            if more than one reading saw it.
          </span>
        </div>

        {ground ? (
          <Ground
            result={ground}
            outgoing={outgoing.tailFrame?.dataUrl ?? null}
            incoming={incoming.headFrame?.dataUrl ?? null}
          />
        ) : null}
      </section>

      {result ? <Verdict result={result} /> : null}

      <footer className="disclosure">
        <strong>Where your footage goes.</strong> Nowhere. Both clips are decoded
        by your own browser and the files themselves are never sent. Two JPEG
        frames are uploaded so Gemini can measure the shadows in them. Sun and
        moon positions are computed with the NOAA solar position algorithm and
        are real astronomy.
      </footer>
    </div>
  );
}

/** Everything one clip slot has to hold, so both of them behave alike. */
function useClipSlot(limits: typeof DEFAULT_LIMITS) {
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<Mp4Metadata | null>(null);
  const [frames, setFrames] = useState<ExtractedFrame[]>([]);
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const choose = useCallback(
    async (chosen: File, onMeta: (meta: Mp4Metadata) => void) => {
      setProblem(null);
      setFrames([]);
      setFile(chosen);

      try {
        setBusy("Reading what the file already knows");
        const parsed = await readMp4Metadata(chosen);
        setMeta(parsed);
        if (parsed.recordedAt) setWhen(toLocalInput(parsed.recordedAt));
        onMeta(parsed);

        setBusy("Decoding and lifting the frames that matter");
        const extracted = await extractHeadAndTail(chosen, {
          limits,
          onProgress: (done, total) => setBusy(`Lifting frames, ${done} of ${total}`),
        });
        setFrames(extracted.frames);
      } catch (caught) {
        if (caught instanceof ClipTooLarge || caught instanceof ClipTooLong) {
          setProblem(caught.message);
        } else {
          setProblem(
            caught instanceof Error ? caught.message : "This file could not be read.",
          );
        }
        setFile(null);
      } finally {
        setBusy(null);
      }
    },
    [limits],
  );

  return {
    file,
    meta,
    frames,
    when,
    setWhen,
    busy,
    problem,
    choose,
    headFrame: frames.find((frame) => frame.role === "head") ?? null,
    tailFrame: [...frames].reverse().find((frame) => frame.role === "tail") ?? null,
  };
}

type Slot = ReturnType<typeof useClipSlot>;

function ClipCard({
  slot,
  onMeta,
  title,
  uses,
  usesLabel,
}: {
  slot: Slot;
  onMeta: (meta: Mp4Metadata) => void;
  title: string;
  uses: "head" | "tail";
  usesLabel: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const chosenFrame = uses === "head" ? slot.headFrame : slot.tailFrame;

  return (
    <section className="panel">
      <h2>{title}</h2>
      <p className="hint">{usesLabel}</p>

      <div className="drop">
        <input
          ref={inputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          onChange={(event) => {
            const picked = event.target.files?.[0];
            if (picked) void slot.choose(picked, onMeta);
          }}
          hidden
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={!!slot.busy}
        >
          {slot.file ? "Choose a different clip" : "Choose a clip"}
        </button>
        {slot.file ? (
          <span className="hint" style={{ margin: 0 }}>
            {slot.file.name}
          </span>
        ) : null}
      </div>

      {slot.busy ? (
        <p className="empty">
          <span className="pulse" aria-hidden="true" />
          {slot.busy}…
        </p>
      ) : null}
      {slot.problem ? <p className="banner">{slot.problem}</p> : null}

      {chosenFrame ? (
        <>
          <figure style={{ marginTop: 18 }}>
            <img src={chosenFrame.dataUrl} alt={`${uses} frame`} />
            <figcaption>
              <b>{uses === "head" ? "First frame" : "Last frame"}</b>
              {chosenFrame.at.toFixed(2)}s
              <span>this is the frame that gets read</span>
            </figcaption>
          </figure>

          <div className="known" style={{ marginTop: 18 }}>
            <Known
              label="Recorded at"
              value={slot.meta?.recordedAt ? slot.meta.recordedAt.toUTCString() : null}
              missing="not in this file"
            />
            <Known
              label="Location"
              value={
                slot.meta?.latitude != null
                  ? `${slot.meta.latitude.toFixed(4)}, ${slot.meta.longitude?.toFixed(4)}`
                  : null
              }
              missing="not in this file"
            />
            <Known label="Camera heading" value={null} missing="never in any file" />
          </div>

          <label
            className="field"
            style={{ flexDirection: "column", alignItems: "flex-start" }}
          >
            <span>Recorded at (your local time)</span>
            <input
              type="datetime-local"
              value={slot.when}
              onChange={(event) => slot.setWhen(event.target.value)}
            />
          </label>
        </>
      ) : null}
    </section>
  );
}

function Known({
  label,
  value,
  missing,
}: {
  label: string;
  value: string | null;
  missing: string;
}) {
  return (
    <div className="known-item">
      <dt>{label}</dt>
      <dd className={value ? "known-yes" : "known-no"}>{value ?? missing}</dd>
    </div>
  );
}

function Verdict({ result }: { result: CompareResult }) {
  const verdict = result.verdict;
  const short = result.frames.find((frame) => frame.reads < result.reads_expected);
  const tone =
    verdict.verdict === "matched"
      ? "verdict verdict-good"
      : verdict.verdict === "suspect"
        ? "verdict verdict-alarm"
        : "verdict";

  return (
    <section className="panel">
      <h2>What the sun says about this cut</h2>
      <p className="hint">
        Shadows measured by {result.model} on the two frames, reconciled against
        the computed position of the sun at {result.latitude}, {result.longitude}.
      </p>

      <div className={tone}>
        {verdict.verdict === "matched" ? <SunIcon size={20} /> : null}
        <div>
          <b>{verdict.headline}</b>
          <p>{verdict.detail}</p>
        </div>
      </div>

      {short ? (
        <div className="verdict">
          <div>
            <b>Read fewer times than it should have been</b>
            <p>
              Each frame is measured {result.reads_expected} times and the middle
              answer kept, because a single reading of one frame varies enough to
              change the verdict.{" "}
              {short.role === "outgoing" ? "The outgoing" : "The incoming"} frame
              managed {short.reads} of {result.reads_expected}, so the answer above
              rests on less evidence than it normally would. Running it again is
              worth more here than usual.
            </p>
          </div>
        </div>
      ) : null}

      {verdict.camera_heading_change_deg != null ? (
        <div className="verdict">
          <div>
            <b>
              The camera moved{" "}
              {Math.abs(verdict.camera_heading_change_deg).toFixed(0)}° between
              these shots
            </b>
            <p>
              Recovered from the shadows rather than from either file, since no
              file carries a heading. This is reported and not judged: a camera
              is supposed to move between shots, and that movement is coverage,
              not a continuity error.
            </p>
          </div>
        </div>
      ) : null}

      <dl className="facts" style={{ marginTop: 22 }}>
        <div className="fact">
          <dt>What the files claim</dt>
          <dd>{verdict.minutes_apart.toFixed(1)} minutes between these two moments</dd>
        </div>
        <div className="fact">
          <dt>How far this check can see</dt>
          <dd>
            {verdict.detectable_from_minutes != null
              ? `a timestamp would have to be more than ${verdict.detectable_from_minutes.toFixed(0)} minutes wrong before these shadows could show it`
              : "nothing, at this time of day: shadow length barely moves, so no timing error would show"}
          </dd>
        </div>
        <div className="fact">
          <dt>What the sun did in that time</dt>
          <dd>
            elevation {verdict.sun_elevation_change_deg > 0 ? "rose" : "fell"}{" "}
            {Math.abs(verdict.sun_elevation_change_deg).toFixed(2)}°, bearing moved{" "}
            {Math.abs(verdict.sun_azimuth_change_deg).toFixed(2)}°
          </dd>
        </div>
        {verdict.expected_length_ratio != null ? (
          <div className="fact">
            <dt>Shadow length across the cut</dt>
            <dd>
              the sun requires a factor of{" "}
              {verdict.expected_length_ratio.toFixed(2)}
              {verdict.observed_length_ratio != null
                ? `, the frames show ${verdict.observed_length_ratio.toFixed(2)}`
                : ", and nothing measurable came back from the frames"}
            </dd>
          </div>
        ) : null}
        {result.frames.map((frame) => (
          <div className="fact" key={frame.role}>
            <dt>
              {frame.role} frame, {frame.moment} UTC
            </dt>
            <dd>
              sun {frame.inferred.sun_elevation_deg.toFixed(1)}° up,{" "}
              {frame.inferred.sun_azimuth_deg.toFixed(1)}° round · shadow should be{" "}
              {frame.inferred.expected_shadow_length_ratio.toFixed(2)}× height
              {frame.inferred.observed_shadow_length_ratio != null
                ? `, measured ${frame.inferred.observed_shadow_length_ratio.toFixed(2)}×`
                : ", not measurable in this frame"}
            </dd>
          </div>
        ))}
      </dl>

      <p className="hint" style={{ marginTop: 18, marginBottom: 0 }}>
        The comparison is a ratio on purpose. Measured on its own, each of these
        shadows is read about forty percent short; measured against each other,
        that error sits in both halves of the fraction and divides out. It is the
        same reason the demo scene compares takes with takes rather than against
        absolute truth.
      </p>
    </section>
  );
}

/** `datetime-local` wants the browser's own timezone, without a suffix. */
function toLocalInput(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

/**
 * Where on the frame the difference is, drawn rather than described.
 *
 * "A dark smudge in C3" asks a person to find the cell, then find the mark
 * inside it. A box drawn over the frame asks them to look. The box comes back
 * in fractions of the frame, so it lands correctly whatever size the image is
 * displayed at, which is why it is positioned in percentages rather than pixels.
 */
function Ground({
  result,
  outgoing,
  incoming,
}: {
  result: GroundResult;
  outgoing: string | null;
  incoming: string | null;
}) {
  const short = result.reads < result.reads_expected;

  if (result.differences.length === 0) {
    return (
      <>
        <div className="verdict">
          <div>
            <b>Nothing on the ground disagrees</b>
            <p>
              Read {result.reads} times under a {result.grid.columns} by{" "}
              {result.grid.rows} grid, no cell was called different by more than
              one reading. That is not a guarantee that nothing changed: small
              marks and anything outside the frame are beyond this, and it is
              looking at the ground only, not at the people or the sky.
            </p>
          </div>
        </div>
        {short ? <ShortRead result={result} /> : null}
      </>
    );
  }

  return (
    <>
      {short ? <ShortRead result={result} /> : null}
      {result.differences.map((difference) => {
        const frame = difference.present_in === "incoming" ? incoming : outgoing;
        return (
          <div key={`${difference.cell}-${difference.present_in}`} className="ground-find">
            <div className="ground-shot">
              {frame ? (
                <>
                  <img src={frame} alt={`${difference.present_in} frame`} />
                  <span
                    className="ground-box"
                    style={{
                      left: `${difference.box.x * 100}%`,
                      top: `${difference.box.y * 100}%`,
                      width: `${difference.box.width * 100}%`,
                      height: `${difference.box.height * 100}%`,
                    }}
                  />
                </>
              ) : null}
            </div>
            <div>
              <b>
                {difference.cell} · present in the {difference.present_in} frame
              </b>
              <p>{difference.what}</p>
              <p className="hint" style={{ marginBottom: 0 }}>
                Seen by {difference.seen_in_reads} of {result.reads} readings.
                Measured by {result.model} on both frames at once, which is why
                it is a comparison rather than two measurements subtracted from
                each other.
              </p>
            </div>
          </div>
        );
      })}
    </>
  );
}

function ShortRead({ result }: { result: GroundResult }) {
  return (
    <div className="verdict">
      <div>
        <b>Read fewer times than it should have been</b>
        <p>
          The pair was read {result.reads} times of {result.reads_expected}, so
          the agreement of {result.agreement_needed} readings that a finding
          needs rested on less evidence than usual. Running it again is worth
          more here than it normally would be.
        </p>
      </div>
    </div>
  );
}

/** "a and b", or "a, b and c". Lists read badly with a trailing comma. */
function joinNicely(parts: string[]): string {
  if (parts.length === 0) return "nothing";
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
