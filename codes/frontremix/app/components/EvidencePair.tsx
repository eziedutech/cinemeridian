import { useEffect } from "react";

import { categoryIcon } from "~/components/Icons";
import { frameUrl, type Finding } from "~/lib/api";

/**
 * Turn a take id into one of the frames sampled from it.
 *
 * `sc14_su07_t02` becomes `sc14/su07/t02/f000.jpg`, which is the layout the
 * asset pipeline writes and the perception pass records. Derived rather than
 * stored on the finding, so a finding stays about the discrepancy and not
 * about where a file happens to live.
 */
export function frameUriForTake(
  bucket: string,
  takeId: string,
  frameIndex = 0,
): string | null {
  const parts = takeId.split("_");
  if (parts.length !== 3) return null;
  const [scene, setup, take] = parts;
  const frame = `f${String(frameIndex).padStart(3, "0")}.jpg`;
  return `gs://${bucket}/frames/${scene}/${setup}/${take}/${frame}`;
}

/**
 * Findings that no frame can show.
 *
 * Asset versions and LED volume playback state are not perceptual problems at
 * all: nobody can look at a shot and see that a background model moved from
 * v012 to v013. That is exactly why a continuity tool built only on image
 * comparison cannot touch them, and why the evidence panel says so rather than
 * leaving an empty slot that reads as a broken image.
 */
const NO_VISUAL_EVIDENCE: Record<string, string> = {
  asset_version_drift:
    "No frame can show this. An asset version changing between renders leaves " +
    "no trace a person could see, which is why it survives every review that " +
    "relies on looking.",
  volume_plate_drift:
    "No frame can show this on its own. The LED wall's playback position is " +
    "state, not appearance, and it only becomes visible once two shots are cut " +
    "together.",
};

const TYPE_LABEL: Record<string, string> = {
  monotonic_violation: "Runs backwards",
  cross_take_drift: "Drift across the cut",
  physics_mismatch: "Disagrees with physics",
  asset_version_drift: "Asset version drift",
  volume_plate_drift: "LED plate drift",
  slate_error: "Slate may be wrong",
};

type Props = {
  finding: Finding | null;
  apiBase: string;
  bucket: string;
  framesPerTake: number;
  onClose: () => void;
};

/**
 * The evidence, as a modal over the console.
 *
 * It used to sit in the column beneath the findings list, which meant clicking
 * a card put its evidence somewhere the reader had to scroll to find. The two
 * frames are the whole point of a finding, so they come to the reader instead.
 */
export function EvidencePair({
  finding,
  apiBase,
  bucket,
  framesPerTake,
  onClose,
}: Props) {
  useEffect(() => {
    if (!finding) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [finding, onClose]);

  if (!finding) return null;

  // The two frames that actually meet on screen: the outgoing shot's last
  // moment and the incoming shot's first. Comparing a take's head against
  // another take's head would be comparing frames that never touch.
  const uriA = frameUriForTake(bucket, finding.take_a, framesPerTake - 1);
  const uriB = finding.take_b ? frameUriForTake(bucket, finding.take_b, 0) : null;
  const icon = categoryIcon(finding.finding_type, finding.entity, 14);
  const invisible = NO_VISUAL_EVIDENCE[finding.finding_type];

  return (
    <div className="scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <header className="sheet-head">
          <div>
            <h2 className="sheet-title">
              {TYPE_LABEL[finding.finding_type] ?? finding.finding_type}
              <span className={`ribbon-inline ribbon-${finding.severity}`}>
                {icon.node}
                {finding.severity}
              </span>
            </h2>
            <p className="sheet-sub">
              {finding.take_a}
              {finding.take_b ? ` → ${finding.take_b}` : ""}
              {finding.attribute ? ` · ${finding.attribute}` : ""}
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className={uriB ? "ends" : "ends ends-single"}>
          <figure>
            {uriA ? (
              <img src={frameUrl(apiBase, uriA)} alt={`Frame from ${finding.take_a}`} />
            ) : (
              <div className="empty">no frame for {finding.take_a}</div>
            )}
            <figcaption>
              <b>{uriB ? "Outgoing" : "The shot"}</b> {finding.take_a}
              <span>
                {uriB
                  ? "its last moment, the frame the cut leaves on"
                  : "the shot this finding is about"}
              </span>
            </figcaption>
          </figure>

          {uriB ? (
            <figure>
              <img src={frameUrl(apiBase, uriB)} alt={`Head frame of ${finding.take_b}`} />
              <figcaption>
                <b>Incoming</b> {finding.take_b}
                <span>its first moment, the frame the cut arrives on</span>
              </figcaption>
            </figure>
          ) : (
            <aside className="no-evidence">
              <span className="no-evidence-mark" aria-hidden="true">
                {icon.node}
              </span>
              <p>
                {invisible ??
                  "This finding is about one shot on its own, so there is no " +
                    "second frame to set beside it."}
              </p>
            </aside>
          )}
        </div>

        <dl className="facts evidence-facts">
          <Row label="Observed" value={finding.observed_delta} />
          <Row label="Physics expected" value={finding.computed_expectation} />
          <Row label="Looked at" value={finding.gemini_verdict} />
          <Row label="Recommendation" value={finding.recommendation} highlight />
        </dl>

        <p className="sheet-note">
          {uriB ? "Both frames are" : "This frame is"} synthetic: the plate came
          from a generative model, and every variable in dispute was composited
          on at a value we chose. This finding is a recommendation and has not
          been applied to the edit.
        </p>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  if (!value) return null;
  return (
    <div className={highlight ? "fact fact-highlight" : "fact"}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
