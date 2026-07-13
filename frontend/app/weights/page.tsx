import { fetchWeights } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import WeightsPageClient from "./WeightsPageClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

export default async function WeightsPage() {
  // Editing weights writes per-user overrides — not available on the read-only
  // public snapshot (which has no backend and no per-user identity).
  if (READONLY) {
    return (
      <ComingSoon
        title="Genre Weights"
        subtitle="Not available on the read-only public site."
      />
    );
  }
  const token = await getServerAccessToken();
  const [fiction, nonfiction] = await Promise.all([
    fetchWeights("fiction", token),
    fetchWeights("nonfiction", token),
  ]);
  return <WeightsPageClient fiction={fiction} nonfiction={nonfiction} />;
}
