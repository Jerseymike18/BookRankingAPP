import { fetchTiers } from "@/lib/api";
import TierListClient from "./TierListClient";

export const dynamic = "force-dynamic";

export default async function TierListPage() {
  const [allData, data2026, data2025] = await Promise.all([
    fetchTiers(),
    fetchTiers(2026),
    fetchTiers(2025),
  ]);
  return <TierListClient allData={allData} data2026={data2026} data2025={data2025} />;
}
