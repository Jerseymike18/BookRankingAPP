import { fetchBooks, fetchStats } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import StatsClient from "./StatsClient";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

// The merged Stats + Rankings page. The summary dashboard (totals, tier
// distribution, books-per-year) leads; the full Fiction / Nonfiction / All
// ranking tables — including the cross-type WA leaderboard — follow under the
// "Rankings" heading. StatsClient's own cross-type table is suppressed here
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
  const [fiction, nonfiction, stats] = await Promise.all([
    fetchBooks("fiction", token),
    fetchBooks("nonfiction", token),
    fetchStats(token),
  ]);
  return (
    <div>
      <StatsClient data={stats} showRanking={false} />
      <section id="rankings" className="scroll-mt-24">
        <h2
          className="font-display text-lg font-semibold mt-10 mb-3"
          style={{ color: "var(--color-ink)" }}
        >
          Rankings
        </h2>
        <RankingsView
          embedded
          fiction={fiction}
          nonfiction={nonfiction}
          combined={stats.combined_ranking}
          initialType={parseTypeScope(type, "all", true)}
        />
      </section>
    </div>
  );
}
