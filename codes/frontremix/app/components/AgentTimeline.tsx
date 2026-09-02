export type TimelineEvent = {
  kind: "started" | "tool_call" | "tool_result" | "reasoning" | "error" | "done";
  at: number;
  text: string;
};

/**
 * What the agent did, in order.
 *
 * This panel is the honest half of the demo. A findings list alone could have
 * been produced by a hard-coded pipeline; the queries the agent wrote, the
 * candidates it dismissed, and the adjudication it chose to spend are what
 * distinguish an agent from a script with narration. So they are shown, not
 * summarised.
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
        Every query, every physics call, every judgement - as it happened.
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
            <div className="tl-row" key={index}>
              <span className="tl-time">{(event.at / 1000).toFixed(1)}s</span>
              <span className={`tl-kind ${kindClass(event.kind)}`}>
                {LABEL[event.kind]}
              </span>
              <span
                className={`tl-body${event.kind === "reasoning" ? " reasoning" : ""}`}
              >
                {event.text}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const LABEL: Record<TimelineEvent["kind"], string> = {
  started: "start",
  tool_call: "call",
  tool_result: "result",
  reasoning: "says",
  error: "error",
  done: "done",
};

function kindClass(kind: TimelineEvent["kind"]): string {
  if (kind === "tool_call") return "call";
  if (kind === "tool_result") return "result";
  return kind;
}
