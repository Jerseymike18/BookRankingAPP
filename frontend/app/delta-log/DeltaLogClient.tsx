"use client";

import { useState, useEffect } from "react";
import { fetchDeltaLog } from "@/lib/api";
import type { DeltaLogResponse, DeltaLogEntry } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function col(comp: string): string {
  return comp.replace(/ /g, "_").replace(/-/g, "_");
}

/** "read Jul 2026" from the book's read date, falling back to the log date. */
function readWhen(entry: DeltaLogEntry): string {
  if (entry.read_year != null && entry.read_month != null) {
    return `read ${MONTHS[entry.read_month - 1] ?? entry.read_month} ${entry.read_year}`;
  }
  if (entry.read_year != null) return `read ${entry.read_year}`;
  return `logged ${entry.logged_at.slice(0, 10)}`;
}

function sign(v: number | null): string {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

function driftColor(v: number | null): string {
  if (v == null) return "var(--color-faint)";
  const abs = Math.abs(v);
  if (abs < 0.3) return "var(--color-sage)";
  if (abs < 0.7) return "var(--color-spine-b)";
  return "var(--color-spine-c)";
}

/* ── Drift summary bar ───────────────────────────────────────────────────── */

function DriftBar({
  components,
  drift,
}: {
  components: string[];
  drift: Record<string, number | null>;
}) {
  return (
    <div
      className="rounded-xl p-5 mb-8"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <h2
        className="text-xs font-semibold uppercase tracking-widest mb-4"
        style={{ color: "var(--color-muted)" }}
      >
        Mean delta per component (predicted − actual, all-time)
      </h2>
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
        {components.map((c) => {
          const v = drift[c] ?? null;
          return (
            <div key={c} className="comp-tile">
              <span className="comp-label">{c}</span>
              <span
                className="comp-value"
                style={{ color: driftColor(v), fontVariantNumeric: "tabular-nums" }}
              >
                {sign(v)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Single entry card ───────────────────────────────────────────────────── */

function EntryCard({
  entry,
  components,
}: {
  entry: DeltaLogEntry;
  components: string[];
}) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className="book-card rounded-xl mb-3"
      style={{ borderLeft: "4px solid var(--color-rule)", padding: "1rem 1.25rem" }}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="font-display font-semibold text-base" style={{ color: "var(--color-ink)" }}>
            {entry.title}
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>
            {readWhen(entry)}
          </p>
        </div>

        {/* WA delta badge */}
        <div className="flex-shrink-0 text-right">
          <p className="text-xs uppercase tracking-widest mb-0.5" style={{ color: "var(--color-muted)" }}>
            WA delta
          </p>
          <span
            className="wa-badge"
            style={{
              background: "transparent",
              border: "1px solid var(--color-rule)",
              color: driftColor(entry.d_wa),
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {sign(entry.d_wa)}
          </span>
          <p className="text-xs mt-1" style={{ color: "var(--color-faint)" }}>
            pred {entry.pred_wa?.toFixed(2) ?? "—"} → actual {entry.act_wa?.toFixed(2) ?? "—"}
          </p>
        </div>
      </div>

      {/* Toggle */}
      <button
        className="text-xs mt-3"
        style={{ color: "var(--color-sage)", background: "none", border: "none", cursor: "pointer", padding: 0 }}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "Hide components ▲" : "Show components ▼"}
      </button>

      {open && (
        <div
          className="grid gap-2 mt-3"
          style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}
        >
          {components.map((c) => {
            const k = col(c);
            const d = entry[`d_${k}`] as number | null;
            return (
              <div key={c} className="comp-tile">
                <span className="comp-label">{c}</span>
                <span
                  className="comp-value"
                  style={{ color: driftColor(d), fontVariantNumeric: "tabular-nums" }}
                >
                  {sign(d)}
                </span>
                <span
                  className="text-xs"
                  style={{ color: "var(--color-faint)", display: "block", marginTop: 2 }}
                >
                  {(entry[`pred_${k}`] as number | null)?.toFixed(1) ?? "—"} →{" "}
                  {(entry[`act_${k}`] as number | null)?.toFixed(1) ?? "—"}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function DeltaLogClient() {
  const [data, setData] = useState<DeltaLogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDeltaLog()
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p style={{ color: "var(--color-spine-c)" }}>{error}</p>;
  if (!data) return <p style={{ color: "var(--color-muted)" }}>Loading…</p>;

  return (
    <div>
      <h1
        className="font-display text-2xl font-semibold mb-1"
        style={{ color: "var(--color-ink)" }}
      >
        Delta Log
      </h1>
      <p className="text-sm mb-8" style={{ color: "var(--color-muted)" }}>
        Prediction accuracy for books that had a stored forecast before being rated,
        in reading order — most recently read first.
        Delta = predicted − actual; positive means the model over-predicted.
      </p>

      {data.entries.length > 0 && (
        <DriftBar components={data.components} drift={data.drift} />
      )}

      {data.entries.length === 0 ? (
        <div
          className="rounded-xl p-8 text-center"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
        >
          <p style={{ color: "var(--color-muted)" }}>
            No deltas recorded yet. Add a rated book that previously had a prediction to start
            tracking accuracy.
          </p>
        </div>
      ) : (
        <>
          <p className="text-xs mb-4" style={{ color: "var(--color-faint)" }}>
            {data.entries.length} prediction{data.entries.length !== 1 ? "s" : ""} recorded
          </p>
          {data.entries.map((e) => (
            <EntryCard key={e.id} entry={e} components={data.components} />
          ))}
        </>
      )}
    </div>
  );
}
