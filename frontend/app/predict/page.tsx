import { fetchBooks } from "@/lib/api";
import PredictClient from "./PredictClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export default async function PredictPage() {
  // Predict spends Anthropic tokens and writes recommendations — not available
  // on a read-only public deploy.
  if (READONLY) {
    return <ComingSoon title="Predict" subtitle="Not available on the read-only public site." />;
  }
  const data = await fetchBooks();
  return <PredictClient categoryOrder={data.category_order} />;
}
