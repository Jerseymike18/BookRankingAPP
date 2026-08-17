"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import type { SeriesResponse, SeriesEntry, EngineParameters } from "@/lib/types";
import { useSortable, SortableTh } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";

/* ── helpers ──────────────────────────────────────────────────────────────── */

const signed = (v: number | null | undefined, d = 3) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`;

const num = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : v.toFixed(d);

/** Total structural adjustment: how far the score sits from a plain mean of the
 *  series' books. The whole point of the model, so it gets its own column. */
const adjustment = (s: SeriesEntry) => (s.adjusted_wa ?? 0) - (s.avg_wa ?? 0);

/** Which single term moved this series most — the one-word answer to "why is it
 *  here and not where its average would put it". */
function dominantTerm(s: SeriesEntry): { label: string; value: number } {
  const terms: [string, number][] = [
    ["Commitment", s.commitment ?? 0],
    ["Peak", s.peak ?? 0],
    ["Floor", s.floor ?? 0],
    ["Finale", s.finale ?? 0],
  ];
  terms.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return { label: terms[0][0], value: terms[0][1] };
}

const POS = "var(--color-sage)";
const NEG = "var(--color-spine-c)";
const tone = (v: number, eps = 0.0005) =>
  Math.abs(v) < eps ? "var(--color-faint)" : v > 0 ? POS : NEG;

/* ── the term-contribution bar ────────────────────────────────────────────── */

/** A diverging bar centred on zero: green right = the structure helped this
 *  series, clay left = it cost it. Scaled to the largest adjustment on screen so
 *  the comparison is honest rather than each row self-normalising. */
function AdjustmentBar({ value, max }: { value: number; max: number }) {
  const half = 50;
  const pct = max > 0 ? (Math.abs(value) / max) * half : 0;
  return (
    <div
      className="relative rounded-sm"
      style={{ height: 8, minWidth: 90, background: "var(--color-surface-2)" }}
      aria-hidden
    >
      <div
        className="absolute"
        style={{ left: "50%", top: -1, bottom: -1, width: 1, background: "var(--color-rule)" }}
      />
      <div
        className="absolute rounded-sm"
        style={{
          top: 0,
          bottom: 0,
          background: tone(value),
          ...(value >= 0
            ? { left: "50%", width: `${pct}%` }
            : { right: "50%", width: `${pct}%` }),
        }}
      />
    </div>
  );
}

/* ── columns ──────────────────────────────────────────────────────────────── */

const COLS: ColDef<SeriesEntry>[] = [
  { key: "series",      label: "Series",     type: "string",  getValue: (s) => s.series,      align: "left"  },
  { key: "books",       label: "n",          type: "numeric", getValue: (s) => s.books,       align: "right" },
  { key: "avg_wa",      label: "Avg WA",     type: "numeric", getValue: (s) => s.avg_wa,      align: "right" },
  { key: "commitment",  label: "Commitment", type: "numeric", getValue: (s) => s.commitment ?? 0, align: "right" },
  { key: "peak",        label: "Peak",       type: "numeric", getValue: (s) => s.peak ?? 0,   align: "right" },
  { key: "floor",       label: "Floor",      type: "numeric", getValue: (s) => s.floor ?? 0,  align: "right" },
  { key: "finale",      label: "Finale",     type: "numeric", getValue: (s) => s.finale ?? 0, align: "right" },
  { key: "adjustment",  label: "Net ±",      type: "numeric", getValue: adjustment,           align: "right" },
  { key: "adjusted_wa", label: "Score",      type: "numeric", getValue: (s) => s.adjusted_wa, align: "right" },
];

/* ── small presentational pieces ──────────────────────────────────────────── */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-10">
      <h2
        className="font-display text-lg font-semibold mb-3"
        style={{ color: "var(--color-ink)" }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

function TermCard({
  name,
  question,
  measures,
  coefficient,
  cap,
  note,
}: {
  name: string;
  question: string;
  measures: string;
  coefficient: string;
  cap: string;
  note?: string;
}) {
  return (
    <div
      className="rounded-xl p-4"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <h3 className="font-display font-semibold mb-1" style={{ color: "var(--color-ink)" }}>
        {name}
      </h3>
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>{question}</p>
      <dl className="text-xs space-y-1" style={{ color: "var(--color-muted)" }}>
        <div className="flex justify-between gap-3">
          <dt>Measures</dt>
          <dd className="text-right" style={{ color: "var(--color-ink)" }}>{measures}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt>Coefficient</dt>
          <dd style={{ color: "var(--color-ink)", fontVariantNumeric: "tabular-nums" }}>{coefficient}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt>Cap</dt>
          <dd style={{ color: "var(--color-ink)", fontVariantNumeric: "tabular-nums" }}>{cap}</dd>
        </div>
      </dl>
      {note && (
        <p className="text-xs mt-3 pt-3" style={{ color: "var(--color-faint)", borderTop: "1px solid var(--color-rule)" }}>
          {note}
        </p>
      )}
    </div>
  );
}

/* ── page ─────────────────────────────────────────────────────────────────── */

export default function SeriesBreakdownClient({
  data,
  params,
}: {
  data: SeriesResponse;
  params: EngineParameters | null;
}) {
  const [showOngoing, setShowOngoing] = useState(true);

  const series = useMemo(
    () => (showOngoing ? data.series : data.series.filter((s) => s.complete)),
    [data.series, showOngoing]
  );

  const { sorted, sortState, handleSort } = useSortable(series, COLS, {
    key: "adjustment",
    dir: "desc",
  });

  const maxAdj = useMemo(
    () => Math.max(0, ...data.series.map((s) => Math.abs(adjustment(s)))),
    [data.series]
  );

  const m = params?.series_model;
  const nComplete = data.series.filter((s) => s.complete).length;

  if (data.series.length === 0) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          No multi-book series yet — rate two books in the same series and this fills in.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <header className="mb-8">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Series Breakdown
        </h1>
        <p className="mt-2 text-sm max-w-3xl" style={{ color: "var(--color-muted)" }}>
          A series is more than the mean of its books. A mean is order-invariant and
          spread-invariant — it cannot tell a series that built to something from one
          that limped, or a consistent run from one with a dud in the middle. This page
          shows the four terms that price what the mean is blind to, and exactly what
          each one did to each series.
        </p>
      </header>

      <Section title="The score">
        <div
          className="rounded-xl p-4 mb-4 text-sm"
          style={{ background: "var(--color-surface-2)", color: "var(--color-ink)" }}
        >
          <code style={{ fontVariantNumeric: "tabular-nums" }}>
            Score = Avg WA + Commitment + clamp(Peak − Floor + Finale, ±
            {m ? m.quality_clamp.toFixed(2) : "0.75"})
          </code>
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          Avg WA sets the broad shape — a series of great books should outrank a series of
          mediocre ones, and that part was already right. The modifiers only re-order
          neighbours. Peak, Floor and Finale share one budget so no series can be carried
          or buried by structure alone; Commitment sits outside it, because it is the
          original length adjustment and was not changed.
        </p>
      </Section>

      <Section title="The four terms">
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
          <TermCard
            name="Commitment"
            question="How long did it sustain that quality?"
            measures="book count"
            coefficient={m ? `${m.commitment.k} × (${m.commitment.base}^(n−1) − 1)` : "—"}
            cap={m?.commitment.in_quality_budget === false ? "outside the shared budget" : "—"}
            note={
              m
                ? `Minus ${m.commitment.short_series_penalty} per book below ${m.commitment.short_series_floor} — a two-book "series" has not yet earned the name.`
                : undefined
            }
          />
          <TermCard
            name="Peak"
            question="Did it ever produce a standout?"
            measures="max WA − avg WA"
            coefficient={m ? `× ${m.peak.k}` : "—"}
            cap={m ? `+${m.peak.cap.toFixed(2)}` : "—"}
            note="A mean flattens the top: one transcendent volume and a uniformly good run average out the same."
          />
          <TermCard
            name="Floor"
            question="Is there a book you had to slog through?"
            measures="avg WA − min WA"
            coefficient={m ? `× ${m.floor.k}` : "—"}
            cap={m ? `−${m.floor.cap.toFixed(2)}` : "—"}
            note={
              m
                ? `The first ${m.floor.tolerance.toFixed(2)} of drop is forgiven as ordinary book-to-book variation — only a real collapse reads as a dud.`
                : undefined
            }
          />
          <TermCard
            name="Finale"
            question="Did it stick the landing?"
            measures="final volume's Ending − mean Ending"
            coefficient={m ? `× ${m.finale.k}` : "—"}
            cap={m ? `+${m.finale.cap_up.toFixed(2)} / −${m.finale.cap_down.toFixed(2)}` : "—"}
            note="Asymmetric on purpose: a botched ending damages a series more than a great one redeems it. Only applied to a series marked finished."
          />
        </div>
      </Section>

      <Section title="Why deviations, not levels">
        <p className="text-sm mb-4 max-w-3xl" style={{ color: "var(--color-muted)" }}>
          Every term is measured against the series&apos; <em>own</em> average. That is not a
          stylistic choice — it is what stops the modifiers from quietly re-weighting Avg WA
          and calling it new information. Measured on this library:
        </p>
        <div style={{ overflowX: "auto" }}>
          <table className="w-full text-sm" style={{ borderCollapse: "collapse", maxWidth: 620 }}>
            <thead>
              <tr style={{ background: "var(--color-surface-2)" }}>
                <th className="px-3 py-2 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Candidate signal</th>
                <th className="px-3 py-2 text-right font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>r with Avg WA</th>
                <th className="px-3 py-2 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["finale's raw Ending score", "+0.84", "redundant", false],
                ["best book's WA", "+0.95", "redundant", false],
                ["worst book's WA", "+0.90", "redundant", false],
                ["finale lift (vs own mean Ending)", "+0.45", "carries new information", true],
                ["peak lift (vs own average)", "+0.03", "carries new information", true],
                ["floor drop (vs own average)", "−0.14", "carries new information", true],
              ].map(([label, r, verdict, good], i) => (
                <tr key={i} style={{ borderTop: "1px solid var(--color-rule)" }}>
                  <td className="px-3 py-2" style={{ color: "var(--color-ink)" }}>{label as string}</td>
                  <td className="px-3 py-2 text-right" style={{ color: "var(--color-muted)", fontVariantNumeric: "tabular-nums" }}>{r as string}</td>
                  <td className="px-3 py-2" style={{ color: good ? POS : NEG }}>{verdict as string}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs mt-3 max-w-3xl" style={{ color: "var(--color-faint)" }}>
          The level forms track Avg WA so closely that adding them would double-count the
          average while looking like a new axis. The same quantities as deviations do not.
        </p>
      </Section>

      <Section title="Every series, term by term">
        <div className="flex items-center flex-wrap gap-3 mb-4">
          <button
            onClick={() => setShowOngoing((v) => !v)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{
              background: showOngoing ? "var(--color-surface)" : "var(--color-sage-light)",
              border: "1px solid var(--color-rule)",
              color: "var(--color-sage)",
              cursor: "pointer",
            }}
          >
            {showOngoing ? "Show finished only" : "Show all series"}
          </button>
          <span className="text-xs" style={{ color: "var(--color-faint)" }}>
            {sorted.length} shown · {nComplete} of {data.series.length} marked finished
            {" "}(only those get a Finale term)
          </span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--color-surface-2)", borderBottom: "1px solid var(--color-rule)" }}>
                {COLS.map((col) => (
                  <SortableTh key={col.key} col={col} sortState={sortState} onSort={handleSort} />
                ))}
                <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>
                  Driven by
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((s, i) => {
                const adj = adjustment(s);
                const dom = dominantTerm(s);
                return (
                  <tr key={s.series} style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}>
                    <td className="px-3 py-2.5 font-medium" style={{ color: "var(--color-ink)" }}>
                      {s.series}
                      {s.complete && (
                        <span className="ml-2 text-xs font-normal" style={{ color: "var(--color-faint)" }}>finished</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right" style={{ color: "var(--color-muted)", fontVariantNumeric: "tabular-nums" }}>{s.books}</td>
                    <td className="px-3 py-2.5 text-right" style={{ color: "var(--color-ink)", fontVariantNumeric: "tabular-nums" }}>{num(s.avg_wa)}</td>
                    {(["commitment", "peak", "floor", "finale"] as const).map((k) => {
                      const v = s[k] ?? 0;
                      return (
                        <td key={k} className="px-3 py-2.5 text-right" style={{ color: tone(v), fontVariantNumeric: "tabular-nums" }}>
                          {signed(v)}
                        </td>
                      );
                    })}
                    <td className="px-3 py-2.5 text-right" style={{ color: tone(adj), fontVariantNumeric: "tabular-nums" }}>{signed(adj)}</td>
                    <td className="px-3 py-2.5 text-right font-semibold" style={{ color: "var(--color-sage)", fontVariantNumeric: "tabular-nums" }}>{num(s.adjusted_wa, 3)}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <AdjustmentBar value={adj} max={maxAdj} />
                        <span className="text-xs whitespace-nowrap" style={{ color: tone(dom.value) }}>{dom.label}</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Reading the raw deviations">
        <div style={{ overflowX: "auto" }}>
          <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--color-surface-2)", borderBottom: "1px solid var(--color-rule)" }}>
                {["Series", "Peak lift (WA)", "Floor drop (WA)", "Finale lift (Ending pts)"].map((h, i) => (
                  <th key={h} className={`px-3 py-2.5 font-semibold text-xs uppercase tracking-wider ${i === 0 ? "text-left" : "text-right"}`} style={{ color: "var(--color-muted)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((s, i) => (
                <tr key={s.series} style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}>
                  <td className="px-3 py-2.5" style={{ color: "var(--color-ink)" }}>{s.series}</td>
                  <td className="px-3 py-2.5 text-right" style={{ color: "var(--color-muted)", fontVariantNumeric: "tabular-nums" }}>{num(s.peak_lift)}</td>
                  <td className="px-3 py-2.5 text-right" style={{ color: "var(--color-muted)", fontVariantNumeric: "tabular-nums" }}>{num(s.floor_drop)}</td>
                  <td className="px-3 py-2.5 text-right" style={{ color: s.complete ? "var(--color-muted)" : "var(--color-faint)", fontVariantNumeric: "tabular-nums" }}>
                    {s.complete ? signed(s.finale_lift, 2) : "not finished"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs mt-3 max-w-3xl" style={{ color: "var(--color-faint)" }}>
          These are the quantities the coefficients above are applied to. Finale lift is in
          Ending points (0–10), not WA — which is why its coefficient is the smallest.
          A one-book series has no spread and no ordering, so it scores zero on all three.
        </p>
      </Section>

      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        Mark a series finished from the{" "}
        <Link href="/series" style={{ color: "var(--color-sage)", textDecoration: "underline" }}>
          Series page
        </Link>
        , or read how the rest of the engine works in the{" "}
        <Link href="/methodology" style={{ color: "var(--color-sage)", textDecoration: "underline" }}>
          Methodology
        </Link>
        .
      </p>
    </div>
  );
}
