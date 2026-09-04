import { useEffect } from "react";

import { Report } from "~/components/Report";

/**
 * The agent's full report, over the page.
 *
 * One sheet for every page that has a report to show: the demo scene, the
 * worked example, and somebody's own clips. They were drifting apart, which
 * meant a judge who opened two of them saw two products.
 */
export function ReportSheet({
  markdown,
  onClose,
  onExport,
}: {
  markdown: string;
  onClose: () => void;
  /** Offered here rather than beside the answer: somebody who wants to keep a
   *  review is somebody who has just read it. */
  onExport?: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-over" role="dialog" aria-modal="true" onClick={onClose}>
      <div
        className="sheet report-sheet"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sheet-head">
          <h2>The full report</h2>
          <div className="form-row">
            {onExport ? (
              <button type="button" className="ghost small" onClick={onExport}>
                Export PDF
              </button>
            ) : null}
            <button type="button" className="ghost small" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="sheet-body">
          <Report markdown={markdown} />
        </div>
      </div>
    </div>
  );
}
