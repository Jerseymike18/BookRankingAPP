import { fetchBooks } from "@/lib/api";
import AnalyticsClient from "./AnalyticsClient";

// Fetch fresh on every load so a newly-added book shows up with no extra wiring;
// all analytics are derived client-side from this payload.
export const dynamic = "force-dynamic";

export default async function AnalyticsPage() {
  const data = await fetchBooks("fiction");
  return <AnalyticsClient books={data.books} />;
}
