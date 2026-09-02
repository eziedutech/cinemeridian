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
export function FilmRoll({
  stage,
  elapsed,
  frames,
  latest,
}: {
  stage: string;
  elapsed: number;
  /** Data URLs of the frames being analysed, in cut order. */
  frames: string[];
  /** The most recent thing the agent did, if it has started. */
  latest?: string;
}) {
  // The strip is rendered twice end to end and translated by exactly half its
  // width, so the loop closes on itself with no jump. With nothing to show it
  // still runs, because an empty roll turning is better than a still one.
  const cells = frames.length > 0 ? [...frames, ...frames] : [];

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
        {latest ? <p className="roll-latest">{latest}</p> : null}

        <p className="roll-time">
          {formatElapsed(elapsed)}
          <span>
            Four to six minutes is normal. The page is held so nothing you
            already filled in gets changed while this runs.
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
