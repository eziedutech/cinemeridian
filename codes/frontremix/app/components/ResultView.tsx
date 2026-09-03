import { useState } from "react";

import { CutComparison, type Grid, type GridDifference } from "~/components/CutComparison";
import { FindingsMap } from "~/components/FindingsMap";
import { Info } from "~/components/Info";
import { Report } from "~/components/Report";
import { AgentTimeline, type TimelineEvent } from "~/components/AgentTimeline";
import type { Finding } from "~/lib/api";

export type ResultFact = {
  label: string;
  value: string;
  info?: string;
};

/**
 * A finished review, however it was produced.
 *
 * The front page and the page somebody reaches with their own footage show the
 * same thing in the same order, because they are the same thing: a cut, and
 * what an agent found in it. One is a scene of thirty takes with a scored
 * answer key and the other is two clips a visitor made this morning, and the
 * difference is the data rather than the product.
 *
 * The order is the argument. What it concluded, then the frames with what
 * changed drawn on them, then what it filed for a person to accept or dismiss,
 * then what it had to work with, and last of all how it got there. A review
 * that opens with its own working buries the answer; one that never shows its
 * working could have come from a script with narration.
 */
export function ResultView({
  report,
  findings,
  comparison,
  steps,
  seconds,
  facts,
  selectedId = null,
  onSelectFinding,
  focusTakeId = null,
  onClearFocus,
}: {
  report: string;
  findings: Finding[];
  comparison?: {
    outgoing: string;
    incoming: string;
    grid: Grid;
    differences: GridDifference[];
    fromLabel: string;
    toLabel: string;
  } | null;
  steps: TimelineEvent[];
  seconds: number;
  facts: ResultFact[];
  selectedId?: string | null;
  onSelectFinding?: (finding: Finding) => void;
  focusTakeId?: string | null;
  onClearFocus?: () => void;
}) {
  const [reportOpen, setReportOpen] = useState(false);
  const [showSteps, setShowSteps] = useState(false);

  return (
    <>
      {report ? (
        <section className="panel verdict-panel">
          <h2>
            The answer
            <Info>
              The agent is asked to open with three sentences for an editor
              rather than an engineer: whether these shots can be joined, and if
              not, what is wrong. Everything it worked through to get there is
              in the full report behind the button.
            </Info>
          </h2>
          <Report markdown={shortVersion(report)} />
          <button type="button" onClick={() => setReportOpen(true)}>
            Show the full report
          </button>
        </section>
      ) : null}

      {comparison ? (
        <section className="panel">
          <h2>
            What changed across the cut
            <Info>
              Both frames are laid side by side under one grid and read together,
              so what is marked is a comparison rather than two descriptions
              subtracted from each other. That distinction is the whole method:
              measured separately, the same shadow has come back as 1.2 and as
              2.6 on an unchanged frame.
            </Info>
          </h2>
          <CutComparison
            outgoing={comparison.outgoing}
            incoming={comparison.incoming}
            grid={comparison.grid}
            differences={comparison.differences}
            fromLabel={comparison.fromLabel}
            toLabel={comparison.toLabel}
          />
        </section>
      ) : null}

      {findings.length > 0 ? (
        <section className="panel">
          <h2>
            What it filed
            <Info>
              Each of these is a row the agent wrote into ClickHouse itself,
              through the same MCP server it reads with, and each waits for a
              person to accept or dismiss it. Nothing here is a decision the
              tool has taken on anybody's behalf.
            </Info>
          </h2>
          <FindingsMap
            findings={findings}
            selectedId={selectedId}
            focusTakeId={focusTakeId}
            onSelect={onSelectFinding ?? (() => undefined)}
            onClearFocus={onClearFocus ?? (() => undefined)}
          />
        </section>
      ) : null}

      {facts.length > 0 ? (
        <section className="panel">
          <h2>
            What this run had to work with
            <Info>
              A review is only worth what it was given. Where a fact is absent
              the checks that needed it did not run, and that is a fact about
              the review rather than a gap to fill with an assumption.
            </Info>
          </h2>
          <dl className="facts">
            {facts.map((fact) => (
              <div className="fact" key={fact.label}>
                <dt>
                  {fact.label}
                  {fact.info ? <Info>{fact.info}</Info> : null}
                </dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {steps.length > 0 ? (
        <section className="panel">
          <div className="take-head">
            <h2 style={{ marginBottom: 0 }}>
              How it got there
              <Info>
                The findings above could have been produced by a script with
                narration. These are the questions it chose to ask, in the order
                it asked them, and whether each one worked. That is the
                difference, and it is why they are kept.
              </Info>
            </h2>
            <button
              type="button"
              className="ghost small"
              onClick={() => setShowSteps((open) => !open)}
            >
              {showSteps ? "Hide the steps" : `Show all ${steps.length} steps`}
            </button>
          </div>
          <ProcessSummary steps={steps} seconds={seconds} />
          {showSteps ? (
            <AgentTimeline events={steps} running={false} elapsed={seconds} />
          ) : null}
        </section>
      ) : null}

      {reportOpen ? (
        <div
          className="sheet-over"
          role="dialog"
          aria-modal="true"
          onClick={() => setReportOpen(false)}
        >
          <div className="sheet" onClick={(event) => event.stopPropagation()}>
            <div className="sheet-head">
              <h2>The full report</h2>
              <button
                type="button"
                className="ghost small"
                onClick={() => setReportOpen(false)}
              >
                Close
              </button>
            </div>
            <div className="sheet-body">
              <Report markdown={report} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

/**
 * The run in five numbers.
 *
 * Somebody who has just waited wants to know what happened before they want to
 * know each thing that happened. The full list is a click away and stays there,
 * because it is evidence rather than reading.
 */
function ProcessSummary({ steps, seconds }: { steps: TimelineEvent[]; seconds: number }) {
  const calls = steps.filter((step) => step.kind === "tool_call");
  const count = (name: string) => calls.filter((step) => step.name === name).length;
  const failed = calls.filter((step) => step.ok === false).length;

  const stats: Array<[string, string]> = [
    ["Steps", String(calls.length)],
    ["Questions to the database", String(count("run_query"))],
    ["Frames looked at", String(count("adjudicate_cut") * 2)],
    [
      "Physics calls",
      String(count("compute_light_rig") + count("compute_render_error") + count("find_match_windows")),
    ],
    ["Time", seconds < 60 ? `${Math.round(seconds)}s` : `${Math.floor(seconds / 60)}m ${String(Math.round(seconds) % 60).padStart(2, "0")}s`],
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
 * The three sentences the reader came for, and nothing else.
 *
 * The agent is asked to open with a section headed "The short version". When
 * that is missing the opening paragraphs stand in, which is still closer to an
 * answer than the whole report would be.
 */
export function shortVersion(markdown: string): string {
  const match = /##[ \t]*The short version[ \t]*\n([\s\S]*?)(?=\n#{1,3}[ \t]|$)/i.exec(
    markdown,
  );
  if (match) return match[1].trim();

  const body = markdown.replace(/^#.*$/m, "").trim();
  return body.split(/\n\s*\n/).slice(0, 2).join("\n\n");
}
