/**
 * ProgressBar — the one bar every long-running operation uses.
 *
 * Two modes, one look (sage fill on a rule-coloured track, from the
 * `.progress-*` classes in globals.css):
 *
 *   determinate    pass `value` (+ `max`) when the work has countable steps —
 *                  scoring 7 candidates, saving 12 books, classifying a CSV.
 *   indeterminate  omit `value` when the duration is genuinely unknowable — a
 *                  single grounded LLM call is anywhere from a second (warm
 *                  cache) to well over a minute, so a fake percentage would be
 *                  a lie. The bar sweeps instead.
 *
 * Presentational only — no hooks — so it can render from a Server Component
 * (a route's `loading.tsx`) as well as from the client flows.
 */

export function ProgressBar({
  value,
  max = 1,
  label,
  hint,
  className = "",
}: {
  /** Completed units. Omit for the indeterminate sweep. */
  value?: number;
  /** Total units (default 1, i.e. `value` is already a 0–1 fraction). */
  max?: number;
  /** Line under the bar, e.g. "Scoring 3 / 7: Dune". */
  label?: string;
  /** Quieter second line, e.g. an explanation of why this one is slow. */
  hint?: string;
  className?: string;
}) {
  const indeterminate = value == null;
  const pct = indeterminate
    ? 0
    : Math.max(0, Math.min(100, (value / (max || 1)) * 100));

  return (
    <div className={className}>
      <div
        className="progress-track"
        role="progressbar"
        aria-label={label ?? "Working"}
        {...(indeterminate
          ? {}
          : { "aria-valuenow": Math.round(pct), "aria-valuemin": 0, "aria-valuemax": 100 })}
      >
        <div
          className={`progress-fill${indeterminate ? " progress-fill-indeterminate" : ""}`}
          style={indeterminate ? undefined : { width: `${pct}%` }}
        />
      </div>
      {label && (
        <p className="text-xs mt-1" style={{ color: "var(--color-muted)" }}>
          {label}
        </p>
      )}
      {hint && (
        <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>
          {hint}
        </p>
      )}
    </div>
  );
}
