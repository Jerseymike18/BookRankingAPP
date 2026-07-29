import { fetchSeries, fetchSeriesTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import SeriesView from "@/components/views/SeriesView";

export const dynamic = "force-dynamic";

// The canonical Series route. In-page Fiction / Nonfiction toggle (no "All" —
// Adjusted WA is per-track). Old `/fiction/series` + `/nonfiction/series`
// redirect here with ?type=.
export default async function SeriesPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const token = await getServerAccessToken();
  const { type } = await searchParams;
  const [fSeries, fTiers, nSeries, nTiers] = await Promise.all([
    fetchSeries("fiction", token),
    fetchSeriesTiers("fiction", token),
    fetchSeries("nonfiction", token),
    fetchSeriesTiers("nonfiction", token),
  ]);
  return (
    <SeriesView
      fiction={{ seriesData: fSeries, tiersData: fTiers }}
      nonfiction={{ seriesData: nSeries, tiersData: nTiers }}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
