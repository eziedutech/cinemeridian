import type { Finding } from "~/lib/api";
import { frameUrl } from "~/lib/api";

/**
 * Turn a take id into the frame that was sampled from it.
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

type Props = {
  finding: Finding | null;
  apiBase: string;
  bucket: string;
  framesPerTake: number;
};

export function EvidencePair({ finding, apiBase, bucket, framesPerTake }: Props) {
  if (!finding) {
    return (
      <div className="panel">
        <h2>Evidence</h2>
        <p className="empty">Select a finding to see the two frames it is about.</p>
      </div>
    );
  }

  // The two frames that actually meet on screen: the outgoing shot's last
  // moment and the incoming shot's first. Comparing a take's head against
  // another take's head would be comparing frames that never touch.
  const uriA = frameUriForTake(bucket, finding.take_a, framesPerTake - 1);
  const uriB = finding.take_b ? frameUriForTake(bucket, finding.take_b, 0) : null;

  return (
    <div className="panel">
      <h2>Evidence</h2>
      <p className="hint">
        The last moment of the outgoing shot beside the first moment of the
        incoming one, which is what an audience actually sees at the cut. Both
        are synthetic: the plate came from a generative model, and every
        variable in dispute was composited on at a value we chose.
      </p>

      <div className="evidence">
        <figure>
          {uriA ? (
            <img src={frameUrl(apiBase, uriA)} alt={`Frame from ${finding.take_a}`} />
          ) : (
            <div className="empty">no frame for {finding.take_a}</div>
          )}
          <figcaption>
            {finding.take_a}
            <br />
            outgoing, tail frame
          </figcaption>
        </figure>

        <figure>
          {uriB ? (
            <img src={frameUrl(apiBase, uriB)} alt={`Frame from ${finding.take_b}`} />
          ) : (
            <div className="empty">single-take finding</div>
          )}
          <figcaption>
            {finding.take_b || "-"}
            <br />
            incoming
          </figcaption>
        </figure>
      </div>

      <dl style={{ marginTop: 18, marginBottom: 0 }}>
        <Row label="Observed" value={finding.observed_delta} />
        <Row label="Physics expected" value={finding.computed_expectation} />
        <Row label="Visual verdict" value={finding.gemini_verdict} />
        <Row label="Recommendation" value={finding.recommendation} />
      </dl>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <dt
        style={{
          fontSize: 11,
          color: "var(--ink-faint)",
        }}
      >
        {label}
      </dt>
      <dd style={{ margin: "3px 0 0", fontSize: 13.5 }}>{value}</dd>
    </div>
  );
}
