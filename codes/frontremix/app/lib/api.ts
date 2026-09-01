/**
 * Where the backend lives, and the shapes it returns.
 *
 * The API base is read from the environment at request time rather than baked
 * into the bundle, because the console and the API deploy as two separate
 * Cloud Run services and neither knows the other's URL until it exists.
 */

export function apiBase(): string {
  const base = process.env.CINEMERIDIAN_API_URL ?? "http://localhost:8080";
  return base.replace(/\/$/, "");
}

export type Finding = {
  finding_id: string;
  created_at: string;
  finding_type: string;
  severity: "info" | "low" | "medium" | "high" | string;
  take_a: string;
  take_b: string;
  entity: string;
  attribute: string;
  observed_delta: string;
  computed_expectation: string;
  gemini_verdict: string;
  recommendation: string;
  visible_in_cut: number;
  human_reviewed: number;
};

/**
 * Pull findings for one edit version.
 *
 * The API hands back whatever the MCP tool returned, which is a JSON envelope
 * wrapping JSONEachRow text. Unwrapping it here keeps that shape out of the
 * components, and keeps a malformed response from blanking the page.
 */
export async function fetchFindings(
  editVersion: string,
  sceneId: string,
): Promise<{ findings: Finding[]; error?: string }> {
  const url = `${apiBase()}/api/findings?edit_version=${encodeURIComponent(
    editVersion,
  )}&scene_id=${encodeURIComponent(sceneId)}`;

  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
    if (!response.ok) {
      return { findings: [], error: `API returned ${response.status}` };
    }
    const payload = (await response.json()) as { result?: unknown };
    return { findings: parseFindings(payload.result) };
  } catch (error) {
    return {
      findings: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

/**
 * Dig the rows out of the MCP tool's response envelope.
 *
 * mcp-clickhouse answers with `{"columns": [...], "rows": [[...]]}` inside an
 * MCP content envelope, which the Python side then stringifies - so the
 * payload arrives as escaped text within a repr rather than as clean JSON.
 * Finding the columns/rows object and rebuilding records from it is the one
 * approach that survives both shapes.
 */
export function parseRows<T>(result: unknown): T[] {
  const raw = typeof result === "string" ? result : JSON.stringify(result ?? "");
  const text = raw.replace(/\\"/g, '"');

  const start = text.indexOf('{"columns"');
  if (start === -1) return [];
  const end = text.indexOf("]]}", start);
  if (end === -1) return [];

  let payload: { columns: string[]; rows: unknown[][] };
  try {
    payload = JSON.parse(text.slice(start, end + 3));
  } catch {
    return [];
  }

  return payload.rows.map((row) => {
    const record: Record<string, unknown> = {};
    payload.columns.forEach((column, index) => {
      record[column] = row[index];
    });
    return record as T;
  });
}

function parseFindings(result: unknown): Finding[] {
  return parseRows<Finding>(result);
}

export function severityRank(severity: string): number {
  return { high: 0, medium: 1, low: 2, info: 3 }[severity] ?? 4;
}

/** Frames are proxied by the API so the bucket can stay private. */
export function frameUrl(base: string, gsUri: string): string {
  return `${base}/api/frame?uri=${encodeURIComponent(gsUri)}`;
}
