"use client";

import { useState } from "react";
import type {
  BooksResponse,
  TiersResponse,
  CombinedStatsResponse,
  CombinedRankRow,
  ReadQueueResponse,
  NonfictionReadQueueResponse,
  PublicProfile,
} from "@/lib/types";
import { ReadOnlyProvider } from "@/lib/readonly-context";
import RankingsView from "@/components/views/RankingsView";
import TierListView from "@/components/views/TierListView";
import StatsClient from "@/app/stats/StatsClient";
import ReadQueueTypeSwitch from "@/app/read-queue/ReadQueueTypeSwitch";

const TABS = ["rankings", "tiers", "queue", "stats"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = {
  rankings: "Rankings",
  tiers: "Tier List",
  queue: "To-Read",
  stats: "Stats",
};

export default function ProfileClient({
  header,
  fictionBooks,
  nonfictionBooks,
  combined,
  stats,
  fictionTiers,
  nonfictionTiers,
  fictionQueue,
  nonfictionQueue,
}: {
  header: PublicProfile;
  fictionBooks: BooksResponse;
  nonfictionBooks: BooksResponse;
  combined: CombinedRankRow[];
  stats: CombinedStatsResponse;
  fictionTiers: { allData: TiersResponse; byYear: Record<number, TiersResponse> };
  nonfictionTiers: { allData: TiersResponse };
  fictionQueue: ReadQueueResponse;
  nonfictionQueue: NonfictionReadQueueResponse;
}) {
  const [tab, setTab] = useState<Tab>("rankings");
  const name = header.display_name || header.handle;

  return (
    <div>
      {/* Profile header */}
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          {name}
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          @{header.handle} · {header.fiction_books} fiction ·{" "}
          {header.nonfiction_books} nonfiction
        </p>
      </div>

      {/* Sub-tab bar (reuses the sage-pill pattern used across the app) */}
      <div
        className="flex gap-1 mb-6 p-1 rounded-xl w-fit flex-wrap"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: tab === t ? "var(--color-sage)" : "transparent",
              color: tab === t ? "#fff" : "var(--color-muted)",
            }}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {/* Everything below renders READ-ONLY: the reused interactive components
          (RankingsView, the read-queue clients) hide their edit/mutation UI
          because ReadOnlyProvider forces it off for this whole subtree. */}
      <ReadOnlyProvider value={true}>
        {tab === "rankings" && (
          <RankingsView
            fiction={fictionBooks}
            nonfiction={nonfictionBooks}
            combined={combined}
            initialType="all"
          />
        )}
        {tab === "tiers" && (
          <TierListView fiction={fictionTiers} nonfiction={nonfictionTiers} initialType="fiction" />
        )}
        {tab === "queue" && (
          <ReadQueueTypeSwitch
            fiction={{ data: fictionQueue, initialQueue: [] }}
            nonfiction={{ data: nonfictionQueue, initialQueue: [] }}
            initialType="fiction"
          />
        )}
        {tab === "stats" && <StatsClient data={stats} />}
      </ReadOnlyProvider>
    </div>
  );
}
