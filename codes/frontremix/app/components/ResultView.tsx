import { useEffect, useState } from "react";

import { CutComparison, type Grid, type GridDifference } from "~/components/CutComparison";
import { FindingsMap } from "~/components/FindingsMap";
import { Info } from "~/components/Info";
import { Report } from "~/components/Report";
import { ReportSheet } from "~/components/ReportSheet";
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
export type Comparison = {
  outgoing: string;
  incoming: string;
  grid: Grid;
  differences: GridDifference[];
  fromLabel: string;
  toLabel: string;
  /** What was found at this join, in a few words. Shown on its tab. */
  status?: string;
  /** Which kind of thing that was, so the tab can be marked. */
  tone?: "clean" | "marked" | "scene" | "unread";
};

export type FindingGroup = {
  key: string;
  label: string;
  status: string;
  tone: "clean" | "marked" | "scene" | "unread";
  findings: Finding[];
  /** Why this join produced nothing, when it produced nothing. */
  note: string;
};

export function ResultView({
  report,
  findings,
  comparison,
  comparisons,
  groups,
  steps,
  seconds,
  facts,
  selectedId = null,
  onSelectFinding,
  focusTakeId = null,
  onClearFocus,
  onExport,
  showAnswer = true,
}: {
  report: string;
  findings: Finding[];
  comparison?: Comparison | null;
  /** Every join in the cut, when there is more than one. The reader picks
   *  which to look at; the grid can only hold one pair at a time. */
  comparisons?: Comparison[];
  /** The filed list, split by join. Given this, the section shows a block per
   *  join rather than one flat list, so a join that produced nothing still
   *  says so in its own words. */
  groups?: FindingGroup[];
  steps: TimelineEvent[];
  seconds: number;
  facts: ResultFact[];
  selectedId?: string | null;
  onSelectFinding?: (finding: Finding) => void;
  focusTakeId?: string | null;
  onClearFocus?: () => void;
  /** Offered where the answer is, because a review somebody wants to keep is
   *  one they have just read. */
  onExport?: () => void;
  /** Off where the page puts the answer somewhere of its own, so the reader
   *  is not told the same thing twice on one screen. */
  showAnswer?: boolean;
}) {
  const [reportOpen, setReportOpen] = useState(false);
  const [showSteps, setShowSteps] = useState(false);
  const [joinIndex, setJoinIndex] = useState(0);
  const [steered, setSteered] = useState(false);

  // One join or many: the single `comparison` is the front page, which has one
  // cut to show and no tabs to draw.
  const joins = comparisons ?? (comparison ? [comparison] : []);
  const shown = joins[Math.min(joinIndex, joins.length - 1)] ?? null;
  const quiet = joins.length > 1 ? joins.filter((join) => join.tone === "clean") : [];

  // Open on the join that has something to show, until the reader picks for
  // themselves. Opening on the first join means a cut whose only fault is at
  // the end opens on a pair of frames that agree, which reads as the answer.
  const firstMarked = joins.findIndex((join) => join.tone !== "clean");
  useEffect(() => {
    if (steered) return;
    setJoinIndex(firstMarked === -1 ? 0 : firstMarked);
  }, [firstMarked, steered]);

  return (
    <>
      {report && showAnswer ? (
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

      {shown ? (
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
          {joins.length > 1 ? (
            <>
              <p className="hint" style={{ marginTop: -4 }}>
                Every join was read, one at a time, and each was read once. Pick
                one to see the two frames it puts side by side.
              </p>
              <div className="join-tabs">
                {joins.map((join, index) => (
                  <button
                    key={join.fromLabel + join.toLabel}
                    type="button"
                    className={`join-tab${index === joinIndex ? " on" : ""} tone-${join.tone ?? "clean"}`}
                    aria-pressed={index === joinIndex}
                    onClick={() => {
                      setJoinIndex(index);
                      setSteered(true);
                    }}
                  >
                    <b>
                      {join.fromLabel} <span aria-hidden="true">→</span>{" "}
                      {join.toLabel}
                    </b>
                    {join.status ? <em>{join.status}</em> : null}
                  </button>
                ))}
              </div>
            </>
          ) : null}

          <CutComparison
            outgoing={shown.outgoing}
            incoming={shown.incoming}
            grid={shown.grid}
            differences={shown.differences}
            fromLabel={shown.fromLabel}
            toLabel={shown.toLabel}
          />
        </section>
      ) : null}

      {groups && groups.length > 0 ? (
        <section className="panel">
          <h2>
            What it filed
            <Info>
              Each card is a row the agent wrote into ClickHouse itself, through
              the same MCP server it reads with, and each waits for a person to
              accept or dismiss it. Nothing here is a decision the tool has
              taken on anybody's behalf.
            </Info>
          </h2>
          <p className="hint">
            {findings.length} recorded across {groups.length}{" "}
            {groups.length === 1 ? "join" : "joins"}. Every join is below,
            including the ones that came back with nothing: an empty join is a
            result, and it is not the same as a join nobody looked at.
          </p>

          <div className="join-groups">
            {groups.map((group) => (
              <section className={`join-group tone-${group.tone}`} key={group.key}>
                <header className="join-group-head">
                  <b>{group.label}</b>
                  <em>{group.status}</em>
                </header>
                {group.findings.length > 0 ? (
                  <FindingsMap
                    bare
                    findings={group.findings}
                    selectedId={selectedId}
                    focusTakeId={focusTakeId}
                    onSelect={onSelectFinding ?? (() => undefined)}
                    onClearFocus={onClearFocus ?? (() => undefined)}
                  />
                ) : (
                  <p className="join-group-note">{group.note}</p>
                )}
              </section>
            ))}
          </div>
        </section>
      ) : findings.length > 0 ? (
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

          {/* Why this list is shorter than the cut. A finding names the join it
              belongs to, so a join that appears nowhere here is one the agent
              read and had nothing to say about, and that is worth saying out
              loud rather than leaving to be inferred from an absence. */}
          {quiet.length > 0 ? (
            <p className="hint" style={{ marginTop: 14, marginBottom: 0 }}>
              {quiet.length === 1
                ? "The other join was read and nothing was filed against it: "
                : `The other ${quiet.length} joins were read and nothing was filed against them: `}
              {quiet
                .map((join) => `${join.fromLabel} to ${join.toLabel}`)
                .join(", ")}
              .
            </p>
          ) : null}
        </section>
      ) : null}

      <div className="result-pair">
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
      </div>

      {reportOpen ? (
        <ReportSheet
          markdown={report}
          onExport={onExport}
          onClose={() => setReportOpen(false)}
        />
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
