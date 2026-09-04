import { useEffect } from "react";

/**
 * What this tool catches, what it only marks, and what it does not see.
 *
 * Written down because the honest answer to "can it catch X" is three
 * different answers, and a visitor who cannot tell them apart will either
 * trust it too far or dismiss it. Filed means the physics was violated and the
 * agent argued it. Marked means two frames disagree and nobody claimed to know
 * why. Out of reach means the method is wrong for it, which is a different
 * thing from the method being weak.
 *
 * Kept as data rather than markup so the same three lists can be read here and
 * quoted in the README without the two drifting apart.
 */

type Row = { anomaly: string; now: string; gap: string };

const FILED: Row[] = [
  {
    anomaly: "The sun moved too far across a join",
    now: "NOAA solar geometry computed for the time and place given, with no vision involved at all",
    gap: "Needs a position and a clock; without them this check is silent",
  },
  {
    anomaly: "A shadow swung further than the sun did",
    now: "The measured swing of the shadow against the computed swing of the sun",
    gap: "Cannot separate a camera that moved from a time that moved, so both are put to the reader",
  },
  {
    anomaly: "Something that only accumulates, running backwards",
    now: "Footprints, litter, tyre tracks: a count that falls means the order is wrong or the continuity is",
    gap: "Rests on the model counting objects steadily, which is the weakest reading it does",
  },
  {
    anomaly: "The frames against the file's own timestamp",
    now: "A shadow measured at a moment the sun was below the horizon, which no tolerance explains",
    gap: "Needs a sun shadow to exist; an interior at night has nothing to say",
  },
  {
    anomaly: "The light itself changing across a join",
    now: "Direct beam, shaded daylight, lamps or mixed, read from the geometry of the shadows rather than the warmth of the picture",
    gap: "Cannot tell a deliberate lighting change from a mistake",
  },
  {
    anomaly: "Anything measured on both sides that then changed",
    now: "The tail of the outgoing take against the head of the incoming one, which is the moment the audience sees as one",
    gap: "Only for things already measured; it finds nothing that was never read",
  },
  {
    anomaly: "Measured on one side of a join only",
    now: "Surfaced and labelled weak on purpose",
    gap: "One reading per frame, so a missing row cannot be told from a thing that went unmentioned",
  },
];

const MARKED: Row[] = [
  {
    anomaly: "A loose object on the ground, in the room, on the furniture",
    now: "The grid names the cell and a box is drawn on the frame",
    gap: "Never written to ClickHouse, never handed to the agent, never filed",
  },
  {
    anomaly: "What a person is holding changes: a bottle that becomes a can",
    now: "Likely marked, being large in frame and plainly separate from the body",
    gap: "The tool does not know the object is held, so it cannot argue it",
  },
  {
    anomaly: "A fixed thing that moved: a wall clock, a picture",
    now: "Told apart from a movable thing, which is the heavier of the two marks",
    gap: "Stops on the page, the same as the rest",
  },
  {
    anomaly: "A cut between scenes rather than inside one",
    now: "Judged from the place, and continuity is deliberately not checked across it",
    gap: "Judged from one pair of frames, so two similar places can be read as one",
  },
];

const OUT: Row[] = [
  {
    anomaly: "A shawl on the other shoulder, a braid that sits differently",
    now: "Nothing",
    gap: "Attached to a person, and people are ignored on purpose",
  },
  {
    anomaly: "Two buttons that become three, a badge that appears",
    now: "Nothing",
    gap: "Too small for a grid; needs the figure found and the garment aligned",
  },
  {
    anomaly: "Hair, make-up, sweat, tears",
    now: "Nothing",
    gap: "The same, and finer still",
  },
  {
    anomaly: "A fault in the middle of a clip",
    now: "Nothing",
    gap: "Only the first and last frames are read, because those are what a cut joins",
  },
  {
    anomaly: "Eyeline, and the 180 degree line",
    now: "Nothing",
    gap: "Needs where people are looking and how the space is laid out, not how it is lit",
  },
  {
    anomaly: "Movement, hand position, direction of travel",
    now: "Nothing",
    gap: "Needs motion read, not two still frames",
  },
  {
    anomaly: "Dialogue and sound",
    now: "Nothing",
    gap: "No audio is read at any point",
  },
  {
    anomaly: "How many people are in the background",
    now: "Nothing",
    gap: "People are ignored",
  },
  {
    anomaly: "Crew, equipment or a reflection in shot",
    now: "Nothing",
    gap: "Not a comparison of two sides but a judgement about one frame",
  },
  {
    anomaly: "Wet and dry ground, rain, snow",
    now: "Partly: a change on the ground can be marked",
    gap: "Not reasoned about as weather, and not joined to the weather rows",
  },
  {
    anomaly: "Lens, framing, colour, grade",
    now: "Nothing",
    gap: "Continuity of the picture rather than of the world",
  },
];

export function ScopeSheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-over" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="sheet scope-sheet" onClick={(event) => event.stopPropagation()}>
        <div className="sheet-head">
          <h2>What it catches, and what it does not</h2>
          <button type="button" className="ghost small" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="sheet-body">
          <p className="scope-lead">
            Three answers, not one. It accuses only what physics can contradict,
            it marks what changed without claiming to know why, and it says
            nothing at all about the things its method is wrong for.
          </p>

          <Table
            title="Filed as a finding"
            note="The physics was violated and the agent argued it, with the numbers on the record."
            rows={FILED}
          />
          <Table
            title="Marked, but not accused"
            note="Two frames disagree and the difference is shown on them. Nobody claims to know why, so nothing is filed."
            rows={MARKED}
          />
          <Table
            title="Out of reach"
            note="Not weakness so much as the wrong instrument. Each of these needs a method this tool does not use."
            rows={OUT}
          />
        </div>
      </div>
    </div>
  );
}

function Table({ title, note, rows }: { title: string; note: string; rows: Row[] }) {
  return (
    <section className="scope-block">
      <h3>{title}</h3>
      <p className="scope-note">{note}</p>
      <div className="scope-table">
        <table>
          <thead>
            <tr>
              <th>Anomaly</th>
              <th>What it can do now</th>
              <th>What it still cannot do</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.anomaly}>
                <td>
                  <b>{row.anomaly}</b>
                </td>
                <td>{row.now}</td>
                <td>{row.gap}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
