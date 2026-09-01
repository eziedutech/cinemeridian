import type { Finding } from "~/lib/api";
import { severityRank } from "~/lib/api";

type Props = {
  findings: Finding[];
  selectedId: string | null;
  onSelect: (finding: Finding) => void;
};

const TYPE_LABEL: Record<string, string> = {
  monotonic_violation: "runs backwards",
  cross_take_drift: "drift across the cut",
  physics_mismatch: "disagrees with physics",
  asset_version_drift: "asset version drift",
  volume_plate_drift: "LED plate drift",
  slate_error: "slate may be wrong",
};

export function FindingsMap({ findings, selectedId, onSelect }: Props) {
  if (findings.length === 0) {
    return (
      <div className="panel">
        <h2>Findings</h2>
        <p className="empty">
          Nothing recorded for this cut yet. Run an analysis to populate it.
        </p>
      </div>
    );
  }

  const sorted = [...findings].sort(
    (a, b) => severityRank(a.severity) - severityRank(b.severity),
  );
  const visible = sorted.filter((f) => f.visible_in_cut).length;

  return (
    <div className="panel">
      <h2>Findings</h2>
      <p className="hint">
        {findings.length} recorded, {visible} judged visible in the cut. Every
        one is waiting on a human — the agent recommends and never touches the
        edit.
      </p>

      {sorted.map((finding) => (
        <div
          key={finding.finding_id}
          className={`finding sev-${finding.severity}`}
          role="button"
          tabIndex={0}
          aria-selected={finding.finding_id === selectedId}
          onClick={() => onSelect(finding)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onSelect(finding);
            }
          }}
        >
          <div className="finding-head">
            <span className="finding-type">
              {TYPE_LABEL[finding.finding_type] ?? finding.finding_type}
            </span>
            <span className="severity">{finding.severity}</span>
          </div>

          <div className="finding-takes">
            {finding.take_a}
            {finding.take_b ? ` → ${finding.take_b}` : ""}
            {finding.entity ? `  ·  ${finding.entity}` : ""}
          </div>

          <p className="finding-delta">{finding.observed_delta}</p>

          {finding.recommendation ? (
            <p className="finding-recommendation">{finding.recommendation}</p>
          ) : null}

          {finding.visible_in_cut ? null : (
            <p className="not-visible">
              Recorded, but judged not visible at speed.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
