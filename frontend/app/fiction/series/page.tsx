import { fetchSeries, fetchSeriesTiers } from "@/lib/api";
import SeriesClient from "./SeriesClient";

export const dynamic = "force-dynamic";

export default async function SeriesPage() {
  const [seriesData, tiersData] = await Promise.all([
    fetchSeries(),
    fetchSeriesTiers(),
  ]);
  return <SeriesClient seriesData={seriesData} tiersData={tiersData} />;
}
