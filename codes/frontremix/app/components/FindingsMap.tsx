import { categoryIcon } from "~/components/Icons";
import { severityRank, takeLabel, type Finding } from "~/lib/api";

type Props = {
  findings: Finding[];
  selectedId: string | null;
  focusTakeId: string | null;
  onSelect: (finding: Finding) => void;
  onClearFocus: () => void;
  /** Just the cards. For a page that has written its own heading and is
   *  showing one group of findings among several. */
  bare?: boolean;
};

const TYPE_LABEL: Record<string, string> = {
  monotonic_violation: "Runs backwards",
  cross_take_drift: "Drift across the cut",
  physics_mismatch: "Disagrees with physics",
  asset_version_drift: "Asset version drift",
  volume_plate_drift: "LED plate drift",
  slate_error: "Slate may be wrong",
};

/**
 * The findings, as cards a person can scan.
 *
 * Each card carries a ribbon rather than a coloured edge. A ribbon reads as a
 * tab on a physical document - something filed and waiting - which is what a
 * finding is: it sits in a queue until a human decides. The icon in it names
 * the physical thing in dispute, because "medium severity" tells an editor
 * nothing and "the tide moved" tells them where to look.
 */
export function FindingsMap({
  findings,
  selectedId,
  focusTakeId,
  onSelect,
  onClearFocus,
  bare = false,
}: Props) {
  const shown = focusTakeId
    ? findings.filter(
        (f) => f.take_a === focusTakeId || f.take_b === focusTakeId,
      )
    : findings;

  const sorted = [...shown].sort(
    (a, b) => severityRank(a.severity) - severityRank(b.severity),
  );

  const listing = (
    <ol
      className="findings"
      style={{ "--columns": Math.min(3, sorted.length) } as React.CSSProperties}
    >
      {sorted.map((finding) => {
        const icon = categoryIcon(finding.finding_type, finding.entity, 15);
        return <FindingCard key={finding.finding_id} finding={finding} icon={icon} selectedId={selectedId} onSelect={onSelect} />;
      })}
    </ol>
  );

  // A group inside somebody else's panel: no heading and no empty state,
  // because the page around it has already said which join this is and what
  // became of it.
  if (bare) return listing;

  // Not `report`: that class is the agent's prose, capped at 82ch for reading,
  // and wearing it here quietly held the review queue to half the page with
  // nothing beside it.
  return (
    <section className="panel findings-panel" id="findings">
      <header className="findings-head">
        <div>
          <h2>Findings</h2>
          <p className="hint">
            {findings.length} recorded,{" "}
            {findings.filter((f) => f.visible_in_cut).length} judged visible in
            the cut. Every one is waiting on a human: the agent recommends and
            never touches the edit.
          </p>
        </div>
      </header>

      {focusTakeId ? (
        <p className="focus-bar">
          Showing {sorted.length} finding{sorted.length === 1 ? "" : "s"} that
          involve <b>{focusTakeId}</b>.
          <button type="button" className="linkish" onClick={onClearFocus}>
            show all
          </button>
        </p>
      ) : null}

      {sorted.length === 0 ? (
        <p className="empty">
          {focusTakeId
            ? "No finding involves this take. It cut cleanly against its neighbours."
            : "Nothing recorded for this cut yet. Run an analysis to populate it."}
        </p>
      ) : (
        listing
      )}
    </section>
  );
}

/**
 * One finding, as a card.
 *
 * Its own component so the list can be rendered either inside this panel or,
 * in bare form, inside a page that groups findings by the join they belong to.
 */
function FindingCard({
  finding,
  icon,
  selectedId,
  onSelect,
}: {
  finding: Finding;
  icon: { node: JSX.Element; label: string };
  selectedId: string | null;
  onSelect: (finding: Finding) => void;
}) {
  return (
            <li key={finding.finding_id}>
              <article
                className="finding"
                aria-selected={finding.finding_id === selectedId}
                tabIndex={0}
                onClick={() => onSelect(finding)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(finding);
                  }
                }}
              >
                <span className={`ribbon ribbon-${finding.severity}`}>
                  {icon.node}
                  <span className="ribbon-text">{icon.label}</span>
                </span>

                <div className="finding-body">
                  <h3 className="finding-title">
                    {TYPE_LABEL[finding.finding_type] ?? finding.finding_type}
                  </h3>

                  <p className="finding-takes">
                    {takeLabel(finding.take_a)}
                    {finding.take_b ? (
                      <>
                        <span className="arrow">→</span>
                        {takeLabel(finding.take_b)}
                      </>
                    ) : null}
                    {finding.attribute ? (
                      <span className="attr">{finding.attribute}</span>
                    ) : null}
                  </p>

                  <p className="finding-delta">{finding.observed_delta}</p>

                  {finding.computed_expectation ? (
                    <p className="finding-line">
                      <span>Physics expected</span>
                      {finding.computed_expectation}
                    </p>
                  ) : null}

                  {finding.gemini_verdict ? (
                    <p className="finding-line">
                      <span>Looked at</span>
                      {finding.gemini_verdict}
                    </p>
                  ) : null}

                  {finding.recommendation ? (
                    <p className="finding-recommendation">
                      {finding.recommendation}
                    </p>
                  ) : null}

                  <p className="finding-foot">
                    <span className={`sev sev-${finding.severity}`}>
                      {finding.severity}
                    </span>
                    {finding.visible_in_cut ? (
                      <span>visible at speed</span>
                    ) : (
                      <span className="muted">not visible at speed</span>
                    )}
                    <span className="muted">awaiting human review</span>
                  </p>
                </div>
              </article>
            </li>
  );
}
