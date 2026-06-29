import { fetchSeries, fetchSeriesTiers } from "@/lib/api";
import SeriesView from "@/components/views/SeriesView";

export const dynamic = "force-dynamic";

export default async function NonfictionSeriesPage() {
  const [seriesData, tiersData] = await Promise.all([
    fetchSeries("nonfiction"),
    fetchSeriesTiers("nonfiction"),
  ]);
  return <SeriesView seriesData={seriesData} tiersData={tiersData} kind="nonfiction" />;
}
