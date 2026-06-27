"use client";

import { useState, useMemo } from "react";
import type { SeriesResponse, SeriesEntry, SeriesTiersResponse, SeriesTierEntry } from "@/lib/types";
import { TierLadder } from "@/components/TierLadder";

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

/* ── Sort types ───────────────────────────────────────────────────────────── */

type SeriesSortField = "adjusted_wa" | "avg_wa" | "books";
type SortDir = "desc" | "asc";

function getSortValue(s: SeriesEntry, field: SeriesSortField): number {
  if (field === "books") return s.books;
  return (s[field] ?? 0);
}

function SortTh({
  label,
  field,
  active,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  field: SeriesSortField;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <th
      onClick={onClick}
      className={`px-3 py-2.5 text-${align} font-semibold text-xs uppercase tracking-wider cursor-pointer select-none whitespace-nowrap`}
      style={{
        color: active ? "var(--color-sage)" : "var(--color-muted)",
        background: active ? "var(--color-sage-light)" : "transparent",
      }}
    >
      {label}{active ? (dir === "desc" ? " ▼" : " ▲") : ""}
    </th>
  );
}

/* ── Rankings tab ─────────────────────────────────────────────────────────── */

function RankingsTab({ data }: { data: SeriesResponse }) {
  const [genre, setGenre] = useState("");
  const [sortField, setSortField] = useState<SeriesSortField>("adjusted_wa");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleSort(field: SeriesSortField) {
    if (field === sortField) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  }

  const genres = useMemo(
    () => [...new Set(data.series.map((s) => s.genre))].sort(),
    [data.series]
  );

  const sorted = useMemo(() => {
    const base = genre ? data.series.filter((s) => s.genre === genre) : data.series;
    const mult = sortDir === "desc" ? -1 : 1;
    return [...base].sort((a, b) => mult * (getSortValue(a, sortField) - getSortValue(b, sortField)));
  }, [data.series, genre, sortField, sortDir]);

  if (data.series.length === 0) {
    return <p className="text-sm" style={{ color: "var(--color-muted)" }}>No multi-book series found.</p>;
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
              <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>#</th>
              <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Series</th>
              <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Author</th>
              <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Genre</th>
              <SortTh label="Books" field="books" active={sortField === "books"} dir={sortDir} onClick={() => handleSort("books")} align="right" />
              <SortTh label="Adj WA" field="adjusted_wa" active={sortField === "adjusted_wa"} dir={sortDir} onClick={() => handleSort("adjusted_wa")} align="right" />
              <SortTh label="Avg WA" field="avg_wa" active={sortField === "avg_wa"} dir={sortDir} onClick={() => handleSort("avg_wa")} align="right" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((s, i) => (
              <tr
                key={s.series}
                style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}
              >
                <td className="px-3 py-2.5 text-xs" style={{ color: "var(--color-faint)" }}>{i + 1}</td>
                <td className="px-3 py-2.5 font-semibold font-display" style={{ color: "var(--color-ink)" }}>{s.series}</td>
                <td className="px-3 py-2.5" style={{ color: "var(--color-muted)" }}>{s.author}</td>
                <td className="px-3 py-2.5">
                  <span className="genre-chip">{s.genre}</span>
                </td>
                <td className="px-3 py-2.5 text-right" style={{ color: sortField === "books" ? "var(--color-sage)" : "var(--color-muted)", background: sortField === "books" ? "var(--color-sage-light)" : "transparent" }}>{s.books}</td>
                <td className="px-3 py-2.5 text-right font-semibold" style={{ color: "var(--color-sage)", background: sortField === "adjusted_wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{s.adjusted_wa?.toFixed(3) ?? "—"}</td>
                <td className="px-3 py-2.5 text-right" style={{ color: sortField === "avg_wa" ? "var(--color-sage)" : "var(--color-ink)", background: sortField === "avg_wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{s.avg_wa?.toFixed(2) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Tiers tab ────────────────────────────────────────────────────────────── */

function TiersTab({ data }: { data: SeriesTiersResponse }) {
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
    return <p className="text-sm" style={{ color: "var(--color-muted)" }}>No multi-book series found.</p>;
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

export default function SeriesClient({
  seriesData,
  tiersData,
}: {
  seriesData: SeriesResponse;
  tiersData: SeriesTiersResponse;
}) {
  const [tab, setTab] = useState<Tab>("rankings");

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
        <RankingsTab data={seriesData} />
      ) : (
        <TiersTab data={tiersData} />
      )}
    </div>
  );
}
