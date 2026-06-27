"use client";

import type { TimelineResponse, TimelineRow } from "@/lib/types";

/* ── Mini bar chart (SVG) ─────────────────────────────────────────────────── */

function BarChart({
  rows,
  valueKey,
  label,
  color = "var(--color-sage)",
}: {
  rows: TimelineRow[];
  valueKey: keyof TimelineRow;
  label: string;
  color?: string;
}) {
  const values = rows.map((r) => (r[valueKey] as number | null) ?? 0);
  const max = Math.max(...values, 1);
  const barW = 40;
  const gap = 12;
  const h = 120;
  const totalW = rows.length * (barW + gap);

  return (
    <div className="mb-8">
      <h3
        className="font-display font-semibold text-base mb-3"
        style={{ color: "var(--color-ink)" }}
      >
        {label}
      </h3>
      <div style={{ overflowX: "auto" }}>
        <svg width={totalW} height={h + 36} style={{ display: "block" }}>
          {rows.map((row, i) => {
            const val = (row[valueKey] as number | null) ?? 0;
            const barH = Math.round((val / max) * h);
            const x = i * (barW + gap);
            return (
              <g key={row.year}>
                <rect
                  x={x}
                  y={h - barH}
                  width={barW}
                  height={barH}
                  rx={4}
                  fill={color}
                  opacity={0.85}
                />
                <text
                  x={x + barW / 2}
                  y={h - barH - 4}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--color-muted)"
                >
                  {typeof val === "number" ? (Number.isInteger(val) ? val : val.toFixed(1)) : val}
                </text>
                <text
                  x={x + barW / 2}
                  y={h + 20}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--color-faint)"
                >
                  {row.year}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

/* ── Mini line chart (SVG) ────────────────────────────────────────────────── */

const CAT_COLORS: Record<string, string> = {
  Story:        "#4A7C59",
  Character:    "#D4A853",
  Aesthetics:   "#C07C5A",
  Theme:        "#7B8FA1",
  Worldbuilding:"#7BA87B",
};

function LineChart({ rows, categories }: { rows: TimelineRow[]; categories: string[] }) {
  if (rows.length < 2) return null;

  const w = Math.max(500, rows.length * 80);
  const h = 140;
  const padL = 36;
  const padR = 16;
  const padT = 12;
  const padB = 24;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const allVals = categories.flatMap((cat) =>
    rows.map((r) => (r[cat.toLowerCase() as keyof TimelineRow] as number | null) ?? null)
  ).filter((v): v is number => v !== null);
  const minV = Math.min(...allVals, 0);
  const maxV = Math.max(...allVals, 10);

  function xOf(i: number) {
    return padL + (i / (rows.length - 1)) * plotW;
  }
  function yOf(v: number) {
    return padT + ((maxV - v) / (maxV - minV)) * plotH;
  }

  return (
    <div className="mb-8">
      <h3
        className="font-display font-semibold text-base mb-3"
        style={{ color: "var(--color-ink)" }}
      >
        Category averages per year
      </h3>
      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-3">
        {categories.map((cat) => (
          <div key={cat} className="flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-3 rounded-full"
              style={{ background: CAT_COLORS[cat] ?? "#888" }}
            />
            <span className="text-xs" style={{ color: "var(--color-muted)" }}>{cat}</span>
          </div>
        ))}
      </div>
      <div style={{ overflowX: "auto" }}>
        <svg width={w} height={h} style={{ display: "block" }}>
          {/* Y grid lines */}
          {[...Array(5)].map((_, i) => {
            const v = minV + ((maxV - minV) * i) / 4;
            const y = yOf(v);
            return (
              <g key={i}>
                <line x1={padL} y1={y} x2={w - padR} y2={y} stroke="var(--color-rule)" strokeWidth={1} />
                <text x={padL - 4} y={y + 4} textAnchor="end" fontSize={9} fill="var(--color-faint)">
                  {v.toFixed(1)}
                </text>
              </g>
            );
          })}

          {/* Category lines */}
          {categories.map((cat) => {
            const key = cat.toLowerCase() as keyof TimelineRow;
            const pts = rows
              .map((r, i) => {
                const v = r[key] as number | null;
                return v != null ? `${xOf(i)},${yOf(v)}` : null;
              })
              .filter(Boolean);
            if (pts.length < 2) return null;
            return (
              <polyline
                key={cat}
                points={pts.join(" ")}
                fill="none"
                stroke={CAT_COLORS[cat] ?? "#888"}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            );
          })}

          {/* X labels */}
          {rows.map((r, i) => (
            <text
              key={r.year}
              x={xOf(i)}
              y={h - 4}
              textAnchor="middle"
              fontSize={10}
              fill="var(--color-faint)"
            >
              {r.year}
            </text>
          ))}
        </svg>
      </div>
    </div>
  );
}

/* ── Table view ───────────────────────────────────────────────────────────── */

function TimelineTable({ rows, categories }: { rows: TimelineRow[]; categories: string[] }) {
  const headers = ["Year", "Books", "Avg WA", ...categories, "Avg Words"];
  return (
    <div className="mb-8 rounded-xl overflow-hidden" style={{ border: "1px solid var(--color-rule)" }}>
      <div style={{ overflowX: "auto" }}>
        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: "var(--color-surface-2)" }}>
              {headers.map((h) => (
                <th
                  key={h}
                  className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider whitespace-nowrap"
                  style={{ color: "var(--color-muted)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const catVals = categories.map((cat) => {
                const v = r[cat.toLowerCase() as keyof TimelineRow] as number | null;
                return v?.toFixed(2) ?? "—";
              });
              const avgWords = r.avg_words != null
                ? r.avg_words >= 1_000_000
                  ? `${(r.avg_words / 1_000_000).toFixed(1)}M`
                  : r.avg_words >= 1_000
                  ? `${Math.round(r.avg_words / 1_000)}K`
                  : `${r.avg_words}`
                : "—";
              return (
                <tr key={r.year} style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}>
                  <td className="px-3 py-2.5 font-semibold" style={{ color: "var(--color-ink)" }}>{r.year}</td>
                  <td className="px-3 py-2.5" style={{ color: "var(--color-ink)" }}>{r.books}</td>
                  <td className="px-3 py-2.5" style={{ color: "var(--color-sage)", fontWeight: 600 }}>{r.avg_wa?.toFixed(2) ?? "—"}</td>
                  {catVals.map((v, j) => (
                    <td key={j} className="px-3 py-2.5" style={{ color: "var(--color-muted)" }}>{v}</td>
                  ))}
                  <td className="px-3 py-2.5" style={{ color: "var(--color-muted)" }}>{avgWords}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────────────────────── */

export default function TimelineClient({ data }: { data: TimelineResponse }) {
  if (data.rows.length === 0) {
    return (
      <div>
        <h1 className="font-display text-3xl font-bold mb-2" style={{ color: "var(--color-ink)" }}>
          Timeline
        </h1>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          No books have a year_read set yet.
        </p>
      </div>
    );
  }

  const totalBooks = data.rows.reduce((s, r) => s + r.books, 0);

  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Timeline
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {totalBooks} books across {data.rows.length} year{data.rows.length !== 1 ? "s" : ""} · how your reading and rating shift year to year
        </p>
      </div>

      <TimelineTable rows={data.rows} categories={data.categories} />
      <BarChart rows={data.rows} valueKey="books" label="Books per year" />
      <BarChart rows={data.rows} valueKey="avg_wa" label="Average WA per year" color="var(--color-sage)" />
      <LineChart rows={data.rows} categories={data.categories} />
    </div>
  );
}
