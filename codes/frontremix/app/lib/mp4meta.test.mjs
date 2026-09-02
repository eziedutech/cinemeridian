/**
 * Drive the MP4 reader against containers built byte by byte here.
 *
 * The parser walks real box structure, so the only honest way to test it is to
 * hand it real box structure. These files carry no video, which is the point:
 * everything the reader looks at lives in `moov`, and a file that is nothing
 * but a correct `moov` is a sharper test than a real clip, because every byte
 * in it is one somebody chose.
 */

import { readMp4Metadata } from "./mp4meta.ts";

const EPOCH_1904 = 2_082_844_800;

function box(type, ...payloads) {
  const body = Buffer.concat(payloads.map((p) => (Buffer.isBuffer(p) ? p : Buffer.from(p))));
  const header = Buffer.alloc(8);
  header.writeUInt32BE(body.length + 8, 0);
  header.write(type, 4, "binary");
  return Buffer.concat([header, body]);
}

/** A box declaring a 64-bit size, the form large files actually use. */
function box64(type, ...payloads) {
  const body = Buffer.concat(payloads.map((p) => (Buffer.isBuffer(p) ? p : Buffer.from(p))));
  const header = Buffer.alloc(16);
  header.writeUInt32BE(1, 0);
  header.write(type, 4, "binary");
  header.writeBigUInt64BE(BigInt(body.length + 16), 8);
  return Buffer.concat([header, body]);
}

function mvhd({ created, timescale = 1000, duration = 7000, version = 0 }) {
  const stamp = created === null ? 0 : Math.floor(created.getTime() / 1000) + EPOCH_1904;
  const tail = Buffer.alloc(80); // rate, volume, matrix and the rest, unread

  if (version === 1) {
    const body = Buffer.alloc(28);
    body.writeUInt32BE(0x01000000, 0); // version 1, no flags
    body.writeBigUInt64BE(BigInt(stamp), 4); // creation
    body.writeBigUInt64BE(BigInt(stamp), 12); // modification
    body.writeUInt32BE(timescale, 20);
    // duration is 64 bit here, and readMvhd reads it four bytes on
    const duration64 = Buffer.alloc(8);
    duration64.writeBigUInt64BE(BigInt(duration));
    return box("mvhd", body, duration64, tail);
  }

  const body = Buffer.alloc(20);
  body.writeUInt32BE(0, 0); // version 0, no flags
  body.writeUInt32BE(stamp, 4);
  body.writeUInt32BE(stamp, 8);
  body.writeUInt32BE(timescale, 12);
  body.writeUInt32BE(duration, 16);
  return box("mvhd", body, tail);
}

/** The location atom a phone writes, ISO 6709 with a language header. */
function xyz(text) {
  const value = Buffer.from(text, "utf8");
  const header = Buffer.alloc(4);
  header.writeUInt16BE(value.length, 0);
  header.writeUInt16BE(0x15c7, 2); // language code, unread
  return box("\u00a9xyz", header, value);
}

function fileFrom(...boxes) {
  return new File([Buffer.concat(boxes)], "clip.mp4", { type: "video/mp4" });
}

const ftyp = box("ftyp", Buffer.from("isomiso2avc1mp41", "binary"));
/** A big payload before `moov`, so the walk has something to skip past. */
const mdat = box("mdat", Buffer.alloc(3 * 1024 * 1024));

const cases = [];
function check(name, fn) {
  cases.push([name, fn]);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

check("reads the recording time and place a phone would write", async () => {
  const when = new Date("2026-12-14T21:19:10Z");
  const file = fileFrom(
    ftyp,
    mdat,
    box("moov", mvhd({ created: when }), box("udta", xyz("+08.7500-083.5000+010.000/"))),
  );

  const meta = await readMp4Metadata(file);
  assert(meta.recordedAt?.toISOString() === when.toISOString(), `time was ${meta.recordedAt}`);
  assert(meta.source.recordedAt === "file", "the time should be credited to the file");
  assert(Math.abs(meta.latitude - 8.75) < 1e-6, `latitude was ${meta.latitude}`);
  assert(Math.abs(meta.longitude + 83.5) < 1e-6, `longitude was ${meta.longitude}`);
  assert(Math.abs(meta.durationSeconds - 7) < 1e-6, `duration was ${meta.durationSeconds}`);
});

check("reads a version 1 header, which is what long files carry", async () => {
  const when = new Date("2026-12-03T21:01:35Z");
  const file = fileFrom(ftyp, box("moov", mvhd({ created: when, version: 1 })));

  const meta = await readMp4Metadata(file);
  assert(meta.recordedAt?.toISOString() === when.toISOString(), `time was ${meta.recordedAt}`);
});

check("walks past a box declaring a 64 bit size", async () => {
  const when = new Date("2026-12-14T22:22:10Z");
  const file = fileFrom(
    ftyp,
    box64("mdat", Buffer.alloc(2048)),
    box("moov", mvhd({ created: when })),
  );

  const meta = await readMp4Metadata(file);
  assert(meta.recordedAt?.toISOString() === when.toISOString(), `time was ${meta.recordedAt}`);
});

check("finds the location atom nested under meta and ilst", async () => {
  const meta4 = box("meta", Buffer.alloc(4), box("ilst", xyz("-33.8688+151.2093/")));
  const file = fileFrom(ftyp, box("moov", box("udta", meta4)));

  const meta = await readMp4Metadata(file);
  assert(Math.abs(meta.latitude + 33.8688) < 1e-6, `latitude was ${meta.latitude}`);
  assert(Math.abs(meta.longitude - 151.2093) < 1e-6, `longitude was ${meta.longitude}`);
});

check("says nothing rather than guessing when the file carries nothing", async () => {
  const file = fileFrom(ftyp, box("moov", mvhd({ created: null })));

  const meta = await readMp4Metadata(file);
  assert(meta.recordedAt === null, "a zero creation time is absent, not 1904");
  assert(meta.latitude === null && meta.longitude === null, "no position was written");
  assert(meta.source.recordedAt === null, "nothing should be credited to the file");
});

check("survives a file that is not an MP4 at all", async () => {
  const file = new File([Buffer.from("this is a text file, not a video")], "notes.txt");

  const meta = await readMp4Metadata(file);
  assert(meta.recordedAt === null && meta.latitude === null, "should come back empty");
});

check("refuses a position outside the world", async () => {
  const file = fileFrom(ftyp, box("moov", box("udta", xyz("+99.0000-200.0000/"))));

  const meta = await readMp4Metadata(file);
  assert(meta.latitude === null, `latitude ${meta.latitude} should have been refused`);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`  lulus  ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  GAGAL  ${name}\n         ${error.message}`);
  }
}
console.log(`\n${cases.length - failed} lulus, ${failed} gagal`);
process.exit(failed ? 1 : 0);
