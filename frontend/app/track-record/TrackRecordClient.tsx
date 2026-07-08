"use client";

import { useRef, useState } from "react";
import type {
  TrackRecord,
  TrackRecordFold,
  TrackRecordRollingPoint,
  TrackRecordGenreRow,
  TrackRecordIntervalRow,
} from "@/lib/types";

/* ── formatting ─────────────────────────────────────────────────────────── */
const f2 = (v: number) => v.toFixed(2);
const f3 = (v: number) => v.toFixed(3);
const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;

// Accuracy ramp: sage (accurate) → terracotta (big miss). Both are existing
// design tokens (spine-s / spine-c), mixed the way AnalyticsClient does it.
const SAGE = "#4A7C59";
const TERRA = "#C07C5A";
function mixHex(a: string, b: string, t: number) {
  const pa = [parseInt(a.slice(1, 3), 16), parseInt(a.slice(3, 5), 16), parseInt(a.slice(5, 7), 16)];
  const pb = [parseInt(b.slice(1, 3), 16), parseInt(b.slice(3, 5), 16), parseInt(b.slice(5, 7), 16)];
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * Math.max(0, Math.min(1, t))));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
const errColor = (absErr: number) => mixHex(SAGE, TERRA, absErr / 2); // ≥2.0 WA = full terracotta

/* ── shared primitives (match CalibrationClient / AnalyticsClient) ──────── */
function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-sm font-semibold uppercase tracking-wide mt-10 mb-2" style={{ color: "var(--color-muted)" }}>
      {children}
    </h2>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="comp-tile flex flex-col gap-1" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
      <span className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>{label}</span>
      <span className="text-xl font-semibold tabular-nums" style={{ color: "var(--color-ink)" }}>{value}</span>
      {note && <span className="text-xs" style={{ color: "var(--color-muted)" }}>{note}</span>}
    </div>
  );
}

interface Tip { x: number; y: number; lines: string[]; flip: boolean }
function useChartTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  function show(e: React.MouseEvent, lines: string[]) {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setTip({ x, y, lines, flip: x > rect.width * 0.6 });
  }
  return { ref, tip, show, hide: () => setTip(null) };
}
function TipBox({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div
      style={{
        position: "absolute", top: tip.y + 14, left: tip.x + (tip.flip ? -14 : 14),
        transform: tip.flip ? "translateX(-100%)" : "none", pointerEvents: "none",
        background: "var(--color-ink)", color: "#fff", padding: "5px 8px", borderRadius: 6,
        fontSize: 11, lineHeight: 1.35, whiteSpace: "nowrap", zIndex: 30,
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      {tip.lines.map((l, i) => (
        <div key={i} style={{ fontVariantNumeric: "tabular-nums" }}>{l}</div>
      ))}
    </div>
  );
}

/* ── 1. Rolling-MAE curve — "the engine getting smarter as the library grew" ─ */
function RollingChart({ series, lifetimeMae, burnIn }: { series: TrackRecordRollingPoint[]; lifetimeMae: number; burnIn: number }) {
  const { ref, tip, show, hide } = useChartTip();
  if (series.length < 2) return null;
  const W = 720, H = 320, padL = 44, padR = 18, padT = 16, padB = 42;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const xs = series.map((s) => s.pool_size);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yVals = series.map((s) => s.honest_rolling_mae);
  const yMax = Math.max(1, Math.ceil(Math.max(...yVals, lifetimeMae) * 2) / 2);
  const xOf = (p: number) => padL + ((p - xMin) / (xMax - xMin || 1)) * plotW;
  const yOf = (m: number) => padT + ((yMax - m) / (yMax || 1)) * plotH;

  const line = series.map((s) => `${xOf(s.pool_size)},${yOf(s.honest_rolling_mae)}`).join(" ");
  const yTicks = Array.from({ length: 5 }, (_, i) => (yMax * i) / 4);
  const xTicks = Array.from({ length: 5 }, (_, i) => Math.round(xMin + ((xMax - xMin) * i) / 4));

  return (
    <div ref={ref} style={{ position: "relative" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label="Rolling prediction error as the library grew">
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} y1={yOf(v)} x2={W - padR} y2={yOf(v)} stroke="var(--color-rule)" strokeWidth={1} />
            <text x={padL - 6} y={yOf(v) + 3.5} textAnchor="end" fontSize={10} fill="var(--color-faint)" style={{ fontVariantNumeric: "tabular-nums" }}>{v.toFixed(2)}</text>
          </g>
        ))}
        {xTicks.map((v, i) => (
          <text key={i} x={xOf(v)} y={H - padB + 16} textAnchor="middle" fontSize={10} fill="var(--color-faint)">{v}</text>
        ))}
        <text x={padL + plotW / 2} y={H - 4} textAnchor="middle" fontSize={11} fill="var(--color-muted)">books the engine had already read →</text>
        <text x={-(padT + plotH / 2)} y={13} transform="rotate(-90)" textAnchor="middle" fontSize={11} fill="var(--color-muted)">rolling WA error ({series[series.length - 1].window_n}-book window)</text>

        {/* lifetime honest MAE reference */}
        <line x1={padL} y1={yOf(lifetimeMae)} x2={W - padR} y2={yOf(lifetimeMae)} stroke="var(--color-sage)" strokeWidth={1} strokeDasharray="4 4" opacity={0.55} />
        <text x={W - padR} y={yOf(lifetimeMae) - 4} textAnchor="end" fontSize={10} fill="var(--color-sage)" style={{ fontVariantNumeric: "tabular-nums" }}>lifetime {f2(lifetimeMae)}</text>

        {/* the curve */}
        <polyline points={line} fill="none" stroke="var(--color-sage)" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {series.map((s, i) => (
          <circle key={i} cx={xOf(s.pool_size)} cy={yOf(s.honest_rolling_mae)} r={6} fill="transparent"
            onMouseMove={(e) => show(e, [s.title, `after ${s.pool_size} books`, `rolling MAE ${f3(s.honest_rolling_mae)}`])} />
        ))}
        {series.map((s, i) => (
          <circle key={`d${i}`} cx={xOf(s.pool_size)} cy={yOf(s.honest_rolling_mae)} r={1.8} fill="var(--color-sage)" pointerEvents="none" />
        ))}
      </svg>
      <p className="text-xs mt-1" style={{ color: "var(--color-faint)" }}>
        The window starts at {burnIn} books (a burn-in so the engine has something to learn from). Lower is better.
      </p>
      <TipBox tip={tip} />
    </div>
  );
}

