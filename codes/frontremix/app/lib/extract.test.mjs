/**
 * Which moments get lifted out of a clip.
 *
 * The decoding needs a browser, but the choosing does not, and the choosing is
 * where a mistake would be silent. A general video-to-image tool picks the
 * sharpest frames; this picks the two that a cut would actually join, and an
 * off-by-one here would hand the physics an interior frame while the interface
 * went on calling it the first or the last.
 */

import { planTimes } from "./extract.ts";

const cases = [];
const check = (name, fn) => cases.push([name, fn]);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const TAIL_MARGIN = 0.06;

check("the first frame is the clip's own first", () => {
  const times = planTimes(30, 3, 0.5);
  const head = times.filter((t) => t.role === "head");
  assert(head[0].at === 0, `first head frame was at ${head[0].at}`);
});

check("the last frame is the clip's own last", () => {
  const duration = 30;
  const times = planTimes(duration, 3, 0.5);
  const tail = times.filter((t) => t.role === "tail");
  const last = tail[tail.length - 1];
  assert(
    Math.abs(last.at - (duration - TAIL_MARGIN)) < 1e-9,
    `last tail frame was at ${last.at}, not just inside the end`,
  );
});

check("head frames run forwards and tail frames run forwards", () => {
  const times = planTimes(30, 3, 0.5);
  const head = times.filter((t) => t.role === "head").map((t) => t.at);
  const tail = times.filter((t) => t.role === "tail").map((t) => t.at);

  for (const list of [head, tail]) {
    for (let i = 1; i < list.length; i += 1) {
      assert(list[i] > list[i - 1], `not in order: ${list.join(", ")}`);
    }
  }
  assert(head.at(-1) < tail[0], "the head and tail groups should not overlap");
});

check("asks for the number of frames it was told to", () => {
  const times = planTimes(30, 4, 0.5);
  assert(times.length === 8, `got ${times.length} frames`);
  assert(times.filter((t) => t.role === "head").length === 4, "wrong head count");
  assert(times.filter((t) => t.role === "tail").length === 4, "wrong tail count");
});

check("a very short clip does not fold its ends into each other", () => {
  // Two seconds at the requested half second spacing would put the third head
  // frame past the first tail frame. The spacing has to give way, not the ends.
  const times = planTimes(2, 3, 0.5);
  const head = times.filter((t) => t.role === "head").map((t) => t.at);
  const tail = times.filter((t) => t.role === "tail").map((t) => t.at);

  assert(head[0] === 0, `first frame was at ${head[0]}`);
  assert(head.at(-1) <= tail[0], `head ran past the tail: ${head.at(-1)} > ${tail[0]}`);
  assert(times.every((t) => t.at >= 0 && t.at <= 2), "a frame was planned outside the clip");
});

check("a clip barely longer than a single frame stays inside itself", () => {
  const times = planTimes(0.2, 3, 0.5);
  assert(times.every((t) => t.at >= 0 && t.at <= 0.2), "planned outside a very short clip");
  assert(times.every((t) => Number.isFinite(t.at)), "planned a time that is not a number");
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`  lulus  ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  GAGAL  ${name}\n         ${error.message}`);
  }
}
console.log(`\n${cases.length - failed} lulus, ${failed} gagal`);
process.exit(failed ? 1 : 0);
