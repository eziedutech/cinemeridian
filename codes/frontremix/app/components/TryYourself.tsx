import { useEffect, useState } from "react";
import { useNavigate } from "@remix-run/react";

type Clip = { file: string; title: string; note: string; uri: string };

/**
 * The invitation, and the two honest ways to accept it.
 *
 * Most people who reach this page are not carrying two shots of a beach, and a
 * tool that can only be tried by people who happen to have footage is a tool
 * almost nobody tries. So the samples are offered first: six clips built to
 * answer one question each, and any two of them produce something worth
 * reading.
 *
 * Bringing your own is the other door and the more convincing one, which is why
 * it says plainly what leaves the machine: nothing but two frames per clip.
 */
export function TryYourself({
  apiBase,
  onClose,
}: {
  apiBase: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [clips, setClips] = useState<Clip[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`${apiBase}/api/samples`)
      .then((response) => (response.ok ? response.json() : { clips: [] }))
      .then((body) => {
        if (live) setClips(body.clips ?? []);
      })
      .catch(() => setProblem("The sample clips could not be listed."));
    return () => {
      live = false;
    };
  }, [apiBase]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggle = (file: string) =>
    setChosen((current) =>
      current.includes(file)
        ? current.filter((name) => name !== file)
        : current.length >= 6
          ? current
          : [...current, file],
    );

  const enough = chosen.length >= 2;

  return (
    <div className="sheet-over" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <div className="sheet-head">
          <h2>Try it yourself</h2>
          <button type="button" className="ghost small" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="sheet-body">
          <h3 className="door">Use our clips</h3>
          <p className="hint">
            Six shots, each built to answer one question. Pick two or more, in
            the order they would be cut. Any pair produces something: the first
            two should come back with nothing at all, which is the hardest thing
            for a checker to get right.
          </p>

          {problem ? <p className="banner">{problem}</p> : null}

          <ul className="clip-list">
            {clips.map((clip, index) => {
              const picked = chosen.indexOf(clip.file);
              return (
                <li key={clip.file}>
                  <button
                    type="button"
                    className={picked >= 0 ? "clip picked" : "clip"}
                    onClick={() => toggle(clip.file)}
                  >
                    <span className="clip-order">
                      {picked >= 0 ? picked + 1 : index + 1}
                    </span>
                    <span>
                      <b>{clip.title}</b>
                      <em>{clip.note}</em>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="form-row" style={{ marginTop: 4 }}>
            <button
              type="button"
              disabled={!enough}
              onClick={() =>
                navigate(`/try?clips=${encodeURIComponent(chosen.join(","))}`)
              }
            >
              {enough
                ? `Analyse these ${chosen.length}`
                : "Pick at least two"}
            </button>
            {chosen.length > 0 ? (
              <button type="button" className="ghost small" onClick={() => setChosen([])}>
                Clear
              </button>
            ) : null}
          </div>

          <hr className="door-rule" />

          <h3 className="door">Or bring your own</h3>
          <p className="hint">
            Two to six clips of your own, daylight and outdoors or a room with a
            window. Your files are never uploaded: the browser decodes them and
            sends two frames per clip, the first and the last, because those are
            the two moments a cut actually joins.
          </p>
          <button type="button" className="ghost" onClick={() => navigate("/try")}>
            Choose my own clips
          </button>
        </div>
      </div>
    </div>
  );
}
