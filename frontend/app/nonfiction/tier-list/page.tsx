import { fetchTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TierListView from "@/components/views/TierListView";

export const dynamic = "force-dynamic";

export default async function NonfictionTierListPage() {
  const token = await getServerAccessToken();
  const data = await fetchTiers(undefined, "nonfiction", token);
  // Nonfiction tiers aren't split by year (the endpoint has no year param), so
  // there are no per-year tabs — pass an empty by-year map.
  return <TierListView allData={data} byYear={{}} kind="nonfiction" />;
}
