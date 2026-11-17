import { fetchBooks, fetchStats, fetchTiers } from "@/lib/api";
import { loadFictionTiers } from "@/lib/tiers-load";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import StatsClient from "./StatsClient";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

// The merged Stats + Rankings page: the Fiction / Nonfiction / All rankings,
// then the books-per-year chart. The headline tiles and tier summary live on
// /profile (StatsClient section="summary"); this page renders only the chart
// (section="years"), so the leaderboard isn't rendered twice. The former
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
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Stats
        </h1>
      </div>
      <section id="rankings" className="scroll-mt-24">
        <h2
          className="font-display text-lg font-semibold mb-3"
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
      <StatsClient data={stats} section="years" />
    </div>
  );
}
