import { fetchBooks } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import PredictClient from "./PredictClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

export default async function PredictPage() {
  const token = await getServerAccessToken();
  // Predict spends Anthropic tokens and writes recommendations — not available
  // on a read-only public deploy.
  if (READONLY) {
    return <ComingSoon title="Predict" subtitle="Not available on the read-only public site." />;
  }
  const data = await fetchBooks("fiction", token);
  return <PredictClient categoryOrder={data.category_order} />;
}
