"use client";

import { useState, useMemo } from "react";
import { useRowLimit, ShowMoreBar } from "@/components/ShowMore";
import Glyph from "./Glyph";

/* ── Types ─────────────────────────────────────────────────────────────────── */

export type SortDir = "asc" | "desc";

export interface SortState {
  key: string;
  dir: SortDir;
}

export interface ColDef<T> {
  key: string;
  label: string;
  /** numeric: sorted as numbers, zeros/nulls to bottom. string: sorted lexically, blanks to bottom. */
  type: "numeric" | "string";
  getValue: (row: T) => number | string | null | undefined;
  /** Override default first-click direction (numeric→desc, string→asc). */
  defaultDir?: SortDir;
  /** Rendered cell content. Falls back to raw value or "—". */
  formatter?: (val: number | string | null | undefined, row: T) => React.ReactNode;
  /** Header + cell alignment. Defaults: numeric→right, string→left. */
  align?: "left" | "right";
  /** Set false to make the column non-sortable (header not clickable). Default true. */
  sortable?: boolean;
  /** Render the row's 1-based position in the current sort order instead of
      getValue — for a rank/# column that should always read 1..N top-to-bottom
      whatever the active sort/direction. */
  autoRank?: boolean;
}

/* ── Zero/null rule ────────────────────────────────────────────────────────
   Numeric 0, null, and undefined all sort to the bottom regardless of direction.
   This matches display convention (0 → "—") for optional components like WB.
   String blanks/nulls also sort to the bottom.
   ────────────────────────────────────────────────────────────────────────── */

/* ── useSortable hook ──────────────────────────────────────────────────────── */

export function useSortable<T>(
  data: T[],
  cols: ColDef<T>[],
  defaultSort: SortState,
  /** Rows matching this predicate are held at the TOP regardless of the active
      sort (in their original order among themselves). Used to pin a requested
      book to the top of the Discover candidate list. */
  pinFirst?: (row: T) => boolean
): { sorted: T[]; sortState: SortState; handleSort: (key: string) => void } {
  const [sortState, setSortState] = useState<SortState>(defaultSort);

  const sorted = useMemo(() => {
    const col = cols.find((c) => c.key === sortState.key);
    if (!col) return data;
    const mult = sortState.dir === "desc" ? -1 : 1;
    return [...data]
      .map((row, idx) => ({ row, idx }))
      .sort(({ row: a, idx: ai }, { row: b, idx: bi }) => {
        if (pinFirst) {
          const pa = pinFirst(a) ? 1 : 0;
          const pb = pinFirst(b) ? 1 : 0;
          if (pa !== pb) return pb - pa;            // pinned rows first
        }
        const av = col.getValue(a);
        const bv = col.getValue(b);
        if (col.type === "numeric") {
          const an = av === null || av === undefined || av === 0 ? null : Number(av);
          const bn = bv === null || bv === undefined || bv === 0 ? null : Number(bv);
          if (an === null && bn === null) return ai - bi;
          if (an === null) return 1;
          if (bn === null) return -1;
          return mult * (an - bn) || ai - bi;
        } else {
          const as = av == null ? "" : String(av).toLowerCase();
          const bs = bv == null ? "" : String(bv).toLowerCase();
          if (!as && !bs) return ai - bi;
          if (!as) return 1;
          if (!bs) return -1;
          return mult * as.localeCompare(bs) || ai - bi;
        }
      })
      .map(({ row }) => row);
  }, [data, cols, sortState, pinFirst]);

  function handleSort(key: string) {
    const col = cols.find((c) => c.key === key);
    if (!col || col.sortable === false) return;
    setSortState((prev) => {
      if (prev.key === key) return { key, dir: prev.dir === "desc" ? "asc" : "desc" };
      const defaultDir = col.defaultDir ?? (col.type === "numeric" ? "desc" : "asc");
      return { key, dir: defaultDir };
    });
  }

  return { sorted, sortState, handleSort };
}

/* ── SortableTh ────────────────────────────────────────────────────────────── */

