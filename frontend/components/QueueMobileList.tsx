"use client";

import { seriesLabel } from "@/lib/format";

/**
 * QUEUE MOBILE LIST — the phone rendering of the Read Queue results (fiction and
 * nonfiction share it). One tappable row per book: rank, predicted-WA badge,
 * title + author, and the active sort value when it isn't WA. Tapping a row
 * opens the same expanded panel the desktop table uses (`renderPanel`).
 *
 * Presentation only: sorting state and the sort itself stay in the caller.
 */

type QueueRec = {
  title: string;
  author: string;
  series?: string | null;
  series_number?: number | string | null;
  genre: string;
  wa: number | null;
};

export function QueueMobileList<F extends string, R extends QueueRec, I extends { rec: R }>({
  items,
  sortOptions,
  sortField,
  sortDir,
  onSort,
  sortValue,
  expandedTitle,
  onToggle,
  renderPanel,
}: {
  items: I[];
  sortOptions: { id: F; label: string }[];
  sortField: F;
  sortDir: "asc" | "desc";
  onSort: (f: F) => void;
  /** The number the active sort orders by, shown at the row's end (null → "—"). */
  sortValue: (item: I, f: F) => number | null;
  expandedTitle: string | null;
  onToggle: (title: string) => void;
  renderPanel: (item: I) => React.ReactNode;
}) {
  const sortLabel = sortOptions.find((o) => o.id === sortField)?.label ?? "";
  const showSortVal = sortField !== ("wa" as F);
  return (
    <div className="md:hidden">
      <div className="flex items-center gap-2 text-sm mb-3">
        <span style={{ color: "var(--color-muted)" }}>Sort by</span>
        <select
          value={sortField}
          onChange={(e) => onSort(e.target.value as F)}
          className="px-3 py-1.5 rounded-lg text-sm font-medium"
          style={{ background: "var(--color-surface-2)", border: "1px solid transparent", color: "var(--color-ink)" }}
        >
          {sortOptions.map((o) => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => onSort(sortField)}
          aria-label={sortDir === "desc" ? "Sorted high to low — tap to flip" : "Sorted low to high — tap to flip"}
          className="px-2.5 py-1.5 rounded-lg text-sm font-medium"
          style={{ background: "var(--color-surface-2)", color: "var(--color-sage)" }}
        >
          {sortDir === "desc" ? "↓" : "↑"}
        </button>
      </div>

      <ul className="rounded-xl overflow-hidden" style={{ background: "var(--color-surface)", boxShadow: "var(--shadow-card)" }}>
        {items.map((item, i) => {
          const { rec } = item;
          const open = expandedTitle === rec.title;
          const v = showSortVal ? sortValue(item, sortField) : null;
          return (
            <li key={rec.title} style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}>
              <button
                type="button"
                onClick={() => onToggle(rec.title)}
                aria-expanded={open}
                className="app-row w-full flex items-center gap-3 px-3 py-3 text-left"
              >
                <span className="w-6 shrink-0 text-right font-display italic text-sm" style={{ color: "var(--color-faint)" }}>
                  {i + 1}
                </span>
                <span className="wa-badge">{rec.wa != null ? rec.wa.toFixed(1) : "—"}</span>
                <span className="min-w-0 flex-1">
                  <span className="block font-display font-semibold text-sm leading-tight truncate" style={{ color: "var(--color-ink)" }}>
                    {rec.title}
                  </span>
                  <span className="block text-xs mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
                    {rec.author} · {rec.series ? seriesLabel(rec.series, rec.series_number as never) : rec.genre}
                  </span>
                </span>
                {showSortVal && (
                  <span className="shrink-0 text-right">
                    <span className="block text-sm font-semibold tabular-nums" style={{ color: v == null || v === 0 ? "var(--color-faint)" : "var(--color-sage)" }}>
                      {v == null || v === 0 ? "—" : v.toFixed(2)}
                    </span>
                    <span className="block text-[11px]" style={{ color: "var(--color-faint)" }}>{sortLabel}</span>
                  </span>
                )}
                <svg
                  width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0"
                  style={{ color: "var(--color-faint)", transform: open ? "rotate(90deg)" : "none", transition: "transform 150ms" }}
                >
                  <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              {open && <div style={{ borderTop: "1px solid var(--color-rule)" }}>{renderPanel(item)}</div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
