/**
 * Skeleton primitives — the placeholder shapes shown while a page's data is
 * still in flight.
 *
 * All of these are plain presentational Server Components (no hooks, no
 * "use client"), so a route's `loading.tsx` stays in the server graph and ships
 * no extra JS. Every colour comes from the existing Fable tokens via the
 * `.skeleton` class in globals.css — nothing here introduces a new style.
 *
 * The house pattern: keep the real page heading (it is known before the data
 * arrives, so showing it is more useful than a grey bar) and skeletonise only
 * the part that depends on the fetch. See `PageSkeleton`.
 */

import type { CSSProperties, ReactNode } from "react";

/* ── Atom ─────────────────────────────────────────────────────────────────── */

/** One shimmering placeholder block. Size it with `w` / `h` (any CSS length). */
export function Skeleton({
  w = "100%",
  h = "1rem",
  radius,
  className = "",
  style,
}: {
  w?: string | number;
  h?: string | number;
  /** Override the default 0.375rem corner (e.g. "9999px" for a pill). */
  radius?: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div
      aria-hidden
      className={`skeleton ${className}`}
      style={{ width: w, height: h, borderRadius: radius, ...style }}
    />
  );
}

/** A paragraph of placeholder lines; the last one is short, as real text is. */
export function SkeletonText({
  lines = 3,
  className = "",
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} h="0.7rem" w={i === lines - 1 ? "60%" : "100%"} />
      ))}
    </div>
  );
}

/* ── Page chrome ──────────────────────────────────────────────────────────── */

/**
 * The standard page shell used by every `loading.tsx`: the real title (and an
 * optional real subtitle), then a placeholder for the one line of summary copy
 * that depends on the data, then the caller's skeleton body.
 *
 * `role="status"` + `aria-busy` announce the wait once to a screen reader
 * instead of letting it read a screenful of empty boxes.
 */
export function PageSkeleton({
  title,
  subtitle,
  titleClassName = "font-display text-3xl font-bold leading-tight",
  toggle,
  toggleTabs = 2,
  children,
}: {
  /**
   * The page's real heading. Omit only when it genuinely isn't knowable before
   * the fetch (a public profile's display name) — a placeholder bar renders
   * instead.
   */
  title?: string;
  /** Static subtitle copy, when the page's subtitle doesn't depend on the data. */
  subtitle?: string;
  /** Match pages that use a smaller heading (Calibration, Track Record, …). */
  titleClassName?: string;
  /**
   * Reserve the pill-toggle's space where the real page has one. "above" for
   * the views whose Fiction/Nonfiction TypeToggle sits over the heading
   * (Tier List, Series, Reading); "below" for in-page sub-tabs.
   */
  toggle?: "above" | "below";
  toggleTabs?: number;
  children?: ReactNode;
}) {
  return (
    <div role="status" aria-busy="true" aria-label={`Loading ${title ?? "page"}`}>
      <span className="sr-only">Loading {title ?? "page"}…</span>
      {toggle === "above" && <SkeletonToggle tabs={toggleTabs} />}
      <div className="mb-6">
        {title ? (
          <h1 className={titleClassName} style={{ color: "var(--color-ink)" }}>
            {title}
          </h1>
        ) : (
          <Skeleton w="14rem" h="2rem" />
        )}
        {subtitle ? (
          <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
            {subtitle}
          </p>
        ) : (
          <Skeleton w="18rem" h="0.8rem" className="mt-2" />
        )}
      </div>
      {toggle === "below" && <SkeletonToggle tabs={toggleTabs} />}
      {children}
    </div>
  );
}

/** Placeholder matching the TypeToggle / SubTabs pill bar. */
export function SkeletonToggle({ tabs = 3 }: { tabs?: number }) {
  return (
    <div
      aria-hidden
      className="flex gap-1 mb-6 p-1 rounded-xl inline-flex"
      style={{ background: "var(--color-surface-2)" }}
    >
      {Array.from({ length: tabs }, (_, i) => (
        <Skeleton key={i} w="4.5rem" h="1.75rem" radius="0.5rem" />
      ))}
    </div>
  );
}

/* ── Composites ───────────────────────────────────────────────────────────── */

/** Placeholder for a `SortableTable`: header rule + evenly spaced rows. */
export function SkeletonTable({
  rows = 8,
  cols = 5,
}: {
  rows?: number;
  cols?: number;
}) {
  return (
    <div
      aria-hidden
      className="rounded-xl overflow-hidden"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <div
        className="flex items-center gap-4 px-4 py-3"
        style={{ borderBottom: "1px solid var(--color-rule)" }}
      >
        {Array.from({ length: cols }, (_, i) => (
          <Skeleton key={i} h="0.65rem" w={i === 0 ? "35%" : "12%"} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div
          key={r}
          className="flex items-center gap-4 px-4 py-3"
          style={{ borderBottom: r === rows - 1 ? "none" : "1px solid var(--color-rule)" }}
        >
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} h="0.7rem" w={c === 0 ? "35%" : "12%"} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a list of book cards (WA badge + title/author + chips). */
export function SkeletonCardList({ cards = 5 }: { cards?: number }) {
  return (
    <div aria-hidden className="space-y-3">
      {Array.from({ length: cards }, (_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 px-5 py-4 rounded-xl"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
        >
          <Skeleton w="2.5rem" h="2.5rem" radius="9999px" />
          <div className="flex-1 space-y-2">
            <Skeleton h="0.85rem" w={`${70 - i * 6}%`} />
            <Skeleton h="0.65rem" w="35%" />
          </div>
          <Skeleton w="4rem" h="1.1rem" radius="9999px" />
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a row of `StatCard`s. */
export function SkeletonStatCards({
  count = 4,
  cols = "grid-cols-2 sm:grid-cols-4",
}: {
  count?: number;
  cols?: string;
}) {
  return (
    <div aria-hidden className={`grid gap-3 ${cols}`}>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="rounded-xl p-4 flex flex-col gap-2"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
        >
          <Skeleton h="0.6rem" w="55%" />
          <Skeleton h="1.4rem" w="40%" />
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a chart/plot panel — a titled card with a blank plot area. */
export function SkeletonChart({ h = "14rem", title = true }: { h?: string; title?: boolean }) {
  return (
    <div
      aria-hidden
      className="rounded-xl p-5"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      {title && <Skeleton h="0.8rem" w="12rem" className="mb-4" />}
      <Skeleton h={h} radius="0.5rem" />
    </div>
  );
}

/** Placeholder for a bordered panel of prose (methodology-style sections). */
export function SkeletonPanel({ lines = 4 }: { lines?: number }) {
  return (
    <div
      aria-hidden
      className="rounded-xl p-5"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <Skeleton h="0.9rem" w="40%" className="mb-3" />
      <SkeletonText lines={lines} />
    </div>
  );
}

/** Placeholder for the tier ladder: one labelled band per tier. */
export function SkeletonTierLadder() {
  const spines = ["sp", "s", "a", "b", "c", "d", "f"] as const;
  return (
    <div aria-hidden className="space-y-3">
      {spines.map((t, i) => (
        <div
          key={t}
          className={`book-card spine-${t} flex items-center gap-4 px-5 py-4`}
          style={{ border: "1px solid var(--color-rule)", borderLeftWidth: "3px" }}
        >
          <Skeleton w="2rem" h="1.5rem" />
          <div className="flex-1 flex flex-wrap gap-2">
            {Array.from({ length: 6 - (i % 3) }, (_, j) => (
              <Skeleton key={j} w="7rem" h="1.6rem" radius="0.5rem" />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
