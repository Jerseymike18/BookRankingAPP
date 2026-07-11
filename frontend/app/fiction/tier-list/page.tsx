import { fetchTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TierListView from "@/components/views/TierListView";

export const dynamic = "force-dynamic";

export default async function FictionTierListPage() {
  const token = await getServerAccessToken();
  const [allData, data2026, data2025] = await Promise.all([
    fetchTiers(undefined, "fiction", token),
    fetchTiers(2026, "fiction", token),
    fetchTiers(2025, "fiction", token),
  ]);
  return (
    <TierListView allData={allData} data2026={data2026} data2025={data2025} kind="fiction" />
  );
}
