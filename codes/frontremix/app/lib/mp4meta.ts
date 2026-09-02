/**
 * Read what an MP4 already knows about itself.
 *
 * A video file usually carries the moment it was recorded, in the `mvhd` box,
 * and a phone usually writes where it stood, in a `©xyz` atom under `udta`.
 * Neither is exposed by any browser API, so the boxes are walked by hand -
 * which is a few dozen lines and saves asking a person to type in facts their
 * own file is already holding.
 *
 * What no file carries is which way the camera was pointing. That one the
 * physics engine recovers from the shadow; see `infer_capture` on the server.
 *
 * Only box headers are read until `moov` is found, so a hundred megabyte file
 * costs a handful of small range reads rather than a hundred megabyte one.
 */

export type Mp4Metadata = {
  recordedAt: Date | null;
  latitude: number | null;
  longitude: number | null;
  durationSeconds: number | null;
  /** Which facts came from the file, for showing the user what was read. */
  source: {
    recordedAt: "file" | null;
    location: "file" | null;
  };
};

/** MP4 timestamps count seconds from 1904-01-01, not 1970-01-01. */
const EPOCH_1904_TO_1970_SECONDS = 2_082_844_800;

/** Boxes that hold other boxes, so the walk descends rather than skipping. */
const CONTAINERS = new Set(["moov", "udta", "meta", "trak", "mdia", "ilst"]);

const MAX_MOOV_BYTES = 24 * 1024 * 1024;

export async function readMp4Metadata(file: File): Promise<Mp4Metadata> {
  const empty: Mp4Metadata = {
    recordedAt: null,
    latitude: null,
    longitude: null,
    durationSeconds: null,
    source: { recordedAt: null, location: null },
  };

  try {
    const moov = await findMoov(file);
    if (!moov) return empty;
    return parseMoov(moov);
  } catch {
    // A file we cannot parse is not a failure worth stopping for: the form
    // simply asks for what could not be read.
    return empty;
  }
}

/** Walk the top-level boxes, reading only headers, until `moov` turns up. */
async function findMoov(file: File): Promise<DataView | null> {
  let offset = 0;

  while (offset + 8 <= file.size) {
    const header = new DataView(await file.slice(offset, offset + 16).arrayBuffer());
    if (header.byteLength < 8) return null;

    let size = header.getUint32(0);
    const type = typeAt(header, 4);
    let headerSize = 8;

    if (size === 1) {
      // 64-bit size, carried in the eight bytes after the type.
      if (header.byteLength < 16) return null;
      size = Number(header.getBigUint64(8));
      headerSize = 16;
    } else if (size === 0) {
      // Runs to the end of the file.
      size = file.size - offset;
    }
    if (size < headerSize) return null;

    if (type === "moov") {
      const end = Math.min(offset + Math.min(size, MAX_MOOV_BYTES), file.size);
      const body = await file.slice(offset + headerSize, end).arrayBuffer();
      return new DataView(body);
    }

    offset += size;
  }
  return null;
}

function parseMoov(moov: DataView): Mp4Metadata {
  const found: Mp4Metadata = {
    recordedAt: null,
    latitude: null,
    longitude: null,
    durationSeconds: null,
    source: { recordedAt: null, location: null },
  };

  walk(moov, 0, moov.byteLength, (type, view, start, end) => {
    if (type === "mvhd") readMvhd(view, start, found);
    // The location atom's type starts with 0xA9, which is the copyright sign
    // in the old Mac roman encoding these atoms still use.
    if (type === "©xyz") readLocation(view, start, end, found);
  });

  return found;
}

function walk(
  view: DataView,
  start: number,
  end: number,
  visit: (type: string, view: DataView, bodyStart: number, bodyEnd: number) => void,
): void {
  let offset = start;
  while (offset + 8 <= end) {
    let size = view.getUint32(offset);
    const type = typeAt(view, offset + 4);
    let headerSize = 8;

    if (size === 1) {
      if (offset + 16 > end) return;
      size = Number(view.getBigUint64(offset + 8));
      headerSize = 16;
    } else if (size === 0) {
      size = end - offset;
    }
    if (size < headerSize) return;

    const bodyStart = offset + headerSize;
    const bodyEnd = Math.min(offset + size, end);

    visit(type, view, bodyStart, bodyEnd);
    if (CONTAINERS.has(type)) {
      // `meta` carries a version and flags before its children do.
      const childStart = type === "meta" ? bodyStart + 4 : bodyStart;
      walk(view, childStart, bodyEnd, visit);
    }

    offset += size;
  }
}

function readMvhd(view: DataView, start: number, into: Mp4Metadata): void {
  const version = view.getUint8(start);
  let cursor = start + 4; // version plus three flag bytes

  let created: number;
  let timescale: number;
  let duration: number;

  if (version === 1) {
    created = Number(view.getBigUint64(cursor));
    cursor += 16; // creation and modification times
    timescale = view.getUint32(cursor);
    duration = Number(view.getBigUint64(cursor + 4));
  } else {
    created = view.getUint32(cursor);
    cursor += 8;
    timescale = view.getUint32(cursor);
    duration = view.getUint32(cursor + 4);
  }

  // Some encoders leave the creation time at zero rather than omitting it.
  if (created > 0) {
    into.recordedAt = new Date((created - EPOCH_1904_TO_1970_SECONDS) * 1000);
    into.source.recordedAt = "file";
  }
  if (timescale > 0 && duration > 0) {
    into.durationSeconds = duration / timescale;
  }
}

/**
 * Pull latitude and longitude out of an ISO 6709 string.
 *
 * Phones write it as `+12.3456-098.7654+010.000/`, sign-prefixed and with no
 * separator, so the signs are the delimiters.
 */
function readLocation(
  view: DataView,
  start: number,
  end: number,
  into: Mp4Metadata,
): void {
  const bytes = new Uint8Array(view.buffer, view.byteOffset + start, end - start);
  const text = new TextDecoder().decode(bytes);
  const match = text.match(/([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)/);
  if (!match) return;

  const latitude = Number(match[1]);
  const longitude = Number(match[2]);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return;

  into.latitude = latitude;
  into.longitude = longitude;
  into.source.location = "file";
}

function typeAt(view: DataView, offset: number): string {
  return String.fromCharCode(
    view.getUint8(offset),
    view.getUint8(offset + 1),
    view.getUint8(offset + 2),
    view.getUint8(offset + 3),
  );
}