export function SortableTh({ col, sortState, onSort, extraStyle, accessory, title }: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  col: ColDef<any>;
  sortState: SortState;
  onSort: (key: string) => void;
  extraStyle?: React.CSSProperties;
  /** Optional control rendered after the label inside the header (e.g. an
      expand toggle). It must stop propagation so it doesn't also trigger a sort. */
  accessory?: React.ReactNode;
  /** Native tooltip — used when the visible label is abbreviated. */
  title?: string;
}) {
  const active = sortState.key === col.key;
  const sortable = col.sortable !== false;
  const align = col.align ?? (col.type === "numeric" ? "right" : "left");

  return (
    <th
      onClick={sortable ? () => onSort(col.key) : undefined}
      title={title}
      className={`px-3 py-2.5 text-${align} text-xs font-medium whitespace-nowrap${
        sortable ? " cursor-pointer select-none" : ""
      }`}
      style={{
        color: active ? "var(--color-sage)" : "var(--color-muted)",
        borderBottom: "1px solid var(--color-rule)",
        ...extraStyle,
      }}
    >
      {col.label}
      {sortable && active ? (sortState.dir === "desc" ? " ↓" : " ↑") : ""}
      {accessory}
    </th>
  );
}

/* ── MobileSortBar ──────────────────────────────────────────────────────────
   Phones have no room for clickable column headers, so the list views sort
   from a native picker + a direction toggle instead. Drives the same
   useSortable state (handleSort: new key → its default direction; same key →
   flip). */

