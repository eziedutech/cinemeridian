import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { frameUrl } from "~/lib/api";
import {
  SETUP_NAMES,
  formatDuration,
  framePaths,
  localTime,
  takeSeconds,
  type Take,
} from "~/lib/takes";

type Props = {
  take: Take | null;
  bucket: string;
  framesPerTake: number;
  apiBase: string;
  onClose: () => void;
};

const SPEEDS = [0.25, 0.5, 1, 2];

/**
 * A player for a take, built out of frames rather than video.
 *
 * There is no encoded footage here and there does not need to be. The take is
 * a sequence of frames sampled from its first moment to its last, and stepping
 * through them is exactly what a continuity check wants: slow motion down to a
 * standstill with no compression artefacts, and a single-frame step so the
 * head and the tail can be looked at squarely.
 *
 * Every frame is preloaded before playback starts. A player that stutters
 * while it fetches is a player people stop trusting, and eight frames is
 * nothing to hold.
 */
export function FramePlayer({ take, bucket, framesPerTake, apiBase, onClose }: Props) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [ready, setReady] = useState(false);
  const timer = useRef<number | null>(null);

  const urls = useMemo(() => {
    if (!take) return [];
    return framePaths(bucket, take.take_id, framesPerTake).map((uri) =>
      frameUrl(apiBase, uri),
    );
  }, [take, bucket, framesPerTake, apiBase]);

  useEffect(() => {
    setIndex(0);
    setPlaying(false);
    setReady(false);
    if (urls.length === 0) return;

    let cancelled = false;
    let loaded = 0;
    for (const url of urls) {
      const image = new Image();
      image.onload = image.onerror = () => {
        loaded += 1;
        if (!cancelled && loaded === urls.length) setReady(true);
      };
      image.src = url;
    }
    return () => {
      cancelled = true;
    };
  }, [urls]);

  // The take runs for its full duration, so the frames are spread across it:
  // eight frames over ninety-five seconds is one every thirteen seconds of
  // captured time, played back here at roughly two per second at 1x.
  useEffect(() => {
    if (!playing || urls.length === 0) return;
    const interval = 500 / speed;
    timer.current = window.setInterval(() => {
      setIndex((current) => {
        if (current >= urls.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, interval);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, speed, urls.length]);

  const step = useCallback(
    (delta: number) => {
      setPlaying(false);
      setIndex((current) => Math.min(Math.max(current + delta, 0), urls.length - 1));
    },
    [urls.length],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowRight") step(1);
      if (event.key === "ArrowLeft") step(-1);
      if (event.key === " ") {
        event.preventDefault();
        setPlaying((current) => !current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, step]);

  if (!take) return null;

  const seconds = takeSeconds(take);
  const atSecond = urls.length > 1 ? (seconds * index) / (urls.length - 1) : 0;
  const role = index === 0 ? "head" : index === urls.length - 1 ? "tail" : "";

  return (
    <div className="scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="player" onClick={(event) => event.stopPropagation()}>
        <header className="player-head">
          <div>
            <h2 className="sheet-title">
              {SETUP_NAMES[take.setup_id] ?? take.setup_id}
              <span className="sheet-take">take {take.take_number}</span>
            </h2>
            <p className="sheet-sub">
              {take.take_id} · {formatDuration(seconds)} · sampled at{" "}
              {urls.length} frames
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="player-stage">
          {urls[index] ? (
            <img src={urls[index]} alt={`Frame ${index + 1} of ${take.take_id}`} />
          ) : null}
          {role ? <span className={`role-badge role-${role}`}>{role}</span> : null}
          {ready ? null : <span className="player-loading">loading frames…</span>}
        </div>

        <div className="player-scrub">
          <input
            type="range"
            min={0}
            max={Math.max(urls.length - 1, 0)}
            value={index}
            onChange={(event) => {
              setPlaying(false);
              setIndex(Number(event.target.value));
            }}
            aria-label="Frame"
          />
          <span className="player-time">
            {atSecond.toFixed(0)}s / {seconds}s
          </span>
        </div>

        <div className="player-controls">
          <button type="button" className="ghost" onClick={() => step(-1)}>
            ‹ frame
          </button>
          <button type="button" onClick={() => setPlaying((current) => !current)}>
            {playing ? "Pause" : "Play"}
          </button>
          <button type="button" className="ghost" onClick={() => step(1)}>
            frame ›
          </button>

          <span className="speeds">
            {SPEEDS.map((value) => (
              <button
                key={value}
                type="button"
                className="ghost"
                aria-pressed={value === speed}
                onClick={() => setSpeed(value)}
              >
                {value}×
              </button>
            ))}
          </span>
        </div>

        <p className="player-note">
          Frames, not video. The take is sampled from its first moment to its
          last, so the head and the tail here are the exact frames a cut would
          join to the shots either side. Space plays, arrow keys step.
        </p>
      </div>
    </div>
  );
}
