import { fetchBooks, fetchStats, fetchTiers } from "@/lib/api";
import { loadFictionTiers } from "@/lib/tiers-load";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import StatsClient from "./StatsClient";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

// The merged Stats + Rankings page. Headline tiles lead, the Fiction /
// Nonfiction / All rankings follow under "Rankings", and the deeper summaries
// (tiers, books per year) sit at the bottom. StatsClient's own cross-type table is suppressed here
// (showRanking={false}) so the leaderboard isn't rendered twice. The former
// /rankings route redirects here (see next.config.ts), seeding the ranking
// table's type toggle via ?type=; #rankings jumps straight to the tables.
export default async function StatsPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const token = await getServerAccessToken();
  const { type } = await searchParams;
  // Tiers ride along so the rankings band each book exactly as the Tier List does.
  const [fiction, nonfiction, stats, fictionTiers, nonfictionTiers] = await Promise.all([
    fetchBooks("fiction", token),
    fetchBooks("nonfiction", token),
    fetchStats(token),
    loadFictionTiers(token),
    fetchTiers(undefined, "nonfiction", token),
  ]);
  return (
    <div>
      {/* Title + headline tiles, then the Rankings list, then the deeper
          summaries (tiers, per-year chart) at the bottom. */}
      <StatsClient data={stats} showRanking={false} section="header" />
      <section id="rankings" className="scroll-mt-24">
        <h2
          className="font-display text-lg font-semibold mt-8 mb-3"
          style={{ color: "var(--color-ink)" }}
        >
          Rankings
        </h2>
        <RankingsView
          embedded
          fiction={fiction}
          nonfiction={nonfiction}
          combined={stats.combined_ranking}
          tiers={{ fiction: fictionTiers, nonfiction: { allData: nonfictionTiers } }}
          initialType={parseTypeScope(type, "all", true)}
        />
      </section>
      <StatsClient data={stats} showRanking={false} section="details" />
    </div>
  );
}
