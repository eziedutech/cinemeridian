import type { ReactNode } from "react";

/**
 * The explanation, folded away until somebody wants it.
 *
 * This project has a great deal to explain: why a shadow length is trusted and
 * a confidence score is not, why a position is asked for rather than worked
 * out, why six takes is the ceiling. All of it is worth saying and none of it
 * is worth saying at once. Left on the page as paragraphs it buries the two or
 * three sentences a person actually has to read to know what to do next.
 *
 * So headings carry the reason and the page carries the instruction. Focusable
 * as well as hoverable, because a keyboard should reach anything a mouse can.
 */
export function Info({ children }: { children: ReactNode }) {
  return (
    <span className="info" tabIndex={0} role="note">
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9.2" />
        <path d="M12 11v5.4" strokeLinecap="round" />
        <path d="M12 7.6h.01" strokeLinecap="round" />
      </svg>
      <span className="tip">{children}</span>
    </span>
  );
}
