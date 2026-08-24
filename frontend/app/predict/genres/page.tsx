import GenrePredictClient from "./GenrePredictClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

export default async function GenrePredictPage() {
  // Same gate as book prediction: this spends an Anthropic call and reads the
  // caller's own delta_log, so it cannot run on the backend-free public deploy.
  if (READONLY) {
    return (
      <ComingSoon
        title="Genre Prediction"
        subtitle="Not available on the read-only public site."
      />
    );
  }
  // No SSR fetch: the evidence is only ever computed on demand (one LLM call),
  // so there is nothing to prefetch and nothing to render until the reader asks.
  return <GenrePredictClient />;
}