export function MobileSortBar({ cols, sortState, onSort, className = "mb-3" }: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  cols: ColDef<any>[];
  sortState: SortState;
  onSort: (key: string) => void;
  className?: string;
}) {
  const options = cols.filter((c) => c.sortable !== false && !c.autoRank);
  if (options.length < 2) return null;
  return (
    <div className={`flex items-center gap-2 text-sm ${className}`}>
      <span style={{ color: "var(--color-muted)" }}>Sort by</span>
      <select
        value={sortState.key}
        onChange={(e) => onSort(e.target.value)}
        className="px-3 py-1.5 rounded-lg text-sm font-medium"
        style={{ background: "var(--color-surface-2)", border: "1px solid transparent", color: "var(--color-ink)" }}
      >
        {options.map((c) => (
          <option key={c.key} value={c.key}>{c.label}</option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => onSort(sortState.key)}
        aria-label={sortState.dir === "desc" ? "Sorted high to low — tap to flip" : "Sorted low to high — tap to flip"}
        className="inline-flex items-center justify-center w-9 self-stretch rounded-lg"
        style={{ background: "var(--color-surface-2)", color: "var(--color-sage)" }}
      >
        <Glyph name={sortState.dir === "desc" ? "arrow-down" : "arrow-up"} size={16} strokeWidth={2.25} />
      </button>
    </div>
  );
}

/** Phone rendering of a generic table row: the first text column is the title,
 *  the next text columns a subtitle, and the active sort column (or the first
 *  number) the trailing value. */
function MobileRow<T extends object>({ row, rank, columns, sortState }: {
  row: T;
  rank: number;
  columns: ColDef<T>[];
  sortState: SortState;
}) {
  const show = (c: ColDef<T>) => {
    const v = c.getValue(row);
    return c.formatter ? c.formatter(v, row) : (v ?? "—");
  };
  const rankCol = columns.find((c) => c.autoRank);
  const strings = columns.filter((c) => c.type === "string" && !c.autoRank);
  // A table with no text column (e.g. a per-year table) titles each row by its
  // first column instead.
  const title = strings[0] ?? columns.find((c) => !c.autoRank);
  const nums = columns.filter((c) => c.type === "numeric" && !c.autoRank && c !== title);
  const subs = strings.slice(1, 3);
  const active = columns.find((c) => c.key === sortState.key);
  const trailing = active && active.type === "numeric" && !active.autoRank ? active : nums[0];
  const extra = nums.find((c) => c !== trailing);
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      {rankCol && (
        <span className="w-6 text-right font-display italic text-sm shrink-0" style={{ color: "var(--color-faint)" }}>
          {rank}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium leading-snug line-clamp-2 break-words" style={{ color: "var(--color-ink)" }}>
          {title ? show(title) : null}
        </div>
        {subs.length > 0 && (
          <div className="text-xs mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
            {subs.map((c, i) => (
              <span key={c.key}>{i > 0 ? " · " : ""}{show(c)}</span>
            ))}
          </div>
        )}
      </div>
      {trailing && (
        <div className="text-right shrink-0">
          <div className="text-sm font-semibold tabular-nums" style={{ color: "var(--color-sage)" }}>
            {show(trailing)}
          </div>
          <div className="text-[11px]" style={{ color: "var(--color-faint)" }}>
            {trailing.label}
            {extra ? <> · {extra.label} {show(extra)}</> : null}
          </div>
        </div>
      )}
    </li>
  );
}

/* ── SortableTable (generic simple renderer) ───────────────────────────────── */

export function SortableTable<T extends object>({
  columns,
  data,
  defaultSort,
  getRowKey,
  emptyMessage = "No data.",
  tableStyle,
  scrollX = false,
  pinFirst,
}: {
  columns: ColDef<T>[];
  data: T[];
  defaultSort: SortState;
  getRowKey: (row: T, idx: number) => string;
  emptyMessage?: string;
  tableStyle?: React.CSSProperties;
  /** Allow horizontal scroll for wide tables instead of clipping (default: clip). */
  scrollX?: boolean;
  /** Rows matching this predicate stay pinned to the top regardless of sort. */
  pinFirst?: (row: T) => boolean;
}) {
  const { sorted, sortState, handleSort } = useSortable(data, columns, defaultSort, pinFirst);
  // Open short; expand on request (shared by both renderings below).
  const { visible, ...limit } = useRowLimit(sorted);

  return (
    <>
      {/* Phones: a sortable list */}
      <div className="md:hidden">
        <MobileSortBar cols={columns} sortState={sortState} onSort={handleSort} />
        <ul
          className="rounded-xl overflow-hidden divide-y"
          style={{ background: "var(--color-surface)", boxShadow: "var(--shadow-card)", borderColor: "var(--color-rule)" }}
        >
          {sorted.length === 0 ? (
            <li className="px-4 py-8 text-center text-sm" style={{ color: "var(--color-muted)" }}>
              {emptyMessage}
            </li>
          ) : (
            visible.map((row, i) => (
              <MobileRow key={getRowKey(row, i)} row={row} rank={i + 1} columns={columns} sortState={sortState} />
            ))
          )}
        </ul>
        <ShowMoreBar {...limit} noun="rows" />
      </div>

      {/* Desktop: an app-style table on a card */}
      <div
        className="hidden md:block rounded-xl"
        style={{
          background: "var(--color-surface)",
          boxShadow: "var(--shadow-card)",
          overflowX: scrollX ? "auto" : "hidden",
          overflowY: "hidden",
        }}
      >
        <table
          className="w-full text-sm"
          style={{ borderCollapse: "collapse", ...tableStyle }}
        >
          <thead>
            <tr>
              {columns.map((col) => (
                <SortableTh key={col.key} col={col} sortState={sortState} onSort={handleSort} />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-4 py-8 text-center text-sm"
                  style={{ color: "var(--color-muted)" }}
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              visible.map((row, i) => (
                <tr
                  key={getRowKey(row, i)}
                  className="app-row"
                  style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}
                >
                  {columns.map((col) => {
                    const val = col.getValue(row);
                    const active = sortState.key === col.key;
                    const align = col.align ?? (col.type === "numeric" ? "right" : "left");
                    return (
                      <td
                        key={col.key}
                        className={`px-3 py-2.5 text-${align}`}
                        style={{
                          color: active ? "var(--color-sage)" : "var(--color-ink)",
                          fontWeight: active ? 600 : undefined,
                          fontVariantNumeric: col.type === "numeric" ? "tabular-nums" : undefined,
                        }}
                      >
                        {col.autoRank ? i + 1 : col.formatter ? col.formatter(val, row) : (val ?? "—")}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="hidden md:block">
        <ShowMoreBar {...limit} noun="rows" />
      </div>
    </>
  );
}
