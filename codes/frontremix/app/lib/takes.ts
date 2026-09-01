/**
 * The take library, and the frames sampled from each take.
 *
 * A cut joins the tail of one shot to the head of the next, so those two
 * frames are the ones that end up beside each other on screen. Everything
 * here is arranged around that: a take knows its head and its tail, and the
 * frames between exist for scrubbing rather than for analysis.
 */

import { apiBase, parseRows } from "~/lib/api";

export type Take = {
  take_id: string;
  setup_id: string;
  take_number: number;
  shoot_day: number;
  started_at: string;
  ended_at: string;
  camera_heading_deg: number;
  lens_mm: number;
  source_kind: string;
  slate_verified: number;
  cut_position: number | null;
  sun_azimuth_deg: number | null;
  sun_elevation_deg: number | null;
  shadow_len_ratio: number | null;
  daylight_color_temp_k: number | null;
};

export type TakeLibrary = {
  takes: Take[];
  framesPerTake: number;
  bucket: string;
  error?: string;
};

export async function fetchTakes(
  sceneId: string,
  editVersion: string,
): Promise<TakeLibrary> {
  const url = `${apiBase()}/api/takes?scene_id=${encodeURIComponent(
    sceneId,
  )}&edit_version=${encodeURIComponent(editVersion)}`;

  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(60_000) });
    if (!response.ok) {
      return { takes: [], framesPerTake: 8, bucket: "", error: `API returned ${response.status}` };
    }
    const payload = (await response.json()) as {
      result?: unknown;
      frames_per_take?: number;
      bucket?: string;
    };
    return {
      takes: parseRows<Take>(payload.result).map(normalise),
      framesPerTake: payload.frames_per_take ?? 8,
      bucket: payload.bucket ?? "",
    };
  } catch (error) {
    return {
      takes: [],
      framesPerTake: 8,
      bucket: "",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

/** ClickHouse sends nulls for takes with no ephemeris row or no cut position. */
function normalise(row: Take): Take {
  return {
    ...row,
    cut_position: row.cut_position === 0 ? null : row.cut_position,
  };
}

/** Frame paths for one take, head first and tail last. */
export function framePaths(
  bucket: string,
  takeId: string,
  framesPerTake: number,
): string[] {
  const parts = takeId.split("_");
  if (parts.length !== 3) return [];
  const [scene, setup, take] = parts;
  return Array.from(
    { length: framesPerTake },
    (_, index) =>
      `gs://${bucket}/frames/${scene}/${setup}/${take}/f${String(index).padStart(3, "0")}.jpg`,
  );
}

/** How long the take ran, from its slate times. */
export function takeSeconds(take: Take): number {
  const start = Date.parse(`${take.started_at.replace(" ", "T")}Z`);
  const end = Date.parse(`${take.ended_at.replace(" ", "T")}Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Local wall clock at the beach, which is what a call sheet would say. */
export function localTime(utc: string, offsetHours = -6): string {
  const parsed = Date.parse(`${utc.replace(" ", "T")}Z`);
  if (Number.isNaN(parsed)) return utc;
  const shifted = new Date(parsed + offsetHours * 3600_000);
  return shifted.toISOString().slice(11, 16);
}

export function localDate(utc: string, offsetHours = -6): string {
  const parsed = Date.parse(`${utc.replace(" ", "T")}Z`);
  if (Number.isNaN(parsed)) return utc;
  const shifted = new Date(parsed + offsetHours * 3600_000);
  return shifted.toISOString().slice(0, 10);
}

/**
 * Round for display.
 *
 * ClickHouse stores these as Float32 and rounding in SQL does not survive the
 * widening back to a double, so 240.4 arrives as 240.39999389648438. Formatting
 * belongs at the edge anyway: the number is a bearing to a tenth of a degree,
 * and printing fourteen decimals claims a precision the ephemeris does not have.
 */
export function deg(value: number | null, places = 1): string {
  return value == null ? "unknown" : `${value.toFixed(places)}°`;
}

export function num(value: number | null, places = 2): string {
  return value == null ? "unknown" : value.toFixed(places);
}

export const SETUP_NAMES: Record<string, string> = {
  su01: "Master wide",
  su02: "Reverse on A",
  su03: "Reverse on B",
  su04: "Close-up A",
  su05: "Close-up B",
  su06: "Two-shot",
  su07: "Insert, footprints",
  su08: "Set extension (CG)",
};
