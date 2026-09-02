/**
 * Say what the agent just did, in a sentence.
 *
 * The timeline used to print the tool name and the SQL. That is the right
 * thing to keep and the wrong thing to lead with: a wall of query text is
 * proof of work for whoever reads SQL, and noise for the editor the tool is
 * built for. So each action gets one plain sentence and a verdict, and the
 * query stays underneath for anyone who wants it.
 *
 * The phrasing is deliberately about intent rather than mechanism. "Reading
 * the take log against the cut list" is what the agent is doing; "SELECT with
 * a LEFT JOIN" is only how.
 */

/** What each table holds, said the way somebody on a set would say it. */
const TABLES: Record<string, string> = {
  takes: "the take log",
  edit_decisions: "the cut list",
  frame_observations: "what the vision pass measured",
  ephemeris: "the computed sun, moon and tide",
  env_telemetry: "the on-set weather log",
  shot_render_config: "the render settings",
  continuity_findings: "the review queue",
};

export function describeCall(name: string, args: Record<string, string>): string {
  switch (name) {
    case "list_tables":
      return "Looking at what tables the database holds";
    case "list_databases":
      return "Looking at what databases exist";
    case "run_query":
      return describeQuery(args.query ?? "");
    case "compute_light_rig":
      return "Working out what the sun required at that moment";
    case "compute_render_error":
      return "Measuring how far the submitted render sits from the physics";
    case "find_match_windows":
      return "Searching for when the sun returns to this geometry";
    case "adjudicate_cut":
      return `Asking Gemini to look at both frames and judge the cut${
        args.entity ? `, on the ${args.entity.replace(/_/g, " ")}` : ""
      }`;
    case "record_finding":
      return `Recording a finding in the review queue${
        args.finding_type ? `: ${args.finding_type.replace(/_/g, " ")}` : ""
      }`;
    default:
      return name.replace(/_/g, " ");
  }
}

/**
 * What a query is for, from its shape rather than its text.
 *
 * Only the parts that change the sentence are read: whether it writes, whether
 * it counts, and which tables it touches. Everything else about the SQL is
 * left alone, because guessing further would produce a confident sentence
 * about a query this does not actually understand.
 */
function describeQuery(sql: string): string {
  const flat = sql.replace(/\s+/g, " ").trim();
  const lower = flat.toLowerCase();

  if (lower.startsWith("insert")) {
    const into = /insert\s+into\s+(?:\w+\.)?(\w+)/i.exec(flat)?.[1];
    return `Writing to ${TABLES[into ?? ""] ?? "the database"}`;
  }

  const tables = namedTables(flat);
  const where = tables.length ? ` in ${joinNicely(tables)}` : "";

  if (/\bcount\s*\(/i.test(lower)) {
    return tables.length ? `Counting the rows in ${joinNicely(tables)}` : "Counting rows";
  }
  if (/\bselect\s+distinct\b/i.test(lower)) {
    const column = /select\s+distinct\s+([\w.]+)/i.exec(flat)?.[1]?.split(".").pop();
    return `Listing the ${column ? column.replace(/_/g, " ") : "distinct values"}${where}`;
  }
  if (/\bmedian\s*\(|\bavg\s*\(|\bgroup\s+by\b/i.test(lower)) {
    // No table list on this one. The clause explaining why already makes the
    // line long, and it is the reason that matters here, not the sources.
    return "Working out a typical value, so takes can be compared with each other";
  }
  if (tables.length > 1) return `Reading ${joinNicely(tables)} together`;
  if (tables.length === 1) return `Reading ${tables[0]}`;
  return "Asking the database a question";
}

/** Table names in the order they appear, each named once. */
function namedTables(sql: string): string[] {
  const found: string[] = [];
  // The database name is usually written out, so the table is the last
  // segment rather than the first: `cinemeridian.takes` is the take log.
  const pattern = /\b(?:from|join)\s+(?:\w+\.)?(\w+)/gi;
  let match = pattern.exec(sql);
  while (match) {
    const friendly = TABLES[match[1]];
    if (friendly && !found.includes(friendly)) found.push(friendly);
    match = pattern.exec(sql);
  }
  return found;
}

function joinNicely(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/**
 * What came back, in a few words.
 *
 * Only the part that adds something. Whether the action succeeded is said by
 * the badge this sits inside, so repeating it here would print "failed" twice
 * in a row, and an empty string is the right answer for an action that simply
 * worked and has no count to report.
 */
export function describeOutcome(
  ok: boolean,
  rows: number | null,
  detail: string,
): string {
  if (!ok) return detail;
  if (rows === 0) return "nothing came back";
  if (rows === 1) return "1 row";
  if (typeof rows === "number") return `${rows} rows`;
  return detail;
}
