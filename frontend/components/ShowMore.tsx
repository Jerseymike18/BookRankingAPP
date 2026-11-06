"use client";

/**
 * SHOW MORE — long lists open short and expand on request, the way an app
 * does, instead of dumping hundreds of rows on screen.
 *
 *   const { visible, ...limit } = useRowLimit(rows);
 *   …render `visible`…
 *   <ShowMoreBar {...limit} />
 *
 * Pure presentation: the caller still sorts/filters the full list; this only
 * decides how many of the (already ordered) rows to draw.
 */

import { useState } from "react";

export const INITIAL_ROWS = 10;
const STEP = 25;

export function useRowLimit<T>(items: T[], initial = INITIAL_ROWS) {
  const [limit, setLimit] = useState(initial);
  const total = items.length;
  return {
    visible: items.slice(0, limit),
    total,
    shown: Math.min(limit, total),
    initial,
    showMore: () => setLimit((l) => l + STEP),
    showAll: () => setLimit(Number.POSITIVE_INFINITY),
    collapse: () => setLimit(initial),
  };
}

export function ShowMoreBar({
  total,
  shown,
  initial,
  showMore,
  showAll,
  collapse,
  noun = "books",
}: {
  total: number;
  shown: number;
  initial: number;
  showMore: () => void;
  showAll: () => void;
  collapse: () => void;
  /** Plural noun for the "Show all N …" label. */
  noun?: string;
}) {
  const remaining = total - shown;
  if (remaining <= 0 && shown <= initial) return null;
  const btn = "px-3 py-1.5 rounded-lg text-sm font-medium";
  return (
    <div className="flex items-center justify-center gap-2 mt-3 flex-wrap">
      {remaining > 0 ? (
        <>
          {remaining > STEP && (
            <button
              type="button"
              onClick={showMore}
              className={btn}
              style={{ background: "var(--color-surface-2)", color: "var(--color-sage)" }}
            >
              Show {STEP} more
            </button>
          )}
          <button
            type="button"
            onClick={showAll}
            className={btn}
            style={{ background: "var(--color-surface-2)", color: "var(--color-sage)" }}
          >
            Show all {total} {noun}
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={collapse}
          className={btn}
          style={{ background: "var(--color-surface-2)", color: "var(--color-muted)" }}
        >
          Show fewer
        </button>
      )}
    </div>
  );
}
