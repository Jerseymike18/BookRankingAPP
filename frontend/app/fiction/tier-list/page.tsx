import { fetchTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TierListView from "@/components/views/TierListView";
import type { TiersResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function FictionTierListPage() {
  const token = await getServerAccessToken();
  const allData = await fetchTiers(undefined, "fiction", token);
  // Tier bands are computed within each year's cohort, so fetch one snapshot per
  // year the reader actually has (any year — not a hardcoded 2025/2026).
  const years = [
    ...new Set(allData.books.map((b) => b.year_read).filter((y): y is number => y != null)),
  ].sort((a, b) => b - a);
  const perYear = await Promise.all(years.map((y) => fetchTiers(y, "fiction", token)));
  const byYear: Record<number, TiersResponse> = {};
  years.forEach((y, i) => {
    byYear[y] = perYear[i];
  });
  return <TierListView allData={allData} byYear={byYear} kind="fiction" />;
}
