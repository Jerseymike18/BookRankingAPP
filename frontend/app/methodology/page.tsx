import { fetchEngineParameters, fetchEngineValidation } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import MethodologyClient from "./MethodologyClient";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "How the Engine Works — The Reading Ledger",
  description:
    "The prediction engine end to end: the 14-component weighted schema, empirical-Bayes shrinkage, conformal prediction intervals, and walk-forward validation — with the drift-prone numbers pulled live from the engine.",
};

export default async function MethodologyPage() {
  const token = await getServerAccessToken();
  // Engine parameters are always served (never 404). Engine validation is the
  // engine-wide walk-forward baseline (reference library); it may be null
  // until the walk-forward artifacts exist, handled in the client. This is
  // decoupled from /track-record (personal, per-user) by design — the two
  // pages describe different things and can't silently redefine each other.
  const [params, validation] = await Promise.all([
    fetchEngineParameters(token),
    fetchEngineValidation(token).catch(() => null),
  ]);
  return <MethodologyClient params={params} validation={validation} />;
}
