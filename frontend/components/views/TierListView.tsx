"use client";

import { useState, useMemo, useCallback } from "react";
import type { TiersResponse, TierBook, BookKind } from "@/lib/types";
import { TierLadder, type TierItem } from "@/components/TierLadder";

/* ── Sub-tab bar ──────────────────────────────────────────────────────────── */

type YearTab = string; // "all" or a year read, e.g. "2023"
type YearTabDef = { id: YearTab; label: string };

function SubTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: YearTabDef[];
  active: YearTab;
  onChange: (t: YearTab) => void;
}) {
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

/* ── Main TierList view ───────────────────────────────────────────────────── */

export default function TierListView({
  allData,
  byYear,
  kind = "fiction",
}: {
  allData: TiersResponse;
  // Tier bands are computed within each year's cohort, so the view receives a
  // per-year snapshot keyed by year. Empty (e.g. nonfiction) → no year tabs.
  byYear: Record<number, TiersResponse>;
  kind?: BookKind;
}) {
  const [yearTab, setYearTab] = useState<YearTab>("all");
  const score = (b: TierBook) => (kind === "nonfiction" ? (b.total_average ?? 0) : b.wa);

  const yearTabs = useMemo<YearTabDef[]>(() => {
    const years = Object.keys(byYear)
      .map(Number)
      .sort((a, b) => b - a);
    return years.length > 1
      ? [{ id: "all", label: "All" }, ...years.map((y) => ({ id: String(y), label: String(y) }))]
      : [];
  }, [byYear]);

  const activeData = useMemo(() => {
    if (yearTab === "all") return allData;
    return byYear[Number(yearTab)] ?? allData;
  }, [yearTab, allData, byYear]);

  const { books, tier_counts, tier_order } = activeData;

  const itemsByTier = useMemo(() => {
    const scoreLabel = kind === "nonfiction" ? "Total Avg" : "WA";
    const map: Record<string, TierItem[]> = {};
    // Sort each tier by the primary score descending (best first, left to right)
    for (const t of tier_order) {
      const tierBooks = books
        .filter((b: TierBook) => b.tier === t)
        .sort((a: TierBook, b: TierBook) => score(b) - score(a));
      map[t] = tierBooks.map((b: TierBook) => ({
        label: b.title,
        author: b.author,
        genre: b.genre,
        series: b.series || undefined,
        seriesNumber: b.series_number,
        words: b.words,
        yearRead: b.year_read,
        rank: b.rank,
        score: score(b),
        scoreLabel,
        tier: b.tier,
      }));
    }
    return map;
  }, [books, tier_order, kind]);

  const summaryLine = tier_order
    .filter((t) => tier_counts[t] > 0)
    .map((t) => `${t}: ${tier_counts[t]}`)
    .join("  ·  ");

  const handleYearTab = useCallback((t: YearTab) => setYearTab(t), []);

  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Tier List
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {books.length} books · {summaryLine}
        </p>
      </div>

      {yearTabs.length > 0 && (
        <SubTabs tabs={yearTabs} active={yearTab} onChange={handleYearTab} />
      )}

      <TierLadder tierOrder={tier_order} itemsByTier={itemsByTier} />
    </div>
  );
}
