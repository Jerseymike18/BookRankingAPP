import { fetchTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import TierListView from "@/components/views/TierListView";
import { loadFictionTiers } from "@/lib/tiers-load";

export const dynamic = "force-dynamic";

// The canonical Tier List route. In-page Fiction / Nonfiction toggle (no "All"
// — tier bands are per-track). Old `/fiction/tier-list` + `/nonfiction/tier-list`
// redirect here with ?type=.
export default async function TierListPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const token = await getServerAccessToken();
  const { type } = await searchParams;
  // Fiction bands are per-year cohorts; loadFictionTiers fetches all of them.
  const [{ allData: fictionAll, byYear }, nonfictionAll] = await Promise.all([
    loadFictionTiers(token),
    fetchTiers(undefined, "nonfiction", token),
  ]);
  return (
    <TierListView
      fiction={{ allData: fictionAll, byYear }}
      nonfiction={{ allData: nonfictionAll }}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
