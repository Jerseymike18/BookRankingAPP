"use client";

import { useState, useMemo } from "react";

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
  defaultSort: SortState
): { sorted: T[]; sortState: SortState; handleSort: (key: string) => void } {
  const [sortState, setSortState] = useState<SortState>(defaultSort);

  const sorted = useMemo(() => {
    const col = cols.find((c) => c.key === sortState.key);
    if (!col) return data;
    const mult = sortState.dir === "desc" ? -1 : 1;
    return [...data]
      .map((row, idx) => ({ row, idx }))
      .sort(({ row: a, idx: ai }, { row: b, idx: bi }) => {
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
  }, [data, cols, sortState]);

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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function SortableTh({ col, sortState, onSort, extraStyle }: {
  col: ColDef<any>;
  sortState: SortState;
  onSort: (key: string) => void;
  extraStyle?: React.CSSProperties;
}) {
  const active = sortState.key === col.key;
  const sortable = col.sortable !== false;
  const align = col.align ?? (col.type === "numeric" ? "right" : "left");

  return (
    <th
      onClick={sortable ? () => onSort(col.key) : undefined}
      className={`px-3 py-2 text-${align} text-xs font-semibold uppercase tracking-wider whitespace-nowrap${
        sortable ? " cursor-pointer select-none" : ""
      }`}
      style={{
        color: active ? "var(--color-sage)" : "var(--color-muted)",
        background: active ? "var(--color-sage-light)" : "transparent",
        borderBottom: "1px solid var(--color-rule)",
        ...extraStyle,
      }}
    >
      {col.label}
      {sortable && active ? (sortState.dir === "desc" ? " ▼" : " ▲") : ""}
    </th>
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
}: {
  columns: ColDef<T>[];
  data: T[];
  defaultSort: SortState;
  getRowKey: (row: T, idx: number) => string;
  emptyMessage?: string;
  tableStyle?: React.CSSProperties;
}) {
  const { sorted, sortState, handleSort } = useSortable(data, columns, defaultSort);

  return (
    <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--color-rule)" }}>
      <table
        className="w-full text-sm"
        style={{ borderCollapse: "collapse", ...tableStyle }}
      >
        <thead>
          <tr style={{ background: "var(--color-surface-2)" }}>
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
            sorted.map((row, i) => (
              <tr
                key={getRowKey(row, i)}
                style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}
              >
                {columns.map((col) => {
                  const val = col.getValue(row);
                  const active = sortState.key === col.key;
                  const align = col.align ?? (col.type === "numeric" ? "right" : "left");
                  return (
                    <td
                      key={col.key}
                      className={`px-4 py-2.5 text-${align}`}
                      style={{
                        color: active ? "var(--color-sage)" : "var(--color-ink)",
                        background: active ? "var(--color-sage-light)" : "transparent",
                        fontVariantNumeric: col.type === "numeric" ? "tabular-nums" : undefined,
                      }}
                    >
                      {col.formatter ? col.formatter(val, row) : (val ?? "—")}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
