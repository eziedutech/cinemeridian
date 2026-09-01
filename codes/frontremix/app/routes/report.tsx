import { useEffect } from "react";
import { json, type LinksFunction, type LoaderFunctionArgs } from "@remix-run/node";
import { useLoaderData } from "@remix-run/react";

import { categoryIcon } from "~/components/Icons";
import { fetchFindings, severityRank, type Finding } from "~/lib/api";
import styles from "~/styles/report.css?url";

const SCENE_ID = "sc14";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: styles }];

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const editVersion = url.searchParams.get("edit") ?? "v14";
  const { findings, error } = await fetchFindings(editVersion, SCENE_ID);
  return json({
    editVersion,
    findings,
    error,
    generatedAt: new Date().toISOString().replace("T", " ").slice(0, 16),
  });
}

const TYPE_LABEL: Record<string, string> = {
  monotonic_violation: "Runs backwards",
  cross_take_drift: "Drift across the cut",
  physics_mismatch: "Disagrees with physics",
  asset_version_drift: "Asset version drift",
  volume_plate_drift: "LED plate drift",
  slate_error: "Slate may be wrong",
};

/**
 * The report as a document.
 *
 * Opened in its own tab so the console keeps its state, and printed with the
 * browser's own dialogue rather than rendered to PDF on the server. That keeps
 * the text as text - searchable, selectable, and copyable out of the PDF - and
 * keeps a rendering engine out of the container, where it would cost cold
 * start time on every deploy for something used once a session.
 */
export default function Report() {
  const { editVersion, findings, error, generatedAt } = useLoaderData<typeof loader>();

  // Opened from the console specifically to be printed, so offer the dialogue
  // once the page has settled. Never automatically: a print dialogue that
  // appears before the reader has seen the page is a page nobody trusts.
  useEffect(() => {
    document.title = `CineMeridian - continuity report - scene ${SCENE_ID} cut ${editVersion}`;
  }, [editVersion]);

  const sorted = [...findings].sort(
    (a, b) => severityRank(a.severity) - severityRank(b.severity),
  );
  const visible = findings.filter((f) => f.visible_in_cut).length;
  const high = findings.filter((f) => f.severity === "high").length;

  return (
    <div className="page">
      <div className="toolbar">
        <button type="button" className="ghost" onClick={() => window.close()}>
          Close
        </button>
        <button type="button" onClick={() => window.print()}>
          Save as PDF
        </button>
      </div>

      <header className="head">
        <h1>
          Cine<span>Meridian</span> continuity report
        </h1>
        <p className="sub">
          <em>The Tide Line</em>, scene {SCENE_ID}, cut {editVersion}. Every
          finding below is a recommendation awaiting human review.
        </p>

        <dl className="meta">
          <div>
            <dt>Production</dt>
            <dd>prod_tideline</dd>
          </div>
          <div>
            <dt>Location</dt>
            <dd>8.75°N 83.5°W</dd>
          </div>
          <div>
            <dt>Shot</dt>
            <dd>3 to 15 Dec 2026</dd>
          </div>
          <div>
            <dt>Report generated</dt>
            <dd>{generatedAt} UTC</dd>
          </div>
        </dl>
      </header>

      <div className="summary">
        <div>
          <div className="n">{findings.length}</div>
          <div className="l">findings recorded</div>
        </div>
        <div>
          <div className="n">{visible}</div>
          <div className="l">visible at speed</div>
        </div>
        <div>
          <div className="n">{high}</div>
          <div className="l">high severity</div>
        </div>
        <div>
          <div className="n">0</div>
          <div className="l">applied to the edit</div>
        </div>
      </div>

      <h2 className="section">Findings</h2>

      {error ? <p className="empty">Could not reach the API: {error}</p> : null}

      {sorted.length === 0 ? (
        <p className="empty">
          Nothing recorded for this cut. Run an analysis in the console first.
        </p>
      ) : (
        <ol className="findings">
          {sorted.map((finding) => (
            <li key={finding.finding_id}>
              <FindingCard finding={finding} />
            </li>
          ))}
        </ol>
      )}

      <p className="note">
        <strong>What is real here.</strong> Sun and moon positions are computed
        with the NOAA solar position algorithm and are real astronomy. The tide
        and the weather telemetry are <strong>simulated</strong>, from two
        harmonic constituents and a physical afternoon model, and are not a
        forecast for any place. All footage is synthetic and self-made; no film
        or broadcast material is used, and <em>The Tide Line</em> is not a real
        production. The agent only ever recommends: it does not modify the edit,
        submit a render, or mark its own findings reviewed.
      </p>
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const icon = categoryIcon(finding.finding_type, finding.entity, 14);
  return (
    <article className="finding">
      <span className={`ribbon ribbon-${finding.severity}`}>
        {icon.node}
        {icon.label}
      </span>

      <div className="finding-body">
        <h3>{TYPE_LABEL[finding.finding_type] ?? finding.finding_type}</h3>

        <p className="takes">
          {finding.take_a}
          {finding.take_b ? (
            <>
              <span className="arrow">→</span>
              {finding.take_b}
            </>
          ) : null}
          {finding.attribute ? ` · ${finding.attribute}` : ""}
        </p>

        <p className="delta">{finding.observed_delta}</p>

        {finding.computed_expectation ? (
          <p className="line">
            <span>Physics expected</span>
            {finding.computed_expectation}
          </p>
        ) : null}

        {finding.gemini_verdict ? (
          <p className="line">
            <span>Looked at</span>
            {finding.gemini_verdict}
          </p>
        ) : null}

        {finding.recommendation ? (
          <p className="recommendation">{finding.recommendation}</p>
        ) : null}

        <p className="foot">
          <span className={`sev sev-${finding.severity}`}>{finding.severity}</span>
          <span>
            {finding.visible_in_cut ? "visible at speed" : "not visible at speed"}
          </span>
          <span>awaiting human review</span>
        </p>
      </div>
    </article>
  );
}
