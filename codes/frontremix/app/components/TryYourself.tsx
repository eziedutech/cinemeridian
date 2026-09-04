import { useEffect, useState } from "react";
import { useNavigate } from "@remix-run/react";

type Clip = { file: string; title: string; note: string; uri: string };

/**
 * The six sample clips, and the order they would be cut in.
 *
 * Most people who reach this project are not carrying two shots of a beach,
 * and a tool that can only be tried by people who happen to have footage is a
 * tool almost nobody tries. So the samples are the way in: six clips built to
 * answer one question each, and any two of them produce something worth
 * reading.
 *
 * Picking is ordered rather than a set. The same two clips the other way round
 * are a different edit, and the badge on each card says where it sits, because
 * that is the fact the review turns on.
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
  // The list comes from the API, which on a cold service takes a moment. Six
  // cards of the right shape say what is about to be here; a blank panel says
  // something is broken.
  const loading = clips.length === 0 && problem === null;

  return (
    <div className="sheet-over" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="sheet picker" onClick={(event) => event.stopPropagation()}>
        <div className="picker-head">
          <div>
            <h2>Pick two clips, in the order they would be cut</h2>
            <p className="hint">
              Six shots, each built to answer one question. The first two are
              the control: they should come back with nothing at all, which is
              the hardest thing for a checker to get right.
            </p>
          </div>
          <button type="button" className="ghost small" onClick={onClose}>
            Close
          </button>
        </div>

        {problem ? <p className="banner">{problem}</p> : null}

        <ul className="clip-list" aria-busy={loading}>
          {loading
            ? Array.from({ length: 6 }, (_, index) => (
                <li key={index}>
                  <div className="clip clip-waiting" aria-hidden="true">
                    <span className="clip-order" />
                    <span className="clip-text">
                      <span className="waiting-line waiting-title" />
                      <span className="waiting-line" />
                      <span className="waiting-line waiting-short" />
                    </span>
                  </div>
                </li>
              ))
            : null}
          {loading ? <li className="sr-only">Loading the sample clips</li> : null}
          {clips.map((clip, index) => {
            const picked = chosen.indexOf(clip.file);
            return (
              <li key={clip.file}>
                <button
                  type="button"
                  className={picked >= 0 ? "clip picked" : "clip"}
                  aria-pressed={picked >= 0}
                  onClick={() => toggle(clip.file)}
                >
                  <span className="clip-order">
                    {picked >= 0 ? picked + 1 : index + 1}
                  </span>
                  <span className="clip-text">
                    <b>{clip.title}</b>
                    <em>{clip.note}</em>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        {/* The count sits with the button rather than above the list, so the
            answer to "am I allowed to press this yet" is where the pressing
            happens. */}
        <div className="picker-foot">
          <p className="picker-count">
            {chosen.length === 0
              ? "Nothing picked yet"
              : `${chosen.length} picked, cut in that order`}
            {chosen.length > 0 ? (
              <button type="button" className="linkish" onClick={() => setChosen([])}>
                Clear
              </button>
            ) : null}
          </p>
          <button
            type="button"
            disabled={!enough}
            onClick={() =>
              navigate(`/try?clips=${encodeURIComponent(chosen.join(","))}`)
            }
          >
            {enough ? `Analyse these ${chosen.length}` : "Pick at least two"}
          </button>
        </div>
      </div>
    </div>
  );
}
