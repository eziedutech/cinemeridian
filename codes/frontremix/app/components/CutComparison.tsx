/**
 * The two frames a cut joins, side by side, with what changed drawn on them.
 *
 * A finding that reads "cross_take_drift on lamps.on, 100% coverage" is a
 * sentence about a database. What an editor needs is the two pictures and a
 * box around the thing that moved, which is what they would have done
 * themselves with a grease pencil on a contact sheet.
 *
 * The grid is drawn here rather than baked into the image so the same frames
 * can be shown plain elsewhere, and because a cell has to be highlightable
 * after the fact: which one matters is not known until the model has answered.
 */

export type GridDifference = {
  cell: string;
  what: string;
  present_in: "outgoing" | "incoming";
  box: { x: number; y: number; width: number; height: number };
};

export type Grid = { columns: number; rows: number };

export function CutComparison({
  outgoing,
  incoming,
  grid,
  differences,
  fromTake,
  toTake,
}: {
  outgoing: string;
  incoming: string;
  grid: Grid;
  differences: GridDifference[];
  fromTake: number;
  toTake: number;
}) {
  const marked = (side: "outgoing" | "incoming") =>
    differences.filter((difference) => difference.present_in === side);

  return (
    <div className="cut-compare">
      <div className="cut-heads">
        <span>
          Take {fromTake}, last frame
          <em>what the cut leaves</em>
        </span>
        <span>
          Take {toTake}, first frame
          <em>what the cut lands on</em>
        </span>
      </div>

      <div className="cut-frames">
        {(
          [
            ["outgoing", outgoing],
            ["incoming", incoming],
          ] as const
        ).map(([side, source]) => (
          <div className="cut-frame" key={side}>
            <img src={source} alt={`${side} frame`} />
            <GridLines grid={grid} />
            {marked(side).map((difference) => (
              <span
                key={difference.cell}
                className={`cut-box ${side === "incoming" ? "appeared" : "gone"}`}
                style={{
                  left: `${difference.box.x * 100}%`,
                  top: `${difference.box.y * 100}%`,
                  width: `${difference.box.width * 100}%`,
                  height: `${difference.box.height * 100}%`,
                }}
              >
                <b>{difference.cell}</b>
              </span>
            ))}
          </div>
        ))}
      </div>

      {differences.length > 0 ? (
        <ul className="cut-legend">
          {differences.map((difference) => (
            <li key={difference.cell}>
              <span
                className={difference.present_in === "incoming" ? "appeared" : "gone"}
              >
                {difference.cell}
              </span>
              {difference.what}
              <em>
                {difference.present_in === "incoming"
                  ? "appears after the cut"
                  : "is gone after the cut"}
              </em>
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint" style={{ marginBottom: 0 }}>
          Nothing on the ground was called different by more than one reading.
          Small marks and anything outside the frame are beyond this.
        </p>
      )}
    </div>
  );
}

/** The same grid the model was shown, so a named cell can be found by eye. */
function GridLines({ grid }: { grid: Grid }) {
  const lines = [];
  for (let column = 1; column < grid.columns; column += 1) {
    lines.push(
      <span
        className="cut-rule v"
        key={`v${column}`}
        style={{ left: `${(column / grid.columns) * 100}%` }}
      />,
    );
  }
  for (let row = 1; row < grid.rows; row += 1) {
    lines.push(
      <span
        className="cut-rule h"
        key={`h${row}`}
        style={{ top: `${(row / grid.rows) * 100}%` }}
      />,
    );
  }
  return <>{lines}</>;
}
