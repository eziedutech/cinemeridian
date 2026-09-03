/**
 * Carry a finished review from the console to the printable page.
 *
 * The findings themselves are rows in ClickHouse and the report page fetches
 * them like any other reader. What is not in the database is the part written
 * for a person: the three sentences the agent opened with, and the note of what
 * the run was given to work with. Those exist only in the tab that ran it.
 *
 * So they travel through the browser rather than through the server. Sending
 * them up only to fetch them back would mean storing somebody's review under
 * their name to print it, which is a promise this project has not made and does
 * not need to make. The report opens in a second tab of the same origin, and
 * this is the shortest honest way to hand it what it needs.
 */

export type ReportFact = { label: string; value: string };

export type ReportHandoff = {
  production: string;
  scene: string;
  edit: string;
  /** The agent's own report, in full. The page prints the short version. */
  report: string;
  facts: ReportFact[];
  seconds: number;
  takeCount: number;
  /** Where the visitor said it was filmed, if they said. */
  place: string | null;
  savedAt: string;
};

const PREFIX = "cinemeridian.report.";
/** Long enough to walk to a printer, short enough not to leave reviews lying
 *  in somebody's browser. The frames the agent kept expire in a day too. */
const KEEP_MS = 24 * 60 * 60 * 1000;

export function keepForReport(handoff: Omit<ReportHandoff, "savedAt">): void {
  if (typeof window === "undefined") return;
  try {
    prune();
    window.localStorage.setItem(
      PREFIX + handoff.scene,
      JSON.stringify({ ...handoff, savedAt: new Date().toISOString() }),
    );
  } catch {
    // A browser refusing storage costs the header on the printed page and
    // nothing else, so it is not worth interrupting anybody over.
  }
}

export function takeForReport(scene: string): ReportHandoff | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PREFIX + scene);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ReportHandoff;
    return parsed && typeof parsed.report === "string" ? parsed : null;
  } catch {
    return null;
  }
}

/** Drop anything older than a day, so a browser does not accumulate reviews. */
function prune(): void {
  const now = Date.now();
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index);
    if (!key || !key.startsWith(PREFIX)) continue;
    try {
      const saved = JSON.parse(window.localStorage.getItem(key) ?? "{}") as ReportHandoff;
      if (!saved.savedAt || now - Date.parse(saved.savedAt) > KEEP_MS) {
        window.localStorage.removeItem(key);
      }
    } catch {
      window.localStorage.removeItem(key);
    }
  }
}
