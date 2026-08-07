import { fetchScoreAnchors, fetchWeights } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import WelcomeClient from "./WelcomeClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Welcome — The Reading Ledger",
  description:
    "A one-minute tour of your reading ledger, plus a chance to set the genre weights the engine uses to rank and predict.",
};

export default async function WelcomePage() {
  // The setup step writes per-user weight overrides — there is no per-user
  // identity or backend on the read-only public snapshot, so the tour is part of
  // the full app only. (Matches how /weights guards itself.)
  if (READONLY) {
    return (
      <ComingSoon
        title="Welcome"
        subtitle="The guided setup is part of the full app."
      />
    );
  }

  // Seed the pickers from the caller's effective FICTION weights and their rating
  // scale (the prose→number anchors grounded research scores against). For a
  // brand-new account both are exactly the shared defaults; neither builds an
  // engine or touches the books table, so a 0-book account is fine.
  const token = await getServerAccessToken();
  const [weights, anchors] = await Promise.all([
    fetchWeights("fiction", token),
    fetchScoreAnchors(token),
  ]);
  return <WelcomeClient weights={weights} anchors={anchors} />;
}
