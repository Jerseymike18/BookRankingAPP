"use client";

import { useState, useMemo } from "react";
import type { SeriesResponse, SeriesEntry, SeriesTiersResponse, SeriesTierEntry, BookKind } from "@/lib/types";
import { TierLadder } from "@/components/TierLadder";
import { useSortable, SortableTh } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";

/* ── Sub-tab bar ──────────────────────────────────────────────────────────── */

type Tab = "rankings" | "tiers";

function SubTabs({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "rankings", label: "Series Rankings" },
    { id: "tiers", label: "Series Tier List" },
  ];
  return (
    <div
      className="flex gap-1 mb-6 p-1 rounded-xl inline-flex"
      style={{ background: "var(--color-surface-2)" }}
    >
      {tabs.map(({ id, label }) => (
        <button
          key={id}
          onClick={() => onChange(id)}
          className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
          style={{
            background: active === id ? "var(--color-surface)" : "transparent",
            color: active === id ? "var(--color-sage)" : "var(--color-muted)",
            boxShadow: active === id ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/* ── Genre filter ─────────────────────────────────────────────────────────── */

function GenreFilter({
  genres,
  active,
  onChange,
}: {
  genres: string[];
  active: string;
  onChange: (g: string) => void;
}) {
  return (
    <select
      value={active}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg px-3 py-2 text-sm mb-4"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-rule)",
        color: "var(--color-ink)",
      }}
    >
      <option value="">All genres</option>
      {genres.map((g) => (
        <option key={g} value={g}>{g}</option>
      ))}
    </select>
  );
}

/* ── Column definitions ───────────────────────────────────────────────────── */

const SERIES_COLS: ColDef<SeriesEntry>[] = [
  { key: "series",      label: "Series",  type: "string",  getValue: (s) => s.series,      align: "left"  },
  { key: "author",      label: "Author",  type: "string",  getValue: (s) => s.author,      align: "left"  },
  { key: "genre",       label: "Genre",   type: "string",  getValue: (s) => s.genre,       align: "left"  },
  { key: "books",       label: "Books",   type: "numeric", getValue: (s) => s.books,       align: "right" },
  { key: "adjusted_wa", label: "Adj WA",  type: "numeric", getValue: (s) => s.adjusted_wa, align: "right" },
  { key: "avg_wa",      label: "Avg WA",  type: "numeric", getValue: (s) => s.avg_wa,      align: "right" },
];

/* ── Rankings tab ─────────────────────────────────────────────────────────── */

function RankingsTab({ data, emptyMsg }: { data: SeriesResponse; emptyMsg: string }) {
  const [genre, setGenre] = useState("");

  const genres = useMemo(
    () => [...new Set(data.series.map((s) => s.genre))].sort(),
    [data.series]
  );

  const filtered = useMemo(
    () => (genre ? data.series.filter((s) => s.genre === genre) : data.series),
    [data.series, genre]
  );

  const { sorted, sortState, handleSort } = useSortable(
    filtered,
    SERIES_COLS,
    { key: "adjusted_wa", dir: "desc" }
  );

  if (data.series.length === 0) {
    return <p className="text-sm" style={{ color: "var(--color-muted)" }}>{emptyMsg}</p>;
  }

  return (
    <div>
      <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
        Ranked by Adjusted WA — avg WA plus a length bonus (0.0582 × (1.18^(n−1) − 1)) minus a short-series penalty (−0.2 per book below 3 read). Click a column header to sort.
      </p>
      <GenreFilter genres={genres} active={genre} onChange={setGenre} />
      <p className="text-xs mb-3" style={{ color: "var(--color-faint)" }}>{sorted.length} series</p>
      <div style={{ overflowX: "auto" }}>
        <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "var(--color-surface-2)", borderBottom: "1px solid var(--color-rule)" }}>
              <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)" }}>#</th>
              {SERIES_COLS.map((col) => (
                <SortableTh key={col.key} col={col} sortState={sortState} onSort={handleSort} />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((s, i) => (
              <tr
                key={s.series}
                style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}
              >
                <td className="px-3 py-2.5 text-xs" style={{ color: "var(--color-faint)" }}>{i + 1}</td>
                <td className="px-3 py-2.5 font-semibold font-display" style={{ color: "var(--color-ink)", background: sortState.key === "series" ? "var(--color-sage-light)" : "transparent" }}>{s.series}</td>
                <td className="px-3 py-2.5" style={{ color: "var(--color-muted)", background: sortState.key === "author" ? "var(--color-sage-light)" : "transparent" }}>{s.author}</td>
                <td className="px-3 py-2.5" style={{ background: sortState.key === "genre" ? "var(--color-sage-light)" : "transparent" }}>
                  <span className="genre-chip">{s.genre}</span>
                </td>
                <td className="px-3 py-2.5 text-right" style={{ color: sortState.key === "books" ? "var(--color-sage)" : "var(--color-muted)", background: sortState.key === "books" ? "var(--color-sage-light)" : "transparent" }}>{s.books}</td>
                <td className="px-3 py-2.5 text-right font-semibold" style={{ color: "var(--color-sage)", background: sortState.key === "adjusted_wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{s.adjusted_wa?.toFixed(3) ?? "—"}</td>
                <td className="px-3 py-2.5 text-right" style={{ color: sortState.key === "avg_wa" ? "var(--color-sage)" : "var(--color-ink)", background: sortState.key === "avg_wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{s.avg_wa?.toFixed(2) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Tiers tab ────────────────────────────────────────────────────────────── */

function TiersTab({ data, emptyMsg }: { data: SeriesTiersResponse; emptyMsg: string }) {
  const [genre, setGenre] = useState("");

  const genres = useMemo(
    () => [...new Set(data.series.map((s) => s.genre))].sort(),
    [data.series]
  );

  const itemsByTier = useMemo(() => {
    const filtered = genre ? data.series.filter((s) => s.genre === genre) : data.series;
    const map: Record<string, { label: string }[]> = {};
    for (const t of data.tier_order) map[t] = [];
    for (const t of data.tier_order) {
      const entries = filtered
        .filter((s: SeriesTierEntry) => s.tier === t)
        .sort((a: SeriesTierEntry, b: SeriesTierEntry) => (b.adjusted_wa ?? 0) - (a.adjusted_wa ?? 0));
      map[t] = entries.map((s: SeriesTierEntry) => ({ label: s.series }));
    }
    return map;
  }, [data, genre]);

  if (data.series.length === 0) {
    return <p className="text-sm" style={{ color: "var(--color-muted)" }}>{emptyMsg}</p>;
  }

  const visibleCount = Object.values(itemsByTier).reduce((n, arr) => n + arr.length, 0);

  const summaryLine = data.tier_order
    .filter((t) => (itemsByTier[t]?.length ?? 0) > 0)
    .map((t) => `${t}: ${itemsByTier[t].length}`)
    .join("  ·  ");

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <GenreFilter genres={genres} active={genre} onChange={setGenre} />
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
        {visibleCount} series{summaryLine ? ` · ${summaryLine}` : ""}
      </p>
      <TierLadder tierOrder={data.tier_order} itemsByTier={itemsByTier} />
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────────────────────── */

export default function SeriesView({
  seriesData,
  tiersData,
  kind = "fiction",
}: {
  seriesData: SeriesResponse;
  tiersData: SeriesTiersResponse;
  kind?: BookKind;
}) {
  const [tab, setTab] = useState<Tab>("rankings");
  const emptyMsg =
    kind === "nonfiction"
      ? "No nonfiction series yet."
      : "No multi-book series found.";

  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Series
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {seriesData.series.length} series tracked
        </p>
      </div>

      <SubTabs active={tab} onChange={setTab} />

      {tab === "rankings" ? (
        <RankingsTab data={seriesData} emptyMsg={emptyMsg} />
      ) : (
        <TiersTab data={tiersData} emptyMsg={emptyMsg} />
      )}
    </div>
  );
}
