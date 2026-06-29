import { fetchSeries, fetchSeriesTiers } from "@/lib/api";
import SeriesView from "@/components/views/SeriesView";

export const dynamic = "force-dynamic";

export default async function FictionSeriesPage() {
  const [seriesData, tiersData] = await Promise.all([
    fetchSeries("fiction"),
    fetchSeriesTiers("fiction"),
  ]);
  return <SeriesView seriesData={seriesData} tiersData={tiersData} kind="fiction" />;
}