/* ── 2. Predicted vs actual scatter (honest variant) ────────────────────── */
function PredScatter({ folds }: { folds: TrackRecordFold[] }) {
  const { ref, tip, show, hide } = useChartTip();
  const W = 560, H = 520, padL = 44, padR = 16, padT = 16, padB = 42;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const all = folds.flatMap((f) => [f.actual_wa, f.predicted_wa]);
  const lo = Math.floor(Math.min(...all) * 2) / 2;
  const hi = Math.ceil(Math.max(...all) * 2) / 2;
  const xOf = (w: number) => padL + ((w - lo) / (hi - lo || 1)) * plotW;
  const yOf = (w: number) => padT + ((hi - w) / (hi - lo || 1)) * plotH;
  const ticks = Array.from({ length: Math.round(hi - lo) + 1 }, (_, i) => lo + i).filter((v) => Number.isInteger(v));

  return (
    <div ref={ref} style={{ position: "relative", maxWidth: 560, margin: "0 auto" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label="Predicted versus actual score, one point per book">
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={padL} y1={yOf(v)} x2={W - padR} y2={yOf(v)} stroke="var(--color-rule)" strokeWidth={1} />
            <line x1={xOf(v)} y1={padT} x2={xOf(v)} y2={H - padB} stroke="var(--color-rule)" strokeWidth={1} />
            <text x={padL - 6} y={yOf(v) + 3.5} textAnchor="end" fontSize={10} fill="var(--color-faint)" style={{ fontVariantNumeric: "tabular-nums" }}>{v}</text>
            <text x={xOf(v)} y={H - padB + 16} textAnchor="middle" fontSize={10} fill="var(--color-faint)">{v}</text>
          </g>
        ))}
        {/* perfect-prediction diagonal y = x */}
        <line x1={xOf(lo)} y1={yOf(lo)} x2={xOf(hi)} y2={yOf(hi)} stroke="var(--color-ink)" strokeWidth={1} strokeDasharray="5 4" opacity={0.35} />
        <text x={xOf(hi) - 4} y={yOf(hi) + 14} textAnchor="end" fontSize={10} fill="var(--color-muted)" opacity={0.8}>perfect = on the line</text>

        <text x={padL + plotW / 2} y={H - 4} textAnchor="middle" fontSize={11} fill="var(--color-muted)">actual score I gave it →</text>
        <text x={-(padT + plotH / 2)} y={13} transform="rotate(-90)" textAnchor="middle" fontSize={11} fill="var(--color-muted)">predicted score (before reading)</text>

        {folds.map((f, i) => (
          <circle
            key={i}
            cx={xOf(f.actual_wa)}
            cy={yOf(f.predicted_wa)}
            r={4}
            fill={errColor(f.abs_error)}
            fillOpacity={0.72}
            stroke={errColor(f.abs_error)}
            strokeWidth={0.75}
            onMouseMove={(e) => show(e, [f.title, f.genre, `actual ${f2(f.actual_wa)} · predicted ${f2(f.predicted_wa)}`, `off by ${f2(f.abs_error)}`])}
          />
        ))}
      </svg>
      <p className="text-xs mt-1 text-center" style={{ color: "var(--color-faint)" }}>
        Each dot is one book. Green = close; terracotta = a bigger miss. Points below the line were over-predicted, above were under-predicted.
      </p>
      <TipBox tip={tip} />
    </div>
  );
}

