import { fetchTiers } from "@/lib/api";
import TierListView from "@/components/views/TierListView";

export const dynamic = "force-dynamic";

export default async function NonfictionTierListPage() {
  const data = await fetchTiers(undefined, "nonfiction");
  // Nonfiction has no year_read, so the year tabs are hidden; pass the same
  // data for all three slots.
  return (
    <TierListView allData={data} data2026={data} data2025={data} kind="nonfiction" />
  );
}
