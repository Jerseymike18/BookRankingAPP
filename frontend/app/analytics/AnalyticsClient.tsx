"use client";

import { useMemo, useRef, useState } from "react";
import type { Book } from "@/lib/types";
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";
import {
  headerStats,
  libraryMeanWA,
  tasteFingerprint,
  discriminationProfile,
  categoryRadar,
  genreAffinity,
  genresByVolume,
  authorLeaderboard,
  lengthSweetSpot,
  coMovement,
  type CorrRow,
  type SdRow,
  type RadarPoint,
  type GenreAffinity,
  type AuthorStat,
  type LengthSweetSpot,
  type CoMovement,
} from "@/lib/analytics";

/* ── Small formatters (match existing pages) ───────────────────────────────── */

const f2 = (v: number) => v.toFixed(2);

function fmtWords(w: number | null | undefined) {
  if (!w) return "—";
  if (w >= 1_000_000) return `${(w / 1_000_000).toFixed(1)}M`;
  if (w >= 1_000) return `${Math.round(w / 1_000)}K`;
  return `${w}`;
}

/* ── Shared primitives (StatCard / SectionHeading from StatsClient) ─────────── */

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-1"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
        {label}
      </span>
      <span className="font-display text-2xl font-bold" style={{ color: "var(--color-ink)" }}>
        {value}
      </span>
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-display text-lg font-semibold mt-10 mb-1" style={{ color: "var(--color-ink)" }}>
      {children}
    </h3>
  );
}

function Caption({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
      {children}
    </p>
  );
}

function Panel({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div
      className="rounded-xl p-4"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      role={label ? "img" : undefined}
      aria-label={label}
    >
      {children}
    </div>
  );
}

function NotEnough({ n }: { n: number }) {
  return (
    <div
      className="rounded-xl p-4 text-sm"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)", color: "var(--color-muted)" }}
    >
      Not enough data for correlations — this slice has {n} book{n !== 1 ? "s" : ""} (need at least 4).
    </div>
  );
}

/* ── Tooltip (plain absolutely-positioned div, no library) ─────────────────── */

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
  const hide = () => setTip(null);
  return { ref, tip, show, hide };
}

