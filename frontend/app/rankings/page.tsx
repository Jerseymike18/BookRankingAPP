import { fetchBooks, fetchStats } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

// The canonical Rankings route. In-page Fiction / Nonfiction / All toggle;
// "All" reads the backend-computed cross-type Total-Average ranking. Old
// `/fiction/rankings` + `/nonfiction/rankings` redirect here with ?type=.
export default async function RankingsPage({
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
    <RankingsView
      fiction={fiction}
      nonfiction={nonfiction}
      combined={stats.combined_ranking}
      initialType={parseTypeScope(type, "all", true)}
    />
  );
}
