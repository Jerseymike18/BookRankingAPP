import { fetchSeries, fetchSeriesTiers } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import SeriesView from "@/components/views/SeriesView";

export const dynamic = "force-dynamic";

export default async function NonfictionSeriesPage() {
  const token = await getServerAccessToken();
  const [seriesData, tiersData] = await Promise.all([
    fetchSeries("nonfiction", token),
    fetchSeriesTiers("nonfiction", token),
  ]);
  return <SeriesView seriesData={seriesData} tiersData={tiersData} kind="nonfiction" />;
}
