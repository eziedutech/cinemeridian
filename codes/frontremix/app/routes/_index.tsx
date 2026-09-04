import { useState } from "react";
import { json, type LinksFunction, type MetaFunction } from "@remix-run/node";
import { Link, useLoaderData } from "@remix-run/react";

import { TryYourself } from "~/components/TryYourself";
import { apiBase } from "~/lib/api";
import styles from "~/styles/home.css?url";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: styles }];

export const meta: MetaFunction = () => [
  { title: "CineMeridian - continuity for the shoot and the cut" },
  {
    name: "description",
    content:
      "An agent that checks whether two shots can be cut together, by measuring the light in the frames and asking a database what the sun was doing.",
  },
];

export async function loader() {
  return json({ apiBase: apiBase() });
}

/**
 * Three claims, side by side.
 *
 * The page has three separate things to say: what this is, what goes wrong
 * without it, and how it works. Showing them one at a time made each one wait
 * its turn, and a reader who arrives mid-rotation gets the middle of an
 * argument. Across the full width they can be taken in at a glance and read in
 * any order, which is how a poster's credit blocks work.
 *
 * They are set at slightly different heights on purpose. Three boxes ruled
 * level read as a table of features; staggered, they read as type placed on a
 * photograph, and the eye moves between them instead of scanning across.
 */
const CLAIMS = [
  {
    question: "What this is",
    headline: "A continuity check that runs on physics, not opinion",
    body: "CineMeridian reviews the joins in a cut the way a script supervisor would, take by take, and files what it finds as recommendations for a person to accept or dismiss. It never touches the edit.",
  },
  {
    question: "What goes wrong without it",
    headline: "The shadow moved. Nobody wrote it down.",
    body: "Coverage of one scene is shot hours or weeks apart, and the sun does not wait. Shadow direction, length and colour drift between takes that are meant to be one moment. The editor stops seeing it by the fortieth viewing. The audience feels it once.",
  },
  {
    question: "How it works",
    headline: "Vision turns pixels into facts. The database does the rest.",
    body: "Gemini measures the two frames a cut actually joins, real solar geometry says where the sun was at that time and place, and ClickHouse compares every pair at once through an MCP server the agent queries itself.",
  },
];

export default function Home() {
  const { apiBase } = useLoaderData<typeof loader>();
  const [samplesOpen, setSamplesOpen] = useState(false);

  return (
    <main className="poster">
      <div className="poster-bg" />
      <div className="poster-scrim" />

      <header className="poster-head">
        <div>
          <p className="poster-mark">
            <img src="/logocine.png" alt="CineMeridian" width={240} height={90} />
          </p>
          <p className="poster-sub">Continuity intelligence for the shoot and the cut</p>
        </div>
        <span className="poster-badge">
          Agentic Cinema <b>ClickHouse track</b>
        </span>
      </header>

      <section className="poster-body">
        <h1 className="sr-only">
          CineMeridian, a continuity check that runs on physics rather than
          opinion
        </h1>
        <div className="claims">
          {CLAIMS.map((entry, index) => (
            <article
              className="claim"
              key={entry.question}
              style={{ "--claim-order": index } as React.CSSProperties}
            >
              <p className="claim-question">
                <i>{String(index + 1).padStart(2, "0")}</i>
                {entry.question}
              </p>
              <p className="claim-headline">{entry.headline}</p>
              <p className="claim-body">{entry.body}</p>
            </article>
          ))}
        </div>
      </section>

      <footer className="poster-foot">
        <div className="doors">
          <Link to="/example" className="door door-lead">
            <b>
              <PlayIcon />
              Show a worked example
              <GoIcon />
            </b>
            <span>
              Six clips already run through the tool: five joins, one scene
              change, three findings. Opens instantly.
            </span>
          </Link>

          <button
            type="button"
            className="door"
            onClick={() => setSamplesOpen(true)}
          >
            <b>
              <ReelIcon />
              Analyse an example video
              <GoIcon />
            </b>
            <span>
              Six sample clips. Pick two and watch the agent work on them for
              real, end to end.
            </span>
          </button>

          <Link to="/try" className="door">
            <b>
              <UploadIcon />
              Analyse your own video
              <GoIcon />
            </b>
            <span>
              Two to six clips of your own. Your browser decodes them; the files
              never leave it.
            </span>
          </Link>
        </div>

        <p className="poster-facts">
          <span>
            <b>Agentic AI</b> reads the frames
          </span>
          <i className="dot">·</i>
          <span>
            <b>A real-time analytics database</b> queried at runtime through MCP
          </span>
          <i className="dot">·</i>
          <span>
            <b>4 of 5</b> planted faults found in the demo scene
          </span>
          <i className="dot">·</i>
          <span>No login, and nothing you upload is kept beyond a day</span>
        </p>
      </footer>

      {samplesOpen ? (
        <TryYourself apiBase={apiBase} onClose={() => setSamplesOpen(false)} />
      ) : null}
    </main>
  );
}

/* Drawn here rather than pulled from an icon font: three glyphs is not worth a
   dependency, and a font is the first thing to fail on a slow connection. */

function GoIcon() {
  return (
    <svg
      className="door-go"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M3.4 8h9m0 0L9.2 4.8M12.4 8 9.2 11.2"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6.6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M6.6 5.4 11 8l-4.4 2.6V5.4Z" fill="currentColor" />
    </svg>
  );
}

function ReelIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect
        x="1.6"
        y="3.4"
        width="12.8"
        height="9.2"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path d="M5.2 3.4v9.2M10.8 3.4v9.2" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
}

function UploadIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 11.2V3.6m0 0L5.2 6.4M8 3.6l2.8 2.8"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M2.6 10.4v1.8a1.6 1.6 0 0 0 1.6 1.6h7.6a1.6 1.6 0 0 0 1.6-1.6v-1.8"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}
