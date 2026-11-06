"use client";

import { useState, useMemo } from "react";
import type { CombinedStatsResponse, CombinedRankRow, TypeSummary, BookKind } from "@/lib/types";
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";
import { SegmentedTabs } from "@/components/SegmentedTabs";

// Tier spine colours (the existing design tokens, shared with TierLadder).
const TIER_COLORS: Record<string, string> = {
  "S+": "var(--color-spine-sp)", S: "var(--color-sage)", A: "var(--color-spine-a)", B: "var(--color-spine-b)",
  C: "var(--color-spine-c)", D: "var(--color-spine-d)", F: "var(--color-spine-f)",
};

function fmtWords(w: number | null) {
  if (!w) return "—";
  if (w >= 1_000_000) return `${(w / 1_000_000).toFixed(1)}M`;
  if (w >= 1_000) return `${Math.round(w / 1_000)}K`;
  return `${w}`;
}

const FICTION_COLOR = "var(--color-sage)";
const NONFICTION_COLOR = "var(--color-spine-d)";

const tileStyle = { background: "var(--color-surface)", boxShadow: "var(--shadow-card)" } as const;

/** A dashboard tile: small label, big number, optional footnote/visual. */
function StatTile({ label, value, foot, children }: {
  label: string;
  value: string;
  foot?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl p-4 flex flex-col gap-1 min-w-0" style={tileStyle}>
      <span className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>{label}</span>
      <span className="font-display text-3xl font-bold leading-none tabular-nums" style={{ color: "var(--color-ink)" }}>
        {value}
      </span>
      {foot && <span className="text-xs mt-1" style={{ color: "var(--color-faint)" }}>{foot}</span>}
      {children}
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-display text-lg font-semibold mt-8 mb-3" style={{ color: "var(--color-ink)" }}>
      {children}
    </h3>
  );
}

function Legend() {
  return (
    <div className="flex gap-4 text-xs" style={{ color: "var(--color-muted)" }}>
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: FICTION_COLOR }} /> Fiction
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: NONFICTION_COLOR }} /> Nonfiction
      </span>
    </div>
  );
}

/** Fiction : nonfiction as one split bar. */
function SplitBar({ fiction, nonfiction }: { fiction: number; nonfiction: number }) {
  const total = fiction + nonfiction || 1;
  return (
    <div className="flex h-2 rounded-full overflow-hidden mt-2" style={{ background: "var(--color-surface-2)" }}>
      <div style={{ width: `${(fiction / total) * 100}%`, background: FICTION_COLOR }} />
      <div style={{ width: `${(nonfiction / total) * 100}%`, background: NONFICTION_COLOR }} />
    </div>
  );
}

/** One track's tier counts as a stacked bar in the tier spine colours, with
 *  the counts listed underneath. */
