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
  // Fiction tier bands are computed within each year's cohort, so the page needs one
  // payload per year the reader has. `allYears` asks the backend for them in the SAME
  // response: the year list is only knowable FROM that response, so fetching them
  // separately meant a second round trip that could not start until the first landed.
  const [fictionAll, nonfictionAll] = await Promise.all([
    fetchTiers(undefined, "fiction", token, true),
    fetchTiers(undefined, "nonfiction", token),
  ]);

  let byYear: Record<number, TiersResponse> = {};
  if (fictionAll.by_year) {
    for (const [y, data] of Object.entries(fictionAll.by_year)) byYear[Number(y)] = data;
  } else {
    // Static snapshot: no `by_year`, but the per-year files are on local disk, so the
    // extra hop this avoids on the live backend costs nothing here.
    const years = [
      ...new Set(fictionAll.books.map((b) => b.year_read).filter((y): y is number => y != null)),
    ].sort((a, b) => b - a);
    const perYear = await Promise.all(years.map((y) => fetchTiers(y, "fiction", token)));
    byYear = Object.fromEntries(years.map((y, i) => [y, perYear[i]]));
  }
  return (
    <TierListView
      fiction={{ allData: fictionAll, byYear }}
      nonfiction={{ allData: nonfictionAll }}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
