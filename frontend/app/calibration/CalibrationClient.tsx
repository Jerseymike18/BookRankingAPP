"use client";

import { useState } from "react";
import { runLooValidation } from "@/lib/api";
import type { CalibrationHealth, LooResult } from "@/lib/types";

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div
      className="comp-tile flex flex-col gap-1"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <span className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>
        {label}
      </span>
      <span className="text-xl font-semibold tabular-nums" style={{ color: "var(--color-ink)" }}>
        {value}
      </span>
      {note && (
        <span className="text-xs" style={{ color: "var(--color-muted)" }}>
          {note}
        </span>
      )}
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-sm font-semibold uppercase tracking-wide mt-8 mb-3"
      style={{ color: "var(--color-muted)" }}
    >
      {children}
    </h2>
  );
}

function verdictColor(verdict: string): string {
  if (verdict === "strong" || verdict === "strong signal") return "var(--color-sage)";
  if (verdict === "okay" || verdict === "moderate") return "var(--color-ink)";
  return "var(--color-muted)";
}

export default function CalibrationClient({ health }: { health: CalibrationHealth }) {
  const [loo, setLoo] = useState<LooResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRunLoo() {
    setRunning(true);
    setError(null);
    try {
      const result = await runLooValidation();
      setLoo(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "LOO validation failed");
    } finally {
      setRunning(false);
    }
  }

  const genres = Object.entries(health.genre_info).sort((a, b) =>
    a[0].localeCompare(b[0])
  );

  return (
    <main className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="font-display text-2xl font-semibold mb-1" style={{ color: "var(--color-ink)" }}>
        Model Calibration
      </h1>
      <p className="text-sm mb-6" style={{ color: "var(--color-muted)" }}>
        Regression health metrics from the live engine ({health.n_books} rated books).
        LOO accuracy requires a separate run — trigger it below.
      </p>

      {/* ── Regression health ── */}
      <SectionHeader>Regression health</SectionHeader>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="R²" value={health.r2.toFixed(4)} note="fit quality (1 = perfect)" />
        <Stat
          label="Residual SD"
          value={health.resid_sd.toFixed(4)}
          note="±1.645× = 90% CI half-width"
        />
        <Stat label="Books" value={String(health.n_books)} />
        <Stat
          label="90% CI half-width"
          value={`±${(1.645 * health.resid_sd).toFixed(3)}`}
          note="WA points"
        />
      </div>

      {/* ── Regression coefficients ── */}
      <SectionHeader>Regression coefficients</SectionHeader>
      <div
        className="rounded-md border overflow-hidden text-sm"
        style={{ borderColor: "var(--color-rule)" }}
      >
        <table className="w-full">
          <thead>
            <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
              <th className="px-4 py-2 text-left font-medium" style={{ color: "var(--color-muted)" }}>
                Term
              </th>
              <th className="px-4 py-2 text-right font-medium" style={{ color: "var(--color-muted)" }}>
                Coefficient
              </th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(health.coeffs).map(([k, v], i) => (
              <tr
                key={k}
                style={{
                  background: i % 2 === 0 ? "transparent" : "var(--color-surface)",
                  borderTop: "1px solid var(--color-rule)",
                }}
              >
                <td className="px-4 py-2 capitalize" style={{ color: "var(--color-ink)" }}>
                  {k}
                </td>
                <td
                  className="px-4 py-2 text-right tabular-nums font-mono"
                  style={{ color: "var(--color-ink)" }}
                >
                  {v >= 0 ? "+" : ""}
                  {v.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Per-genre bias ── */}
      <SectionHeader>Per-genre bias &amp; trust</SectionHeader>
      <div
        className="rounded-md border overflow-hidden text-sm"
        style={{ borderColor: "var(--color-rule)" }}
      >
        <table className="w-full">
          <thead>
            <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
              {["Genre", "n", "Bias", "Trust"].map((h) => (
                <th
                  key={h}
                  className={`px-4 py-2 font-medium ${h === "Genre" ? "text-left" : "text-right"}`}
                  style={{ color: "var(--color-muted)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {genres.map(([genre, info], i) => (
              <tr
                key={genre}
                style={{
                  background: i % 2 === 0 ? "transparent" : "var(--color-surface)",
                  borderTop: "1px solid var(--color-rule)",
                }}
              >
                <td className="px-4 py-2" style={{ color: "var(--color-ink)" }}>
                  {genre}
                </td>
                <td className="px-4 py-2 text-right tabular-nums" style={{ color: "var(--color-muted)" }}>
                  {info.n}
                </td>
                <td
                  className="px-4 py-2 text-right tabular-nums font-mono"
                  style={{
                    color:
                      Math.abs(info.bias) < 0.05
                        ? "var(--color-muted)"
                        : Math.abs(info.bias) < 0.2
                        ? "var(--color-ink)"
                        : "var(--color-sage)",
                  }}
                >
                  {info.bias >= 0 ? "+" : ""}
                  {info.bias.toFixed(4)}
                </td>
                <td
                  className="px-4 py-2 text-right tabular-nums"
                  style={{ color: "var(--color-ink)" }}
                >
                  {(info.trust * 100).toFixed(0)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── LOO section ── */}
      <SectionHeader>Leave-one-out accuracy</SectionHeader>
      <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
        Each book is removed, the engine refit on the remaining {health.n_books - 1}, and the
        held-out book predicted from scratch. This is slow — it runs {health.n_books} full regressions.
      </p>

      {!loo && (
        <button
          onClick={handleRunLoo}
          disabled={running}
          className="px-4 py-2 rounded-md text-sm font-medium transition-colors"
          style={{
            background: running ? "var(--color-rule)" : "var(--color-sage-light)",
            color: running ? "var(--color-muted)" : "var(--color-sage)",
            border: "1px solid var(--color-rule)",
            cursor: running ? "not-allowed" : "pointer",
          }}
        >
          {running ? "Running LOO validation…" : "Run LOO validation"}
        </button>
      )}

      {error && (
        <p className="mt-3 text-sm" style={{ color: "#c07c5a" }}>
          {error}
        </p>
      )}

      {loo && (
        <div>
          {/* Headline */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <Stat
              label="Engine MAE"
              value={loo.engine_mae.toFixed(3)}
              note={`naive baseline: ${loo.naive_mae.toFixed(3)}`}
            />
            <Stat
              label="vs. naive"
              value={`${loo.improvement_pct.toFixed(0)}% better`}
            />
            <Stat
              label="Within ±0.5"
              value={`${(loo.within_0_5 * 100).toFixed(0)}%`}
              note="of books"
            />
            <Stat
              label="Within ±1.0"
              value={`${(loo.within_1_0 * 100).toFixed(0)}%`}
              note="of books"
            />
          </div>

          {/* Bias correction */}
          <div
            className="rounded-md border px-4 py-3 text-sm mb-6"
            style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)" }}
          >
            <span className="font-medium" style={{ color: "var(--color-ink)" }}>
              Bias correction on held-out books:&nbsp;
            </span>
            <span style={{ color: loo.bias_helps ? "var(--color-sage)" : "var(--color-muted)" }}>
              {loo.bias_helps
                ? `helps by ${loo.bias_delta.toFixed(4)} MAE (${((loo.bias_delta / loo.no_bias_mae) * 100).toFixed(1)}% improvement)`
                : loo.bias_delta < 0
                ? `hurts by ${(-loo.bias_delta).toFixed(4)} MAE out-of-sample`
                : "no measurable difference"}
            </span>
          </div>

          {/* Per-genre */}
          <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--color-muted)" }}>
            Per-genre accuracy
          </h3>
          <div
            className="rounded-md border overflow-hidden text-sm mb-6"
            style={{ borderColor: "var(--color-rule)" }}
          >
            <table className="w-full">
              <thead>
                <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
                  {["Genre", "n", "MAE", "Verdict"].map((h) => (
                    <th
                      key={h}
                      className={`px-4 py-2 font-medium ${h === "Genre" || h === "Verdict" ? "text-left" : "text-right"}`}
                      style={{ color: "var(--color-muted)" }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loo.per_genre.map((row, i) => (
                  <tr
                    key={row.genre}
                    style={{
                      background: i % 2 === 0 ? "transparent" : "var(--color-surface)",
                      borderTop: "1px solid var(--color-rule)",
                    }}
                  >
                    <td className="px-4 py-2" style={{ color: "var(--color-ink)" }}>
                      {row.genre}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums" style={{ color: "var(--color-muted)" }}>
                      {row.n}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums font-mono" style={{ color: "var(--color-ink)" }}>
                      {row.mae.toFixed(3)}
                    </td>
                    <td className="px-4 py-2" style={{ color: verdictColor(row.verdict) }}>
                      {row.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Per-component */}
          <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--color-muted)" }}>
            Per-component predictability
          </h3>
          <p className="text-xs mb-2" style={{ color: "var(--color-muted)" }}>
            Lower MAE = more predictable from author/genre analogs alone.
            High-MAE components are where researched scores add the most value.
          </p>
          <div
            className="rounded-md border overflow-hidden text-sm"
            style={{ borderColor: "var(--color-rule)" }}
          >
            <table className="w-full">
              <thead>
                <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
                  {["Component", "MAE", "n", "Verdict"].map((h) => (
                    <th
                      key={h}
                      className={`px-4 py-2 font-medium ${h === "Component" || h === "Verdict" ? "text-left" : "text-right"}`}
                      style={{ color: "var(--color-muted)" }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loo.per_component.map((row, i) => (
                  <tr
                    key={row.component}
                    style={{
                      background: i % 2 === 0 ? "transparent" : "var(--color-surface)",
                      borderTop: "1px solid var(--color-rule)",
                    }}
                  >
                    <td className="px-4 py-2" style={{ color: "var(--color-ink)" }}>
                      {row.component}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums font-mono" style={{ color: "var(--color-ink)" }}>
                      {row.mae.toFixed(3)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums" style={{ color: "var(--color-muted)" }}>
                      {row.n}
                    </td>
                    <td className="px-4 py-2" style={{ color: verdictColor(row.verdict) }}>
                      {row.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <button
            onClick={handleRunLoo}
            disabled={running}
            className="mt-4 px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
            style={{
              background: "transparent",
              color: "var(--color-muted)",
              border: "1px solid var(--color-rule)",
              cursor: "pointer",
            }}
          >
            Re-run LOO
          </button>
        </div>
      )}
    </main>
  );
}
