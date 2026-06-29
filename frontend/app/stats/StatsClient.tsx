"use client";

import { useState, useMemo } from "react";
import type { CombinedStatsResponse, CombinedRankRow, TypeSummary, BookKind } from "@/lib/types";
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";

// Tier spine colours (the existing design tokens, shared with TierLadder).
const TIER_COLORS: Record<string, string> = {
  "S+": "#2D6A4F", S: "#4A7C59", A: "#7BA87B", B: "#D4A853",
  C: "#C07C5A", D: "#7B8FA1", F: "#C4B8AD",
};

function fmtWords(w: number | null) {
  if (!w) return "—";
  if (w >= 1_000_000) return `${(w / 1_000_000).toFixed(1)}M`;
  if (w >= 1_000) return `${Math.round(w / 1_000)}K`;
  return `${w}`;
}

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
    <h3 className="font-display text-lg font-semibold mt-10 mb-3" style={{ color: "var(--color-ink)" }}>
      {children}
    </h3>
  );
}

function TypeCard({ title, sub, s }: { title: string; sub: string; s: TypeSummary }) {
  return (
    <div className="rounded-xl p-5" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
      <div className="flex items-baseline justify-between mb-3">
        <span className="font-display text-lg font-bold" style={{ color: "var(--color-ink)" }}>{title}</span>
        <span className="text-xs" style={{ color: "var(--color-faint)" }}>{sub}</span>
      </div>
      <div className="grid grid-cols-2 gap-y-2 text-sm">
        <span style={{ color: "var(--color-muted)" }}>Books</span>
        <span className="text-right font-semibold" style={{ color: "var(--color-ink)" }}>{s.books}</span>
        <span style={{ color: "var(--color-muted)" }}>Avg WA</span>
        <span className="text-right" style={{ color: "var(--color-ink)", fontVariantNumeric: "tabular-nums" }}>{s.avg_wa != null ? s.avg_wa.toFixed(2) : "—"}</span>
        <span style={{ color: "var(--color-muted)" }}>Avg Total Average</span>
        <span className="text-right font-semibold" style={{ color: "var(--color-sage)", fontVariantNumeric: "tabular-nums" }}>{s.avg_total_average != null ? s.avg_total_average.toFixed(2) : "—"}</span>
        <span style={{ color: "var(--color-muted)" }}>Words</span>
        <span className="text-right" style={{ color: "var(--color-ink)" }}>{fmtWords(s.total_words)}</span>
      </div>
    </div>
  );
}

// A compact tier-count row coloured by the tier tokens (no new chart style).
function TierRow({ label, counts, order }: { label: string; counts: Record<string, number>; order: string[] }) {
  return (
    <div className="flex items-center gap-3 flex-wrap mb-1.5">
      <span className="text-xs font-semibold w-20" style={{ color: "var(--color-muted)" }}>{label}</span>
      {order.filter((t) => (counts[t] ?? 0) > 0).map((t) => (
        <span key={t} className="text-xs font-medium px-2 py-0.5 rounded" style={{ background: TIER_COLORS[t], color: "#fff" }}>
          {t} {counts[t]}
        </span>
      ))}
    </div>
  );
}

const TYPE_TABS: { id: "all" | BookKind; label: string }[] = [
  { id: "all", label: "All" },
  { id: "fiction", label: "Fiction" },
  { id: "nonfiction", label: "Nonfiction" },
];

const RANK_COLS: ColDef<CombinedRankRow>[] = [
  { key: "rank", label: "#", type: "numeric", getValue: (r) => r.rank, align: "left" },
  { key: "title", label: "Book", type: "string", getValue: (r) => r.title, align: "left" },
  { key: "author", label: "Author", type: "string", getValue: (r) => r.author, align: "left" },
  { key: "type", label: "Type", type: "string", getValue: (r) => r.type, align: "left",
    formatter: (v) => (v === "nonfiction" ? "Nonfiction" : "Fiction") },
  { key: "total_average", label: "Total Avg", type: "numeric", getValue: (r) => r.total_average ?? 0,
    formatter: (v) => (v != null ? Number(v).toFixed(2) : "—") },
  { key: "wa", label: "WA", type: "numeric", getValue: (r) => r.wa ?? 0,
    formatter: (v) => (v != null && v !== 0 ? Number(v).toFixed(2) : "—") },
];

