/**
 * Put both frames in one picture, under one grid.
 *
 * This is the whole method, and it is a drawing problem rather than a model
 * problem. Asking a model to describe two frames separately and then
 * differencing the descriptions is what produced every wobble measured in this
 * project: the same shadow read 1.2 and 2.6 on the same frame. Put the two
 * frames side by side under a shared grid and it is no longer measuring twice,
 * it is comparing, and on a planted mark it named the same cell eight times out
 * of eight.
 *
 * Composed here rather than on the server for the same reason the frames are
 * cut here: what leaves the machine should be the smallest thing that answers
 * the question, and never the footage.
 */

export type Grid = { columns: number; rows: number };

/** Four across and three down. Not three by three, which on 16:9 footage makes
 *  cells wide and flat, so a named cell points at a shape nobody can picture.
 *  Finer than this buys nothing: the model already volunteers where inside a
 *  cell a mark sits, so precision comes from asking, not from more lines. */
export const DEFAULT_GRID: Grid = { columns: 4, rows: 3 };

const LABEL_BAND = 34;
const GAP = 24;
const LINE = "rgba(255, 90, 60, 0.9)";
const LABEL = "rgba(255, 200, 120, 0.95)";

export async function composePair(
  outgoing: Blob,
  incoming: Blob,
  grid: Grid = DEFAULT_GRID,
): Promise<Blob> {
  const [left, right] = await Promise.all([toImage(outgoing), toImage(incoming)]);

  // Both halves are drawn at the same size, so a cell means the same fraction
  // of each frame even when the two clips were shot at different resolutions.
  const width = Math.min(left.width, right.width, 960);
  const height = Math.round((width * left.height) / left.width);

  const canvas = document.createElement("canvas");
  canvas.width = width * 2 + GAP;
  canvas.height = height + LABEL_BAND;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser will not give us a canvas to draw on.");

  context.fillStyle = "#000";
  context.fillRect(0, 0, canvas.width, canvas.height);

  drawHalf(context, left, 0, width, height, grid, "LEFT: outgoing");
  drawHalf(context, right, width + GAP, width, height, grid, "RIGHT: incoming");

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Could not encode the pair."))),
      "image/jpeg",
      0.92,
    );
  });
}

function drawHalf(
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  originX: number,
  width: number,
  height: number,
  grid: Grid,
  title: string,
): void {
  context.drawImage(image, originX, LABEL_BAND, width, height);

  context.font = "14px system-ui, sans-serif";
  context.fillStyle = "#fff";
  context.fillText(title, originX + 8, 22);

  const cellWidth = width / grid.columns;
  const cellHeight = height / grid.rows;

  context.strokeStyle = LINE;
  context.lineWidth = 2;
  for (let column = 1; column < grid.columns; column += 1) {
    const x = originX + column * cellWidth;
    context.beginPath();
    context.moveTo(x, LABEL_BAND);
    context.lineTo(x, LABEL_BAND + height);
    context.stroke();
  }
  for (let row = 1; row < grid.rows; row += 1) {
    const y = LABEL_BAND + row * cellHeight;
    context.beginPath();
    context.moveTo(originX, y);
    context.lineTo(originX + width, y);
    context.stroke();
  }

  context.font = "13px ui-monospace, monospace";
  context.fillStyle = LABEL;
  for (let row = 0; row < grid.rows; row += 1) {
    for (let column = 0; column < grid.columns; column += 1) {
      const label = `${String.fromCharCode(65 + column)}${row + 1}`;
      context.fillText(
        label,
        originX + column * cellWidth + 7,
        LABEL_BAND + row * cellHeight + 17,
      );
    }
  }
}

function toImage(blob: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("A frame could not be drawn."));
    };
    image.src = url;
  });
}
