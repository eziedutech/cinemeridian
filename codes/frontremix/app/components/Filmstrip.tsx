import { useEffect, useRef } from "react";

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
  takes: Take[];
  bucket: string;
  framesPerTake: number;
  apiBase: string;
  goToIndex: number | null;
  onOpen: (take: Take) => void;
};

/**
 * The take library, laid out as a strip of film.
 *
 * The sprocket rails are not decoration for its own sake: they say at a glance
 * that this is footage in shooting order, not a list of records. Each cell
 * shows the take's head frame, because that is the frame that will sit against
 * the previous shot's tail if this take makes the cut.
 */
export function Filmstrip({
  takes,
  bucket,
  framesPerTake,
  apiBase,
  goToIndex,
  onOpen,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);

  // A horizontal strip that only scrolls with a shift-wheel is a strip most
  // people will think is broken, so a plain wheel moves it sideways.
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      event.preventDefault();
      element.scrollLeft += event.deltaY;
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    if (goToIndex == null) return;
    const element = scroller.current;
    const cell = element?.querySelector<HTMLElement>(`[data-index="${goToIndex}"]`);
    cell?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    cell?.focus({ preventScroll: true });
  }, [goToIndex]);

  return (
    <div className="filmstrip">
      <div className="sprockets" aria-hidden="true" />
      <div className="strip" ref={scroller}>
        {takes.map((take, index) => {
          const head = framePaths(bucket, take.take_id, framesPerTake)[0];
          const seconds = takeSeconds(take);
          return (
            <button
              key={take.take_id}
              type="button"
              className="cell"
              data-index={index}
              data-in-cut={take.cut_position != null || undefined}
              onClick={() => onOpen(take)}
              title={`${SETUP_NAMES[take.setup_id] ?? take.setup_id}, take ${take.take_number}`}
            >
              <span className="cell-frame">
                {head ? (
                  <img
                    src={frameUrl(apiBase, head)}
                    alt={`Head frame of ${take.take_id}`}
                    loading="lazy"
                  />
                ) : null}
                <span className="cell-duration">{formatDuration(seconds)}</span>
                {take.cut_position != null ? (
                  <span className="cell-cut">cut {take.cut_position}</span>
                ) : null}
              </span>
              <span className="cell-label">
                <b>
                  {take.setup_id}/{String(take.take_number).padStart(2, "0")}
                </b>
                <span>
                  day {take.shoot_day} · {localTime(take.started_at)}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      <div className="sprockets" aria-hidden="true" />
    </div>
  );
}