/* ── 3. Interval coverage bars (nominal vs measured) ────────────────────── */
function CoverageBar({ row, tone, verdict }: { row: TrackRecordIntervalRow; tone: string; verdict: string }) {
  if (row.measured == null) return null;
  const W = 560, H = 46, padL = 4, padR = 4, trackY = 14, trackH = 16;
  const trackW = W - padL - padR;
  const xOf = (frac: number) => padL + frac * trackW;
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-sm" style={{ color: "var(--color-ink)" }}>{row.label}</span>
        <span className="text-sm font-semibold tabular-nums" style={{ color: tone }}>
          {pct1(row.measured)}<span className="font-normal" style={{ color: "var(--color-muted)" }}> measured</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label={`${row.label}: claimed ${pct1(row.nominal)}, measured ${pct1(row.measured)}`}>
        <rect x={padL} y={trackY} width={trackW} height={trackH} rx={4} fill="var(--color-surface-2)" />
        <rect x={padL} y={trackY} width={Math.max(2, xOf(row.measured) - padL)} height={trackH} rx={4} fill={tone} opacity={0.85} />
        {/* claimed-level marker */}
        <line x1={xOf(row.nominal)} y1={trackY - 4} x2={xOf(row.nominal)} y2={trackY + trackH + 4} stroke="var(--color-ink)" strokeWidth={1.5} />
        <text x={xOf(row.nominal)} y={trackY + trackH + 16} textAnchor="middle" fontSize={10} fill="var(--color-muted)" style={{ fontVariantNumeric: "tabular-nums" }}>claims {pct1(row.nominal)}</text>
        <text x={W - padR} y={11} textAnchor="end" fontSize={10} fill={tone}>{verdict}</text>
      </svg>
    </div>
  );
}

/* ── graceful empty state ───────────────────────────────────────────────── */
function NotAvailable() {
  return (
    <main className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="font-display text-2xl font-semibold mb-1" style={{ color: "var(--color-ink)" }}>Track Record</h1>
      <div className="rounded-md border px-4 py-8 text-center text-sm mt-6" style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)", color: "var(--color-muted)" }}>
        The walk-forward backtest hasn&apos;t been generated yet. Run <code className="font-mono">python3 walkforward.py</code> to produce the validation artifacts, then re-export the snapshot.
      </div>
    </main>
  );
}

