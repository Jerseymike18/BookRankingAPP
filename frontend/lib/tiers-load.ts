import { fetchTiers, type ServerToken } from "@/lib/api";
import type { TiersResponse } from "@/lib/types";

/**
 * The fiction tier payloads a page needs: the all-years cohort plus one per year
 * read (tier bands are computed within each year's cohort). On the live backend
 * `years=all` returns them in ONE response; the static snapshot has no `by_year`
 * but keeps per-year files on local disk, where the extra reads cost nothing.
 * Shared by the Tier List page and the Rankings on /stats so both band books
 * identically.
 */
export async function loadFictionTiers(
  token: ServerToken,
): Promise<{ allData: TiersResponse; byYear: Record<number, TiersResponse> }> {
  const allData = await fetchTiers(undefined, "fiction", token, true);
  let byYear: Record<number, TiersResponse> = {};
  if (allData.by_year) {
    for (const [y, data] of Object.entries(allData.by_year)) byYear[Number(y)] = data;
  } else {
    const years = [
      ...new Set(allData.books.map((b) => b.year_read).filter((y): y is number => y != null)),
    ].sort((a, b) => b - a);
    const perYear = await Promise.all(years.map((y) => fetchTiers(y, "fiction", token)));
    byYear = Object.fromEntries(years.map((y, i) => [y, perYear[i]]));
  }
  return { allData, byYear };
}
