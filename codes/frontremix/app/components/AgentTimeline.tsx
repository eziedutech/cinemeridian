import { useState } from "react";

export type TimelineEvent = {
  kind: "started" | "tool_call" | "tool_result" | "reasoning" | "error" | "done";
  at: number;
  text: string;
  /** Which tool this row is about, used to pair a result with its call. */
  name?: string;
  /** Whether the action succeeded. Undefined while it is still running. */
  ok?: boolean;
  /** What came back, in a few words. */
  outcome?: string;
  /** How long the action took, in milliseconds. */
  took?: number;
  /** The query or arguments, kept for anyone who wants to check the work. */
  detail?: string;
};

/**
 * What the agent did, in order, one line per action.
 *
 * This panel is the honest half of the demo. A findings list alone could have
 * been produced by a hard-coded pipeline; the questions the agent chose to ask,
 * the candidates it dismissed, and the adjudication it chose to spend are what
 * separate an agent from a script with narration.
 *
 * Which is why the query text is kept rather than thrown away, and also why it
 * is no longer what the reader meets first. A wall of SQL is proof of work to
 * somebody who reads SQL and noise to the editor this is built for, so each
 * action leads with a sentence and a verdict, and the query sits one click
 * underneath.
 */
export function AgentTimeline({
  events,
  running,
  elapsed = 0,
}: {
  events: TimelineEvent[];
  running: boolean;
  elapsed?: number;
}) {
  return (
    <div className="panel">
      <h2>Agent timeline</h2>
      <p className="hint">
        Every question it asked, every physics call, every judgement - as it
        happened, and whether each one worked.
      </p>

      {events.length === 0 ? (
        <p className="empty">
          {running ? (
            <>
              <span className="pulse" aria-hidden="true" />
              Reaching the agent and warming the ClickHouse MCP server. The first
              step usually lands within twenty seconds.
              <span className="elapsed">{elapsed}s</span>
            </>
          ) : (
            "Run an analysis to watch it work."
          )}
        </p>
      ) : (
        <div className="timeline">
          {running ? (
            <div className="tl-row">
              <span className="tl-time">{elapsed}s</span>
              <span className="tl-kind call">
                <span className="pulse" aria-hidden="true" />
                working
              </span>
              <span className="tl-body">still going</span>
            </div>
          ) : null}
          {events.map((event, index) => (
            <Row event={event} key={index} />
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ event }: { event: TimelineEvent }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(event.detail);

  return (
    <div className="tl-row">
      <span className="tl-time">{(event.at / 1000).toFixed(1)}s</span>
      <span className={`tl-kind ${kindClass(event.kind)}`}>{LABEL[event.kind]}</span>
      <span className={`tl-body${event.kind === "reasoning" ? " reasoning" : ""}`}>
        <span className="tl-line">
          <span>{event.text}</span>
          {event.ok === undefined ? null : (
            <span className={`tl-verdict ${event.ok ? "good" : "bad"}`}>
              {event.ok ? "success" : "failed"}
              {event.outcome ? <em>{event.outcome}</em> : null}
              {typeof event.took === "number" && event.took > 0 ? (
                <em>{(event.took / 1000).toFixed(1)}s</em>
              ) : null}
            </span>
          )}
          {hasDetail ? (
            <button type="button" className="tl-more" onClick={() => setOpen(!open)}>
              {open ? "hide the query" : "show the query"}
            </button>
          ) : null}
        </span>
        {hasDetail && open ? <code className="tl-detail">{event.detail}</code> : null}
      </span>
    </div>
  );
}

const LABEL: Record<TimelineEvent["kind"], string> = {
  started: "start",
  tool_call: "did",
  tool_result: "result",
  reasoning: "says",
  error: "error",
  done: "done",
};

function kindClass(kind: TimelineEvent["kind"]): string {
  if (kind === "tool_call") return "call";
  if (kind === "error") return "bad";
  return kind;
}
