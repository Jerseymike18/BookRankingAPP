import { fetchTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TierListView from "@/components/views/TierListView";

export const dynamic = "force-dynamic";

export default async function NonfictionTierListPage() {
  const token = await getServerAccessToken();
  const data = await fetchTiers(undefined, "nonfiction", token);
  // Nonfiction has no year_read, so the year tabs are hidden; pass the same
  // data for all three slots.
  return (
    <TierListView allData={data} data2026={data} data2025={data} kind="nonfiction" />
  );
}
