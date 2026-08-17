import { fetchSeries, fetchEngineParameters } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import SeriesBreakdownClient from "./SeriesBreakdownClient";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Series Breakdown — The Reading Ledger",
  description:
    "Term-by-term anatomy of the series-quality score: what Commitment, Peak, Floor and Finale each contribute, and why every term is measured as a deviation from the series' own average.",
};

export default async function SeriesBreakdownPage() {
  const token = await getServerAccessToken();
  // Fiction only — the nonfiction series rollup has no quality model, so there
  // would be no terms to break down.
  const [data, params] = await Promise.all([
    fetchSeries("fiction", token),
    // The coefficients are nice-to-have context, not the point of the page: if
    // this payload is missing the table still renders, just without the
    // constants beside each term.
    fetchEngineParameters(token).catch(() => null),
  ]);
  return <SeriesBreakdownClient data={data} params={params} />;
}
