/**
 * The agent's report, as the page's answer rather than a line in a log.
 *
 * It used to arrive as one more row in the timeline, filed between "counting
 * the rows in the review queue" and "reading the review queue", which put the
 * conclusion at the same weight as the bookkeeping that produced it. It is the
 * thing somebody came for.
 *
 * The markdown is rendered here rather than by a library, and only the parts
 * the agent actually writes: headings, bold, inline code, bullets, and tables.
 * A dependency would bring a parser for a whole language to read six
 * constructs, and would have to be trusted with text a model wrote.
 */
export function Report({ markdown }: { markdown: string }) {
  return <div className="report">{render(markdown)}</div>;
}

function render(markdown: string): JSX.Element[] {
  const out: JSX.Element[] = [];
  const lines = markdown.replace(/\r/g, "").split("\n");

  let bullets: string[] = [];
  let table: string[] = [];
  let paragraph: string[] = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    out.push(
      <ul key={`u${out.length}`}>
        {bullets.map((item, index) => (
          <li key={index}>{inline(item)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(<p key={`p${out.length}`}>{inline(paragraph.join(" "))}</p>);
    paragraph = [];
  };

  const flushTable = () => {
    if (!table.length) return;
    // The second row of a markdown table is the alignment rule, which carries
    // nothing worth showing.
    const rows = table
      .filter((row) => !/^\|[\s:|-]+\|$/.test(row.trim()))
      .map((row) =>
        row
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map((cell) => cell.trim()),
      );
    const [head, ...body] = rows;
    out.push(
      <div className="report-table" key={`t${out.length}`}>
        <table>
          {head ? (
            <thead>
              <tr>
                {head.map((cell, index) => (
                  <th key={index}>{inline(cell)}</th>
                ))}
              </tr>
            </thead>
          ) : null}
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, index) => (
                  <td key={index}>{inline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
    table = [];
  };

  const flushAll = () => {
    flushBullets();
    flushTable();
    flushParagraph();
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushBullets();
      flushParagraph();
      table.push(line);
      continue;
    }
    flushTable();

    if (!line.trim()) {
      flushAll();
      continue;
    }
    if (/^-{3,}$/.test(line.trim())) {
      flushAll();
      out.push(<hr key={`h${out.length}`} />);
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushAll();
      const level = Math.min(heading[1].length, 4);
      const Tag = (["h3", "h3", "h4", "h5"][level - 1] ?? "h5") as "h3" | "h4" | "h5";
      out.push(<Tag key={`k${out.length}`}>{inline(heading[2])}</Tag>);
      continue;
    }

    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line) ?? /^\s*\d+\.\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      bullets.push(bullet[1]);
      continue;
    }

    flushBullets();
    paragraph.push(line.trim());
  }

  flushAll();
  return out;
}

/**
 * The few LaTeX tokens a model reaches for when it wants a symbol.
 *
 * It is asked not to and mostly does not, but a report already written and
 * frozen cannot be asked again, and a raw `$\Delta$` in the middle of a
 * sentence is the kind of detail that makes careful work look unfinished.
 */
const MATHS: Record<string, string> = {
  Delta: "Δ",
  delta: "δ",
  rightarrow: "→",
  to: "→",
  times: "×",
  approx: "≈",
  pm: "±",
  circ: "°",
  degree: "°",
  le: "≤",
  ge: "≥",
};

function plainMaths(text: string): string {
  return text.replace(/\$([^$\n]{1,60})\$/g, (_whole, body: string) =>
    body
      .trim()
      .replace(/\\([a-zA-Z]+)/g, (token: string, name: string) => MATHS[name] ?? name),
  );
}

/** Bold, inline code, and the mathematical arrow the agent likes for a cut. */
function inline(source: string): (string | JSX.Element)[] {
  const text = plainMaths(source);
  const parts: (string | JSX.Element)[] = [];
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\$\\rightarrow\$|\\u2192/g;

  let last = 0;
  let match = pattern.exec(text);
  let key = 0;

  while (match) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    if (match[1] !== undefined) parts.push(<strong key={key++}>{match[1]}</strong>);
    else if (match[2] !== undefined) parts.push(<code key={key++}>{match[2]}</code>);
    else parts.push("→");
    last = match.index + match[0].length;
    match = pattern.exec(text);
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
