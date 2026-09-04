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
  /** Narrow to one run. Re-analysing a cut appends rather than replaces, so a
   *  page that reads the whole table shows every run that ever happened. */
  since = "",
): Promise<{ findings: Finding[]; error?: string }> {
  const url =
    `${apiBase()}/api/findings?edit_version=${encodeURIComponent(editVersion)}` +
    `&scene_id=${encodeURIComponent(sceneId)}` +
    (since ? `&since=${encodeURIComponent(since)}` : "");

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
  const payload = readEnvelope(result);
  if (!payload) return [];

  return payload.rows.map((row) => {
    const record: Record<string, unknown> = {};
    payload.columns.forEach((column, index) => {
      record[column] = row[index];
    });
    return record as T;
  });
}

type Table = { columns: string[]; rows: unknown[][] };

/**
 * Find the table inside whatever MCP handed back.
 *
 * The tool returns its answer as a JSON document inside the `text` of a
 * content part, so the structured path is one `JSON.parse` of that string and
 * every escape in it survives. Digging the JSON out of the stringified whole
 * instead costs a round of re-escaping, and `°` reaches the page as six
 * literal characters where a degree sign was meant: the agent measures angles,
 * so that is most of what it writes about.
 */
function readEnvelope(result: unknown): Table | null {
  const direct = asTable(result);
  if (direct) return direct;

  const content = (result as { content?: Array<{ text?: string }> })?.content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (typeof part?.text !== "string") continue;
      try {
        const table = asTable(JSON.parse(part.text));
        if (table) return table;
      } catch {
        // Not this part. A tool is allowed to return prose alongside a table.
      }
    }
  }

  // Older shapes, and anything that wrapped the document in prose: find the
  // object by eye. Kept as the last resort rather than the first.
  const raw = typeof result === "string" ? result : JSON.stringify(result ?? "");
  const text = raw.replace(/\\"/g, '"');
  const start = text.indexOf('{"columns"');
  const end = text.indexOf("]]}", start);
  if (start === -1 || end === -1) return null;
  try {
    return asTable(JSON.parse(text.slice(start, end + 3)));
  } catch {
    return null;
  }
}

function asTable(value: unknown): Table | null {
  const candidate = value as Table | null;
  return candidate && Array.isArray(candidate.columns) && Array.isArray(candidate.rows)
    ? candidate
    : null;
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

/**
 * Turn a visitor project's take id into the only name for it a person has.
 *
 * `sc_9f2c1a3b4d_t03` is how a take is addressed in the database, and it means
 * nothing to the editor reading the report: for their own clips the position in
 * the cut is the whole identity. The demo scene's ids are left alone, because
 * `sc14_su03_t02` really does carry a setup and a take number somebody would
 * want to read.
 */
export function humanTakes(text: string): string {
  return text.replace(/sc_[0-9a-f]{6,}_t0*(\d+)/g, "take $1");
}

/** The same, for a single id used as a label. */
export function takeLabel(id: string): string {
  const match = /^sc_[0-9a-f]{6,}_t0*(\d+)$/.exec(id);
  return match ? `Take ${match[1]}` : id;
}
