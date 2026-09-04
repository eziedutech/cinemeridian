/**
 * The wait, as a roll of film going past.
 *
 * An analysis takes four to six minutes, which is long enough that a line of
 * text and a spinner leave somebody wondering whether anything is happening,
 * and long enough that they will start editing the form behind it while the
 * work they already asked for is still running. So it covers the page: the
 * inputs are out of reach until the answer arrives, which is the honest state
 * of things rather than a decoration.
 *
 * The cells on the roll are their own frames, not a stock animation. What is
 * scrolling past is the footage being worked on.
 */
import type { TimelineEvent } from "~/components/AgentTimeline";

export function FilmRoll({
  stage,
  elapsed,
  frames,
  events = [],
  note,
  showClock = true,
}: {
  stage: string;
  elapsed: number;
  /** Data URLs of the frames being analysed, in cut order. */
  frames: string[];
  /** What the agent has done so far, newest last. */
  events?: TimelineEvent[];
  /** What the wait is worth, in one sentence. The investigation and the
   *  decoding of a few clips are waits of very different length, and telling
   *  somebody to expect six minutes for a five second job is a lie that costs
   *  their patience. */
  note?: string;
  showClock?: boolean;
}) {
  // The strip is rendered twice end to end and translated by exactly half its
  // width, so the loop closes on itself with no jump. With nothing to show it
  // still runs, because an empty roll turning is better than a still one.
  const cells = frames.length > 0 ? [...frames, ...frames] : [];

  // The last handful only. The full list is worth keeping and is shown after
  // the run; during it, a wall of scrolling text is the same as no text.
  const steps = events.filter((event) => event.kind === "tool_call").slice(-4);

  return (
    <div className="roll-over" role="status" aria-live="polite">
      <div className="roll-card">
        <div className="roll-strip" aria-hidden="true">
          <div className="roll-track">
            {cells.length > 0
              ? cells.map((frame, index) => (
                  <span className="roll-cell" key={index}>
                    <img src={frame} alt="" />
                  </span>
                ))
              : Array.from({ length: 12 }, (_, index) => (
                  <span className="roll-cell empty-cell" key={index} />
                ))}
          </div>
        </div>

        <p className="roll-stage">{stage}…</p>

        {steps.length > 0 ? (
          <ol className="roll-steps">
            {steps.map((step, index) => (
              <li key={index} className={step.ok === false ? "bad" : undefined}>
                <span>{step.text}</span>
                {step.ok === undefined ? null : (
                  <em>{step.ok ? step.outcome || "success" : "failed"}</em>
                )}
              </li>
            ))}
          </ol>
        ) : null}

        <p className="roll-time">
          {showClock ? formatElapsed(elapsed) : null}
          <span>
            {note ??
              "Four to six minutes is normal. The page is held so nothing you already filled in gets changed while this runs."}
          </span>
        </p>
      </div>
    </div>
  );
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}