/* ── page ───────────────────────────────────────────────────────────────── */
export default function TrackRecordClient({ data }: { data: TrackRecord | null }) {
  if (!data) return <NotAvailable />;

  const { headline, folds, rolling, mae_by_genre, interval_coverage, caveats, provenance } = data;
  const served = interval_coverage.served_conformal;
  const legacy = interval_coverage.legacy_resid_sd;
  const improvePct = headline.naive_wa_mae > 0
    ? Math.round(((headline.naive_wa_mae - headline.honest_wa_mae) / headline.naive_wa_mae) * 100)
    : 0;

  return (
    <main className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="font-display text-2xl font-semibold mb-1" style={{ color: "var(--color-ink)" }}>Track Record</h1>
      <p className="text-sm mb-6" style={{ color: "var(--color-muted)" }}>
        How well the engine predicts a book&apos;s score <em>before</em> I read it — measured honestly, on books it had never seen.
      </p>

      {/* ── Headline ── */}
      <div className="rounded-lg border px-5 py-5" style={{ borderColor: "var(--color-rule)", background: "var(--color-sage-light)" }}>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-display text-4xl font-semibold tabular-nums" style={{ color: "var(--color-sage)" }}>{f2(headline.honest_wa_mae)}</span>
          <span className="text-sm" style={{ color: "var(--color-ink)" }}>average error (WA points, 0–10 scale)</span>
        </div>
        <p className="text-sm mt-2" style={{ color: "var(--color-ink)" }}>
          Across {headline.n_folds} books, the engine&apos;s pre-read prediction landed within about <strong>{f2(headline.honest_wa_mae)}</strong> of the
          score I actually gave — {improvePct}% better than guessing the library average.
        </p>
        <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
          This is the <strong>honest, chronological</strong> number: each book was predicted using only the books I&apos;d read <em>before</em>{" "}it,
          so no future ratings leak in. It&apos;s the &ldquo;what was knowable then&rdquo; accuracy, not a hindsight fit.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
        <Stat label="Honest MAE" value={f3(headline.honest_wa_mae)} note="corrected, no leakage" />
        <Stat label="Raw MAE" value={f3(headline.raw_wa_mae)} note="research only, uncorrected" />
        <Stat label="Naïve baseline" value={f3(headline.naive_wa_mae)} note="predict the mean" />
        <Stat label="Books tested" value={String(headline.n_folds)} note={`of ${headline.n_books_total} (burn-in ${headline.burn_in})`} />
      </div>

      {/* ── Rolling curve ── */}
      <SectionHeader>Getting smarter as the library grew</SectionHeader>
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        Trailing-window prediction error over the reading history. As the engine accumulates more of my taste to reason from, recent error trends down.
      </p>
      <RollingChart series={rolling.series} lifetimeMae={headline.honest_wa_mae} burnIn={headline.burn_in} />

      {/* ── Scatter ── */}
      <SectionHeader>Predicted vs. actual</SectionHeader>
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        One point per book: what the engine predicted (before reading) against the score I ended up giving. The closer to the diagonal, the better the call.
      </p>
      <PredScatter folds={folds} />

      {/* ── MAE by genre ── */}
      <SectionHeader>Where it&apos;s strong and weak (by genre)</SectionHeader>
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        Worst-predicted genres first — an honest look at where taste is hardest to pin down. Small-<em>n</em> genres are noisy.
      </p>
      <div className="rounded-md border overflow-hidden text-sm" style={{ borderColor: "var(--color-rule)" }}>
        <table className="w-full">
          <thead>
            <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
              {["Genre", "n", "Honest MAE", "Raw MAE", "Δ honest−raw"].map((h) => (
                <th key={h} className={`px-4 py-2 font-medium ${h === "Genre" ? "text-left" : "text-right"}`} style={{ color: "var(--color-muted)" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {mae_by_genre.map((row: TrackRecordGenreRow, i) => {
              const delta = row.honest_mae - row.raw_mae; // <0 = correction lowered error (report's Δ honest−raw)
              return (
                <tr key={row.genre} style={{ background: i % 2 === 0 ? "transparent" : "var(--color-surface)", borderTop: "1px solid var(--color-rule)" }}>
                  <td className="px-4 py-2" style={{ color: "var(--color-ink)" }}>{row.genre}</td>
                  <td className="px-4 py-2 text-right tabular-nums" style={{ color: "var(--color-muted)" }}>{row.n}</td>
                  <td className="px-4 py-2 text-right tabular-nums font-mono" style={{ color: "var(--color-ink)" }}>{f3(row.honest_mae)}</td>
                  <td className="px-4 py-2 text-right tabular-nums font-mono" style={{ color: "var(--color-muted)" }}>{f3(row.raw_mae)}</td>
                  <td className="px-4 py-2 text-right tabular-nums font-mono" style={{ color: Math.abs(delta) < 0.02 ? "var(--color-muted)" : delta < 0 ? "var(--color-sage)" : "#C07C5A" }}>
                    {delta > 0 ? "+" : ""}{f3(delta)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
        Δ honest−raw = the change in error from the author/genre bias-correction; negative (green) means the correction lowered error.
      </p>

      {/* ── Interval coverage ── */}
      <SectionHeader>Is the confidence band honest?</SectionHeader>
      <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
        A prediction interval should contain the true score as often as it claims to. The band shown on the Predict page claims 80% — and delivers.
      </p>
      <div className="rounded-md border px-4 py-4 flex flex-col gap-5" style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)" }}>
        <CoverageBar row={served} tone="var(--color-sage)" verdict="on target ✓" />
        <CoverageBar row={legacy} tone="#C07C5A" verdict="overconfident — removed ✗" />
      </div>
      <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
        The old band was a fit-diagnostic masquerading as a prediction interval; it claimed 90% but covered only {pct1(legacy.measured ?? 0)}.
        It was replaced with a density-bucketed conformal band that covers {pct1(served.measured ?? 0)} against its 80% claim.
      </p>

      {/* ── Caveats ── */}
      <SectionHeader>Caveats</SectionHeader>
      <ul className="flex flex-col gap-2">
        {caveats.map((c, i) => (
          <li key={i} className="text-xs pl-3 border-l-2" style={{ color: "var(--color-muted)", borderColor: "var(--color-rule)" }}>{c}</li>
        ))}
      </ul>

      <p className="text-xs mt-8" style={{ color: "var(--color-faint)" }}>
        Backtest at commit <span className="font-mono">{provenance.git_head}</span> · engine <span className="font-mono">{provenance.engine_hash}</span> · generated {provenance.backtest_generated_at}.
        Reads committed validation artifacts; the harness is not re-run to serve this page.
      </p>
    </main>
  );
}
