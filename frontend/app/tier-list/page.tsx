import { fetchTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import TierListView from "@/components/views/TierListView";
import type { TiersResponse } from "@/lib/types";

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
  const [fictionAll, nonfictionAll] = await Promise.all([
    fetchTiers(undefined, "fiction", token),
    fetchTiers(undefined, "nonfiction", token),
  ]);
  // Fiction tier bands are computed within each year's cohort, so fetch one
  // snapshot per year the reader actually has. Nonfiction isn't split by year.
  const years = [
    ...new Set(fictionAll.books.map((b) => b.year_read).filter((y): y is number => y != null)),
  ].sort((a, b) => b - a);
  const perYear = await Promise.all(years.map((y) => fetchTiers(y, "fiction", token)));
  const byYear: Record<number, TiersResponse> = {};
  years.forEach((y, i) => {
    byYear[y] = perYear[i];
  });
  return (
    <TierListView
      fiction={{ allData: fictionAll, byYear }}
      nonfiction={{ allData: nonfictionAll }}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