export default function StatsClient({ data }: { data: CombinedStatsResponse }) {
  const { totals, by_type, tier_distribution, per_year, combined_ranking } = data;
  const [typeFilter, setTypeFilter] = useState<"all" | BookKind>("all");

  const ranking = useMemo(
    () => (typeFilter === "all" ? combined_ranking : combined_ranking.filter((r) => r.type === typeFilter)),
    [combined_ranking, typeFilter]
  );

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Stats
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          Fiction + Nonfiction combined · {totals.total_books} books
        </p>
      </div>

      {/* Top-line totals */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatCard label="Total books" value={`${totals.total_books}`} />
        <StatCard label="Fiction" value={`${totals.fiction_books}`} />
        <StatCard label="Nonfiction" value={`${totals.nonfiction_books}`} />
        <StatCard label="Total words" value={fmtWords(totals.total_words)} />
        <StatCard label="Avg Total Avg" value={totals.avg_total_average != null ? totals.avg_total_average.toFixed(2) : "—"} />
      </div>

      {/* Per-type comparison */}
      <SectionHeading>By type</SectionHeading>
      <div className="grid sm:grid-cols-2 gap-4">
        <TypeCard title="Fiction" sub="WA-ranked · 5 categories" s={by_type.fiction} />
        <TypeCard title="Nonfiction" sub="Total-Average-ranked · 3 categories" s={by_type.nonfiction} />
      </div>

      {/* Tier distribution (per type — bases differ) */}
      <SectionHeading>Tier distribution</SectionHeading>
      <div className="rounded-xl p-4" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
        <TierRow label="Fiction" counts={tier_distribution.fiction} order={tier_distribution.tier_order} />
        <TierRow label="Nonfiction" counts={tier_distribution.nonfiction} order={tier_distribution.tier_order} />
        <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
          Shown per type because the bases differ: fiction tiers are banded by WA, nonfiction by Total Average.
        </p>
      </div>

      {/* Combined ranking by Total Average */}
      <SectionHeading>All books · ranked by Total Average</SectionHeading>
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        Total Average (the unweighted mean of category averages) is on the same 0–10 scale for both
        types, so this cross-type ranking is by Total Average — not WA, whose formula and scale differ
        between fiction and nonfiction.
      </p>
      <div className="flex gap-1 mb-4 p-1 rounded-xl inline-flex" style={{ background: "var(--color-surface-2)" }}>
        {TYPE_TABS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTypeFilter(id)}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: typeFilter === id ? "var(--color-surface)" : "transparent",
              color: typeFilter === id ? "var(--color-sage)" : "var(--color-muted)",
              boxShadow: typeFilter === id ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <SortableTable
        columns={RANK_COLS}
        data={ranking}
        defaultSort={{ key: "total_average", dir: "desc" }}
        getRowKey={(r) => `${r.type}:${r.title}`}
      />

      {/* Books per year (combined) */}
      {per_year.length > 0 && (
        <>
          <SectionHeading>Books per year</SectionHeading>
          <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--color-rule)" }}>
            <table className="w-full text-sm">
              <thead>
                <tr style={{ background: "var(--color-surface-2)" }}>
                  {["Year", "Fiction", "Nonfiction", "Total"].map((h) => (
                    <th key={h} className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {per_year.map((r, i) => (
                  <tr key={r.year} style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}>
                    <td className="px-3 py-2.5 font-semibold" style={{ color: "var(--color-ink)" }}>{r.year}</td>
                    <td className="px-3 py-2.5" style={{ color: "var(--color-muted)" }}>{r.fiction}</td>
                    <td className="px-3 py-2.5" style={{ color: "var(--color-muted)" }}>{r.nonfiction}</td>
                    <td className="px-3 py-2.5 font-semibold" style={{ color: "var(--color-ink)" }}>{r.books}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
