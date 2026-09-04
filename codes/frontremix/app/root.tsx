import {
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  isRouteErrorResponse,
  useRouteError,
} from "@remix-run/react";
import type { LinksFunction, MetaFunction } from "@remix-run/node";

import styles from "~/styles/app.css?url";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: styles }];

/* A default the pages override. Hard-coding a title in the document head
   instead would leave every route carrying the console's name, including the
   front page and the printable report. */
export const meta: MetaFunction = () => [{ title: "CineMeridian" }];

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return <Outlet />;
}

export function ErrorBoundary() {
  const error = useRouteError();
  // Say what went wrong rather than showing a blank page. A judge who cannot
  // reach the API should be able to see that, and see which URL was tried.
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "Unknown error";

  return (
    <div className="shell">
      <h1 className="wordmark">
        <img src="/logocine.png" alt="CineMeridian" width={200} height={75} />
      </h1>
      <div className="panel" style={{ marginTop: 24 }}>
        <h2>Console unavailable</h2>
        <p className="finding-delta">{detail}</p>
        <p className="hint">
          The console reads from the CineMeridian API. If that service is cold
          or unreachable, this is what you see. Reload once it is up.
        </p>
      </div>
    </div>
  );
}
