import { fetchBooks } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import AnalyticsClient from "./AnalyticsClient";

// Fetch fresh on every load so a newly-added book shows up with no extra wiring;
// all analytics are derived client-side from this payload.
export const dynamic = "force-dynamic";

export default async function AnalyticsPage() {
  const token = await getServerAccessToken();
  const data = await fetchBooks("fiction", token);
  return <AnalyticsClient books={data.books} />;
}
