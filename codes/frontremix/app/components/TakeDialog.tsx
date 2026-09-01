import { frameUrl } from "~/lib/api";
import {
  SETUP_NAMES,
  deg,
  formatDuration,
  framePaths,
  localDate,
  localTime,
  num,
  takeSeconds,
  type Take,
} from "~/lib/takes";

type Props = {
  take: Take | null;
  bucket: string;
  framesPerTake: number;
  apiBase: string;
  onClose: () => void;
  onPlay: (take: Take) => void;
  onAnalyse: (take: Take) => void;
  analysing: boolean;
};

/**
 * What a take is, before anyone asks whether it cuts.
 *
 * The head and tail are shown together and labelled, because those are the two
 * frames that will ever touch another shot. Everything else here is what a
 * camera report would carry, plus what the sun was doing at the time, which no
 * camera report has ever carried and is the reason this tool exists.
 */
export function TakeDialog({
  take,
  bucket,
  framesPerTake,
  apiBase,
  onClose,
  onPlay,
  onAnalyse,
  analysing,
}: Props) {
  if (!take) return null;

  const frames = framePaths(bucket, take.take_id, framesPerTake);
  const head = frames[0];
  const tail = frames[frames.length - 1];
  const seconds = takeSeconds(take);
  const inCut = take.cut_position != null;

  return (
    <div className="scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <header className="sheet-head">
          <div>
            <h2 className="sheet-title">
              {SETUP_NAMES[take.setup_id] ?? take.setup_id}
              <span className="sheet-take">take {take.take_number}</span>
            </h2>
            <p className="sheet-sub">
              {take.take_id} · shoot day {take.shoot_day} ·{" "}
              {localDate(take.started_at)} at {localTime(take.started_at)} local ·{" "}
              {formatDuration(seconds)}
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="ends">
          <figure>
            <img src={frameUrl(apiBase, head)} alt={`Head frame of ${take.take_id}`} />
            <figcaption>
              <b>Head</b> {localTime(take.started_at)} local
              <span>the frame that meets the previous shot</span>
            </figcaption>
          </figure>
          <figure>
            <img src={frameUrl(apiBase, tail)} alt={`Tail frame of ${take.take_id}`} />
            <figcaption>
              <b>Tail</b> {localTime(take.ended_at)} local
              <span>the frame the next shot cuts from</span>
            </figcaption>
          </figure>
        </div>

        <dl className="facts">
          <Fact label="Source" value={take.source_kind.replace("_", " ")} />
          <Fact label="Lens" value={`${num(take.lens_mm, 0)} mm`} />
          <Fact label="Camera heading" value={deg(take.camera_heading_deg, 0)} />
          <Fact
            label="In this cut"
            value={inCut ? `position ${take.cut_position}` : "not used"}
          />
          <Fact
            label="Sun"
            value={
              take.sun_azimuth_deg == null
                ? "unknown"
                : `${deg(take.sun_azimuth_deg)} az, ${deg(take.sun_elevation_deg)} elevation`
            }
          />
          <Fact
            label="Shadow"
            value={
              take.shadow_len_ratio == null
                ? "unknown"
                : `${num(take.shadow_len_ratio)}× height, ${take.daylight_color_temp_k} K`
            }
          />
          <Fact
            label="Slate"
            value={take.slate_verified ? "verified" : "not verified against the sun"}
            warn={!take.slate_verified}
          />
        </dl>

        {inCut ? null : (
          <p className="sheet-note">
            This take was shot but did not make cut. Analysing will review the
            whole cut; nothing in the report will involve this take.
          </p>
        )}

        <footer className="sheet-foot">
          <button type="button" className="ghost" onClick={() => onPlay(take)}>
            Play take
          </button>
          <button type="button" onClick={() => onAnalyse(take)} disabled={analysing}>
            {analysing ? "Analysing…" : "Analyse the cut"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function Fact({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd className={warn ? "warn" : undefined}>{value}</dd>
    </div>
  );
}
