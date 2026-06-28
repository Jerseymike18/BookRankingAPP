"use client";

import { useState, useMemo, useCallback } from "react";
import type { TiersResponse, TierBook } from "@/lib/types";
import { TierLadder } from "@/components/TierLadder";

/* ── Sub-tab bar ──────────────────────────────────────────────────────────── */

type YearTab = "all" | "2026" | "2025";

const YEAR_TABS: { id: YearTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "2026", label: "2026" },
  { id: "2025", label: "2025" },
];

function SubTabs({
  active,
  onChange,
}: {
  active: YearTab;
  onChange: (t: YearTab) => void;
}) {
  return (
    <div
      className="flex gap-1 mb-6 p-1 rounded-xl inline-flex"
      style={{ background: "var(--color-surface-2)" }}
    >
      {YEAR_TABS.map(({ id, label }) => (
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

export default function TierListClient({
  allData,
  data2026,
  data2025,
}: {
  allData: TiersResponse;
  data2026: TiersResponse;
  data2025: TiersResponse;
}) {
  const [yearTab, setYearTab] = useState<YearTab>("all");

  const activeData = useMemo(() => {
    if (yearTab === "2026") return data2026;
    if (yearTab === "2025") return data2025;
    return allData;
  }, [yearTab, allData, data2026, data2025]);

  const { books, tier_counts, tier_order } = activeData;

  const itemsByTier = useMemo(() => {
    const map: Record<string, { label: string }[]> = {};
    for (const t of tier_order) map[t] = [];
    for (const b of books) {
      if (map[b.tier]) map[b.tier].push({ label: b.title });
    }
    // Sort each tier by WA descending (best first, left to right)
    for (const t of tier_order) {
      const tierBooks = books.filter((b: TierBook) => b.tier === t);
      tierBooks.sort((a: TierBook, b: TierBook) => b.wa - a.wa);
      map[t] = tierBooks.map((b: TierBook) => ({ label: b.title }));
    }
    return map;
  }, [books, tier_order]);

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

      <SubTabs active={yearTab} onChange={handleYearTab} />

      <TierLadder tierOrder={tier_order} itemsByTier={itemsByTier} />
    </div>
  );
}
