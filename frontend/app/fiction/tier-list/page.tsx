import { fetchTiers } from "@/lib/api";
import TierListView from "@/components/views/TierListView";

export const dynamic = "force-dynamic";

export default async function FictionTierListPage() {
  const [allData, data2026, data2025] = await Promise.all([
    fetchTiers(undefined, "fiction"),
    fetchTiers(2026, "fiction"),
    fetchTiers(2025, "fiction"),
  ]);
  return (
    <TierListView allData={allData} data2026={data2026} data2025={data2025} kind="fiction" />
  );
}