function TipBox({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div
      style={{
        position: "absolute",
        top: tip.y + 14,
        left: tip.x + (tip.flip ? -14 : 14),
        transform: tip.flip ? "translateX(-100%)" : "none",
        pointerEvents: "none",
        background: "var(--color-ink)",
        color: "#fff",
        padding: "5px 8px",
        borderRadius: 6,
        fontSize: 11,
        lineHeight: 1.35,
        whiteSpace: "nowrap",
        zIndex: 30,
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      {tip.lines.map((l, i) => (
        <div key={i} style={{ fontVariantNumeric: "tabular-nums" }}>{l}</div>
      ))}
    </div>
  );
}

/* ── 1 & 2. Horizontal bar ranking (fingerprint / discrimination) ──────────── */

function HBarRanking({
  rows,
  color,
  format,
}: {
  rows: { label: string; value: number | null }[];
  color: string;
  format: (v: number) => string;
}) {
  const W = 720, rowH = 30, padT = 6, labelW = 178, valW = 52;
  const trackX = labelW, trackW = W - labelW - valW;
  const H = padT * 2 + rows.length * rowH;

  const vals = rows.map((r) => r.value).filter((v): v is number => v != null);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0.0001, ...vals);
  const xOf = (v: number) => trackX + ((v - lo) / (hi - lo)) * trackW;
  const x0 = xOf(0);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img">
      {rows.map((r, i) => {
        const cy = padT + i * rowH + rowH / 2;
        const barY = padT + i * rowH + 6;
        const barH = rowH - 12;
        const has = r.value != null;
        const xv = has ? xOf(r.value as number) : x0;
        const x1 = Math.min(x0, xv);
        const x2 = Math.max(x0, xv);
        const neg = has && (r.value as number) < 0;
        return (
          <g key={r.label}>
            <text x={labelW - 10} y={cy} textAnchor="end" dominantBaseline="middle" fontSize={11.5} fill="var(--color-ink)">
              {r.label}
            </text>
            {has && (
              <rect
                x={x1}
                y={barY}
                width={Math.max(1.5, x2 - x1)}
                height={barH}
                rx={3}
                fill={neg ? "var(--color-spine-c)" : color}
                opacity={0.9}
              />
            )}
            <text
              x={W - 8}
              y={cy}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={11.5}
              fontWeight={600}
              fill="var(--color-muted)"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {has ? format(r.value as number) : "—"}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ── 3. Category radar (pentagon) ──────────────────────────────────────────── */

function RadarChart({ points }: { points: RadarPoint[] }) {
  const size = 360, cx = 180, cy = 176, R = 116, MAX = 10;
  const n = points.length;
  const ang = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pt = (i: number, radius: number): [number, number] => [
    cx + radius * Math.cos(ang(i)),
    cy + radius * Math.sin(ang(i)),
  ];
  const ringPoly = (frac: number) =>
    points.map((_, i) => pt(i, R * frac).join(",")).join(" ");
  const dataPoly = points
    .map((p, i) => pt(i, p.mean != null ? R * (Math.max(0, Math.min(MAX, p.mean)) / MAX) : 0).join(","))
    .join(" ");
  const rings = [2, 4, 6, 8, 10];

  return (
    <svg viewBox={`0 0 ${size} ${size - 20}`} style={{ width: "100%", maxWidth: 460, height: "auto", display: "block", margin: "0 auto" }} role="img" aria-label="Category radar">
      {/* grid rings */}
      {rings.map((r) => (
        <polygon key={r} points={ringPoly(r / MAX)} fill="none" stroke="var(--color-rule)" strokeWidth={1} />
      ))}
      {/* spokes */}
      {points.map((_, i) => {
        const [x, y] = pt(i, R);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--color-rule)" strokeWidth={1} />;
      })}
      {/* data polygon */}
      <polygon points={dataPoly} fill="var(--color-sage)" fillOpacity={0.22} stroke="var(--color-sage)" strokeWidth={2} strokeLinejoin="round" />
      {points.map((p, i) => {
        const [x, y] = pt(i, p.mean != null ? R * (Math.max(0, Math.min(MAX, p.mean)) / MAX) : 0);
        return <circle key={i} cx={x} cy={y} r={3} fill="var(--color-sage)" />;
      })}
      {/* axis labels */}
      {points.map((p, i) => {
        const [lx, ly] = pt(i, R + 20);
        const c = Math.cos(ang(i));
        const anchor = c > 0.25 ? "start" : c < -0.25 ? "end" : "middle";
        return (
          <g key={i}>
            <text x={lx} y={ly} textAnchor={anchor} dominantBaseline="middle" fontSize={11.5} fontWeight={600} fill="var(--color-ink)">
              {p.category}
            </text>
            <text x={lx} y={ly + 13} textAnchor={anchor} dominantBaseline="middle" fontSize={11} fill="var(--color-sage)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {p.mean != null ? f2(p.mean) : "—"}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ── 4. Genre affinity scatter ─────────────────────────────────────────────── */

function GenreScatter({ genres, meanWA }: { genres: GenreAffinity[]; meanWA: number | null }) {
  const { ref, tip, show, hide } = useChartTip();
  const W = 720, H = 380, padL = 46, padR = 20, padT = 18, padB = 40;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const maxCount = Math.max(1, ...genres.map((g) => g.count));
  const was = genres.map((g) => g.avgWA);
  const yMin = Math.max(0, Math.floor((Math.min(...was) - 0.4) * 2) / 2);
  const yMax = Math.min(10, Math.ceil((Math.max(...was) + 0.4) * 2) / 2);
  const xOf = (c: number) => padL + (c / (maxCount * 1.08)) * plotW;
  const yOf = (w: number) => padT + ((yMax - w) / (yMax - yMin || 1)) * plotH;

  const cMin = Math.min(...genres.map((g) => g.count));
  const rOf = (c: number) => {
    if (maxCount === cMin) return 14;
    const t = (Math.sqrt(c) - Math.sqrt(cMin)) / (Math.sqrt(maxCount) - Math.sqrt(cMin));
    return 7 + t * 19;
  };

  const xTicks = Array.from({ length: 5 }, (_, i) => Math.round((maxCount * 1.08 * i) / 4));
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4);

  return (
    <div ref={ref} style={{ position: "relative" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label="Genre affinity scatter">
        {/* y grid + labels */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} y1={yOf(v)} x2={W - padR} y2={yOf(v)} stroke="var(--color-rule)" strokeWidth={1} />
            <text x={padL - 6} y={yOf(v) + 3.5} textAnchor="end" fontSize={10} fill="var(--color-faint)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {v.toFixed(1)}
            </text>
          </g>
        ))}
        {/* x ticks */}
        {xTicks.map((v, i) => (
          <text key={i} x={xOf(v)} y={H - padB + 16} textAnchor="middle" fontSize={10} fill="var(--color-faint)">
            {v}
          </text>
        ))}
        <text x={padL + plotW / 2} y={H - 4} textAnchor="middle" fontSize={11} fill="var(--color-muted)">
          books in genre →
        </text>
        {/* library mean WA rule */}
        {meanWA != null && meanWA >= yMin && meanWA <= yMax && (
          <g>
            <line x1={padL} y1={yOf(meanWA)} x2={W - padR} y2={yOf(meanWA)} stroke="var(--color-sage)" strokeWidth={1} strokeDasharray="4 4" opacity={0.6} />
            <text x={W - padR} y={yOf(meanWA) - 4} textAnchor="end" fontSize={10} fill="var(--color-sage)" style={{ fontVariantNumeric: "tabular-nums" }}>
              avg WA {f2(meanWA)}
            </text>
          </g>
        )}
        {/* bubbles */}
        {genres.map((g) => {
          const x = xOf(g.count), y = yOf(g.avgWA), r = rOf(g.count);
          const labelLeft = x > W * 0.62;
          return (
            <g key={g.genre}>
              <circle
                cx={x}
                cy={y}
                r={r}
                fill="var(--color-sage)"
                fillOpacity={0.32}
                stroke="var(--color-sage)"
                strokeWidth={1.5}
                onMouseMove={(e) => show(e, [g.genre, `${g.count} books`, `avg WA ${f2(g.avgWA)}`])}
              />
              <text
                x={labelLeft ? x - r - 5 : x + r + 5}
                y={y + 3.5}
                textAnchor={labelLeft ? "end" : "start"}
                fontSize={10.5}
                fill="var(--color-muted)"
              >
                {g.genre}
              </text>
            </g>
          );
        })}
      </svg>
      <TipBox tip={tip} />
    </div>
  );
}

/* ── 6. Length sweet-spot scatter ──────────────────────────────────────────── */

function LengthScatter({ data }: { data: LengthSweetSpot }) {
  const { ref, tip, show, hide } = useChartTip();
  const { points, bins } = data;
  const W = 720, H = 380, padL = 46, padR = 18, padT = 18, padB = 40;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const words = points.map((p) => p.words);
  const wMin = Math.min(...words), wMax = Math.max(...words);
  const lgMin = Math.log(wMin), lgMax = Math.log(wMax);
  const xOf = (w: number) => (lgMax === lgMin ? padL + plotW / 2 : padL + ((Math.log(w) - lgMin) / (lgMax - lgMin)) * plotW);

  const was = points.map((p) => p.wa);
  const yMin = Math.max(0, Math.floor((Math.min(...was) - 0.3) * 2) / 2);
  const yMax = Math.min(10, Math.ceil((Math.max(...was) + 0.3) * 2) / 2);
  const yOf = (w: number) => padT + ((yMax - w) / (yMax - yMin || 1)) * plotH;

  const xTicks = Array.from({ length: 5 }, (_, i) => Math.exp(lgMin + ((lgMax - lgMin) * i) / 4));
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4);

  // Step line through quartile means (contiguous ranges read as steps).
  const stepPts = bins.flatMap((b) => [`${xOf(b.loWords)},${yOf(b.avgWA)}`, `${xOf(b.hiWords)},${yOf(b.avgWA)}`]);

  return (
    <div ref={ref} style={{ position: "relative" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label="WA versus word count scatter">
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} y1={yOf(v)} x2={W - padR} y2={yOf(v)} stroke="var(--color-rule)" strokeWidth={1} />
            <text x={padL - 6} y={yOf(v) + 3.5} textAnchor="end" fontSize={10} fill="var(--color-faint)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {v.toFixed(1)}
            </text>
          </g>
        ))}
        {xTicks.map((v, i) => (
          <text key={i} x={xOf(v)} y={H - padB + 16} textAnchor="middle" fontSize={10} fill="var(--color-faint)">
            {fmtWords(Math.round(v))}
          </text>
        ))}
        <text x={padL + plotW / 2} y={H - 4} textAnchor="middle" fontSize={11} fill="var(--color-muted)">
          word count (log) →
        </text>
        {/* points */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={xOf(p.words)}
            cy={yOf(p.wa)}
            r={3.5}
            fill="var(--color-sage)"
            fillOpacity={0.45}
            onMouseMove={(e) => show(e, [p.title, `${fmtWords(p.words)} words`, `WA ${f2(p.wa)}`])}
          />
        ))}
        {/* quartile trend */}
        {stepPts.length > 0 && (
          <polyline points={stepPts.join(" ")} fill="none" stroke="var(--color-sage)" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        )}
        {bins.map((b, i) => {
          const mx = (xOf(b.loWords) + xOf(b.hiWords)) / 2;
          return (
            <text key={i} x={mx} y={yOf(b.avgWA) - 8} textAnchor="middle" fontSize={10} fontWeight={600} fill="var(--color-sage)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {f2(b.avgWA)}
            </text>
          );
        })}
      </svg>
      <TipBox tip={tip} />
    </div>
  );
}

/* ── 7. Component co-movement heatmap ──────────────────────────────────────── */

const SURFACE2 = "#F1EDE5", SAGE = "#4A7C59", SPINEC = "#C07C5A";
function mixHex(a: string, b: string, t: number) {
  const pa = [parseInt(a.slice(1, 3), 16), parseInt(a.slice(3, 5), 16), parseInt(a.slice(5, 7), 16)];
  const pb = [parseInt(b.slice(1, 3), 16), parseInt(b.slice(3, 5), 16), parseInt(b.slice(5, 7), 16)];
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function cellColor(r: number | null) {
  if (r == null) return "var(--color-surface-2)";
  const t = Math.min(1, Math.abs(r));
  return r >= 0 ? mixHex(SURFACE2, SAGE, t) : mixHex(SURFACE2, SPINEC, t);
}

function Heatmap({ data }: { data: CoMovement }) {
  const { ref, tip, show, hide } = useChartTip();
  const { labels, matrix } = data;
  const N = labels.length;
  const cell = 22, gutterL = 46, gutterT = 48, pad = 8;
  const W = gutterL + N * cell + pad;
  const H = gutterT + N * cell + pad;

  return (
    <div ref={ref} style={{ position: "relative" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label="Component co-movement heatmap">
        {/* top labels (angled) */}
        {labels.map((c, j) => (
          <text
            key={`t${j}`}
            x={gutterL + j * cell + cell / 2}
            y={gutterT - 6}
            fontSize={9}
            fill="var(--color-muted)"
            textAnchor="start"
            transform={`rotate(-45 ${gutterL + j * cell + cell / 2} ${gutterT - 6})`}
          >
            {c.abbr}
          </text>
        ))}
        {/* left labels */}
        {labels.map((c, i) => (
          <text key={`l${i}`} x={gutterL - 6} y={gutterT + i * cell + cell / 2 + 3} fontSize={9} fill="var(--color-muted)" textAnchor="end">
            {c.abbr}
          </text>
        ))}
        {/* cells */}
        {matrix.map((row, i) =>
          row.map((r, j) => (
            <rect
              key={`${i}-${j}`}
              x={gutterL + j * cell}
              y={gutterT + i * cell}
              width={cell - 1}
              height={cell - 1}
              fill={cellColor(r)}
              onMouseMove={(e) =>
                show(e, [
                  `${labels[i].name} × ${labels[j].name}`,
                  i === j ? "self" : r != null ? `r = ${f2(r)}` : "r = —",
                ])
              }
            />
          ))
        )}
      </svg>
      {/* legend */}
      <div className="flex items-center gap-2 mt-3 text-xs" style={{ color: "var(--color-muted)" }}>
        <span>−1</span>
        <span
          style={{
            display: "inline-block",
            width: 140,
            height: 10,
            borderRadius: 3,
            background: `linear-gradient(to right, ${SPINEC}, ${SURFACE2}, ${SAGE})`,
          }}
        />
        <span>+1</span>
        <span className="ml-1" style={{ color: "var(--color-faint)" }}>terracotta = move apart · sage = move together</span>
      </div>
      <TipBox tip={tip} />
    </div>
  );
}

/* ── 5. Author leaderboard columns ─────────────────────────────────────────── */

const AUTHOR_COLS: ColDef<AuthorStat>[] = [
  { key: "rank", label: "#", type: "numeric", getValue: () => 0, align: "left", autoRank: true, sortable: false },
  { key: "author", label: "Author", type: "string", getValue: (r) => r.author, align: "left" },
  { key: "books", label: "Books", type: "numeric", getValue: (r) => r.books },
  { key: "avgWA", label: "Avg WA", type: "numeric", getValue: (r) => r.avgWA, formatter: (v) => (v != null ? Number(v).toFixed(2) : "—") },
  {
    key: "favorite",
    label: "Favorite",
    type: "numeric",
    getValue: (r) => r.favoriteScore,
    defaultDir: "desc", // best work first
    formatter: (v) => (v != null ? Number(v).toFixed(2) : "—"),
  },
  {
    key: "consistency",
    label: "Consistency (σ)",
    type: "numeric",
    getValue: (r) => r.consistency,
    defaultDir: "asc", // lower spread = more reliable
    formatter: (v) => (v != null ? Number(v).toFixed(2) : "—"),
  },
];

/* ── Genre filter chip ─────────────────────────────────────────────────────── */

function Chip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="genre-chip"
      style={{
        cursor: "pointer",
        border: "1px solid transparent",
        background: active ? "var(--color-sage)" : "var(--color-sage-light)",
        color: active ? "#fff" : "var(--color-sage)",
      }}
    >
      {label}
    </button>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────────── */

export default function AnalyticsClient({ books }: { books: Book[] }) {
  const [genre, setGenre] = useState<string | null>(null);
  const allGenres = useMemo(() => genresByVolume(books), [books]);

  const filtered = useMemo(
    () => (genre ? books.filter((b) => b.genre === genre) : books),
    [books, genre]
  );
  const enoughForCorr = filtered.length >= 4;

  const header = useMemo(() => headerStats(filtered), [filtered]);
  const meanWA = useMemo(() => libraryMeanWA(filtered), [filtered]);
  const fingerprint: CorrRow[] = useMemo(() => tasteFingerprint(filtered), [filtered]);
  const discrimination: SdRow[] = useMemo(() => discriminationProfile(filtered), [filtered]);
  const radar = useMemo(() => categoryRadar(filtered), [filtered]);
  const genres = useMemo(() => genreAffinity(filtered), [filtered]);
  const authors = useMemo(() => authorLeaderboard(filtered), [filtered]);
  const lengths: LengthSweetSpot = useMemo(() => lengthSweetSpot(filtered), [filtered]);
  const heat: CoMovement = useMemo(() => coMovement(filtered), [filtered]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Taste Lab
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          What your ratings reveal about what you actually love.
        </p>
      </div>

      {/* Genre filter */}
      <div className="flex flex-wrap gap-2 mb-6">
        <Chip label="All" active={genre === null} onClick={() => setGenre(null)} />
        {allGenres.map((g) => (
          <Chip key={g} label={g} active={genre === g} onClick={() => setGenre(g)} />
        ))}
      </div>

      {/* Header band */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Rated books" value={`${header.books}`} />
        <StatCard label="Authors" value={`${header.authors}`} />
        <StatCard label="Genres" value={`${header.genres}`} />
        <StatCard label="Avg WA" value={header.avgWA != null ? f2(header.avgWA) : "—"} />
      </div>

      {/* 1. Fingerprint */}
      <SectionHeading>What drives a favorite</SectionHeading>
      <Caption>
        Pearson correlation between each component and a book&apos;s overall WA. Longer bars are the
        qualities that most reliably separate your favorites from the rest.
      </Caption>
      {enoughForCorr ? (
        <Panel label="Component correlation with WA">
          <HBarRanking rows={fingerprint.map((r) => ({ label: r.comp.name, value: r.r }))} color="var(--color-sage)" format={f2} />
        </Panel>
      ) : (
        <NotEnough n={filtered.length} />
      )}

      {/* 2. Discrimination */}
      <SectionHeading>Where you rate sharply</SectionHeading>
      <Caption>
        Standard deviation of each component across the library. Tall bars are where your scores
        swing widely; short bars are where you rate almost everything the same.
      </Caption>
      {enoughForCorr ? (
        <>
          <Panel label="Component standard deviation">
            <HBarRanking rows={discrimination.map((r) => ({ label: r.comp.name, value: r.sd }))} color="var(--color-spine-d)" format={f2} />
          </Panel>
          <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
            The three Worldbuilding components (Depth2 / Integration / Originality) sit high partly
            because Worldbuilding is only scored for some genres — so their spread is genre-mix, not
            just opinion.
          </p>
        </>
      ) : (
        <NotEnough n={filtered.length} />
      )}

      {/* 3. Radar */}
      <SectionHeading>Your taste in five dimensions</SectionHeading>
      <Caption>
        Mean weighted score per category. Worldbuilding is averaged over only the books that
        actually scored it, so realist reads don&apos;t drag it down.
      </Caption>
      <Panel>
        <RadarChart points={radar} />
      </Panel>

      {/* 4. Genre affinity */}
      <SectionHeading>Genre affinity</SectionHeading>
      <Caption>
        Each bubble is a genre: how much you read it (x, and bubble size) against how highly you
        rate it (y). The dashed line is your library-wide average WA.
      </Caption>
      <Panel>
        <GenreScatter genres={genres} meanWA={meanWA} />
      </Panel>

      {/* 5. Author leaderboard */}
      <SectionHeading>Authors: favorites &amp; reliability</SectionHeading>
      <Caption>
        Favorite score weights an author&apos;s best work far above their weakest, so one dud
        doesn&apos;t sink a great catalog. Consistency (σ) is the spread of their ratings — lower is
        steadier. Shown with at least 2 books.
      </Caption>
      <SortableTable
        columns={AUTHOR_COLS}
        data={authors}
        defaultSort={{ key: "favorite", dir: "desc" }}
        getRowKey={(r) => r.author}
        emptyMessage="No authors in this slice."
      />

      {/* 6. Length sweet-spot */}
      <SectionHeading>Do you reward length?</SectionHeading>
      <Caption>
        Each dot is a book: word count (x, log scale) against WA (y). The sage steps are your
        average WA per word-count quartile. Books with no word count are omitted here.
      </Caption>
      {lengths.points.length > 0 ? (
        <Panel>
          <LengthScatter data={lengths} />
        </Panel>
      ) : (
        <div className="rounded-xl p-4 text-sm" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)", color: "var(--color-muted)" }}>
          No books in this slice have a word count.
        </div>
      )}

      {/* 7. Co-movement */}
      <SectionHeading>What travels together</SectionHeading>
      <Caption>
        Pairwise correlation between components. Sage cells are qualities that rise together;
        terracotta cells are where one rises as the other falls. Hover any cell for the exact r.
      </Caption>
      {enoughForCorr ? (
        <Panel>
          <Heatmap data={heat} />
        </Panel>
      ) : (
        <NotEnough n={filtered.length} />
      )}
    </div>
  );
}