function TierBar({ label, counts, order }: { label: string; counts: Record<string, number>; order: string[] }) {
  const tiers = order.filter((t) => (counts[t] ?? 0) > 0);
  const total = tiers.reduce((n, t) => n + counts[t], 0);
  if (total === 0) return null;
  return (
    <div className="mb-4 last:mb-0">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-sm font-medium" style={{ color: "var(--color-ink)" }}>{label}</span>
        <span className="text-xs" style={{ color: "var(--color-faint)" }}>{total} books</span>
      </div>
      <div className="flex h-3 rounded-full overflow-hidden gap-px" style={{ background: "var(--color-surface-2)" }}>
        {tiers.map((t) => (
          <div key={t} title={`${t}: ${counts[t]}`} style={{ width: `${(counts[t] / total) * 100}%`, background: TIER_COLORS[t] }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2">
        {tiers.map((t) => (
          <span key={t} className="flex items-center gap-1 text-xs" style={{ color: "var(--color-muted)" }}>
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: TIER_COLORS[t] }} />
            {t} <span className="tabular-nums" style={{ color: "var(--color-ink)" }}>{counts[t]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Books per year as stacked columns (fiction under nonfiction). */
function YearChart({ rows }: { rows: { year: number; fiction: number; nonfiction: number; books: number }[] }) {
  const data = [...rows].sort((a, b) => a.year - b.year);
  const max = Math.max(1, ...data.map((r) => r.books));
  return (
    <div className="rounded-2xl p-4" style={tileStyle}>
      <div className="flex justify-end mb-3"><Legend /></div>
      <div className="flex items-end gap-2 h-40 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
        {data.map((r) => (
          <div key={r.year} className="flex flex-col items-center gap-1 flex-1 min-w-8 h-full justify-end">
            <span className="text-[11px] tabular-nums" style={{ color: "var(--color-muted)" }}>{r.books}</span>
            {/* The bar's % height resolves against this flex-1 well, not the
                whole column (which also holds the two labels). */}
            <div className="flex-1 w-full flex items-end justify-center min-h-0">
              <div
                className="w-full max-w-10 rounded-md overflow-hidden flex flex-col"
                style={{ height: `${(r.books / max) * 100}%`, minHeight: 4 }}
                title={`${r.year}: ${r.fiction} fiction, ${r.nonfiction} nonfiction`}
              >
                <div style={{ flex: r.nonfiction, background: NONFICTION_COLOR }} />
                <div style={{ flex: r.fiction, background: FICTION_COLOR }} />
              </div>
            </div>
            <span className="text-[11px] tabular-nums" style={{ color: "var(--color-faint)" }}>
              {r.year}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** One track's headline numbers. */
function TypeCard({ title, color, s }: { title: string; color: string; s: TypeSummary }) {
  return (
    <div className="rounded-2xl p-4" style={tileStyle}>
      <div className="flex items-center gap-2 mb-3">
        <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: color }} />
        <span className="font-display text-base font-semibold" style={{ color: "var(--color-ink)" }}>{title}</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {[
          ["Books", `${s.books}`],
          ["Avg WA", s.avg_wa != null ? s.avg_wa.toFixed(2) : "—"],
          ["Words", fmtWords(s.total_words)],
        ].map(([l, v]) => (
          <div key={l}>
            <div className="text-[11px]" style={{ color: "var(--color-muted)" }}>{l}</div>
            <div className="font-display text-xl font-bold tabular-nums" style={{ color: "var(--color-ink)" }}>{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** The top of the combined WA leaderboard. */
function TopBooks({ rows }: { rows: CombinedRankRow[] }) {
  const top = [...rows].sort((a, b) => (b.wa ?? 0) - (a.wa ?? 0)).slice(0, 5);
  if (top.length === 0) return null;
  return (
    <ul className="rounded-2xl overflow-hidden" style={tileStyle}>
      {top.map((r, i) => (
        <li
          key={`${r.type}:${r.title}`}
          className="flex items-center gap-3 px-4 py-3"
          style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}
        >
          <span className="w-4 text-right font-display italic text-sm" style={{ color: "var(--color-faint)" }}>{i + 1}</span>
          <span className="wa-badge">{r.wa != null ? r.wa.toFixed(1) : "—"}</span>
          <span className="min-w-0 flex-1">
            <span className="font-display font-semibold text-sm leading-snug line-clamp-2 break-words" style={{ color: "var(--color-ink)" }}>
              {r.title}
            </span>
            <span className="block text-xs mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
              {r.author} · {r.type === "nonfiction" ? "Nonfiction" : "Fiction"}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

const TYPE_TABS: { id: "all" | BookKind; label: string }[] = [
  { id: "all", label: "All" },
  { id: "fiction", label: "Fiction" },
  { id: "nonfiction", label: "Nonfiction" },
];

const RANK_COLS: ColDef<CombinedRankRow>[] = [
  { key: "rank", label: "#", type: "numeric", getValue: (r) => r.rank, align: "left", autoRank: true, sortable: false },
  { key: "title", label: "Book", type: "string", getValue: (r) => r.title, align: "left" },
  { key: "author", label: "Author", type: "string", getValue: (r) => r.author, align: "left" },
  { key: "type", label: "Type", type: "string", getValue: (r) => r.type, align: "left",
    formatter: (v) => (v === "nonfiction" ? "Nonfiction" : "Fiction") },
  { key: "wa", label: "WA", type: "numeric", getValue: (r) => r.wa ?? 0,
    formatter: (v) => (v != null ? Number(v).toFixed(2) : "—") },
  { key: "total_average", label: "Total Avg", type: "numeric", getValue: (r) => r.total_average ?? 0,
    formatter: (v) => (v != null ? Number(v).toFixed(2) : "—") },
];

export default function StatsClient({
  data,
  showRanking = true,
  section = "all",
}: {
  data: CombinedStatsResponse;
  /** Which part to render. The /stats page splits the dashboard around its
   *  Rankings list: "header" (title + headline tiles) above it, "details"
   *  (top rated, tiers, per-year chart, by-type cards) below it. "all" — the
   *  public-profile Stats tab — renders everything in one piece. */
  section?: "all" | "header" | "details";
  // The cross-type "all books" table. On by default (the public-profile Stats
  // tab keeps its own copy); the merged /stats page passes false because its
  // Rankings section below already renders this leaderboard.
  showRanking?: boolean;
}) {
  const { totals, by_type, tier_distribution, per_year, combined_ranking } = data;
  const [typeFilter, setTypeFilter] = useState<"all" | BookKind>("all");

  // The newest two years with reads, for the "this year" tile.
  const [thisYear, lastYear] = useMemo(
    () => [...per_year].sort((a, b) => b.year - a.year),
    [per_year],
  );

  const ranking = useMemo(
    () => (typeFilter === "all" ? combined_ranking : combined_ranking.filter((r) => r.type === typeFilter)),
    [combined_ranking, typeFilter]
  );

  const header = section !== "details";
  const details = section !== "header";

  return (
    <div>
      {header && (
      <>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Stats
        </h1>
      </div>

      {/* Headline tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile
          label="Books read"
          value={`${totals.total_books}`}
          foot={`${totals.fiction_books} fiction · ${totals.nonfiction_books} nonfiction`}
        >
          <SplitBar fiction={totals.fiction_books} nonfiction={totals.nonfiction_books} />
        </StatTile>
        <StatTile label="Words read" value={fmtWords(totals.total_words)} />
        <StatTile
          label={thisYear ? `Read in ${thisYear.year}` : "This year"}
          value={thisYear ? `${thisYear.books}` : "—"}
          foot={lastYear ? `${lastYear.books} in ${lastYear.year}` : undefined}
        />
        <StatTile
          label="Avg Total Avg"
          value={totals.avg_total_average != null ? totals.avg_total_average.toFixed(2) : "—"}
        />
      </div>
      </>
      )}

      {details && (
      <>
      <div className="grid lg:grid-cols-2 gap-x-6">
        <div>
          <SectionHeading>Top rated</SectionHeading>
          <TopBooks rows={combined_ranking} />
        </div>
        <div>
          <SectionHeading>Tiers</SectionHeading>
          <div className="rounded-2xl p-4" style={tileStyle}>
            <TierBar label="Fiction" counts={tier_distribution.fiction} order={tier_distribution.tier_order} />
            <TierBar label="Nonfiction" counts={tier_distribution.nonfiction} order={tier_distribution.tier_order} />
          </div>
        </div>
      </div>

      {per_year.length > 0 && (
        <>
          <SectionHeading>Books per year</SectionHeading>
          <YearChart rows={per_year} />
        </>
      )}

      <SectionHeading>By type</SectionHeading>
      <div className="grid sm:grid-cols-2 gap-3">
        <TypeCard title="Fiction" color={FICTION_COLOR} s={by_type.fiction} />
        <TypeCard title="Nonfiction" color={NONFICTION_COLOR} s={by_type.nonfiction} />
      </div>
      </>
      )}

      {/* Combined cross-type ranking (by WA). Suppressed on the merged /stats
          page (showRanking={false}), where the Rankings section renders the same
          leaderboard; shown standalone e.g. on the public-profile Stats tab. */}
      {showRanking && section === "all" && (
        <>
          <SectionHeading>All books · ranked by WA</SectionHeading>
          <SegmentedTabs tabs={TYPE_TABS} value={typeFilter} onChange={setTypeFilter} className="mb-4" />
          <SortableTable
            columns={RANK_COLS}
            data={ranking}
            defaultSort={{ key: "wa", dir: "desc" }}
            getRowKey={(r) => `${r.type}:${r.title}`}
          />
        </>
      )}

    </div>
  );
}
