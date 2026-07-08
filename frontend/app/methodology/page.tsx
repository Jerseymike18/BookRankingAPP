import { fetchEngineParameters, fetchTrackRecord } from "@/lib/api";
import MethodologyClient from "./MethodologyClient";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "How the Engine Works — The Reading Ledger",
  description:
    "The prediction engine end to end: the 14-component weighted schema, empirical-Bayes shrinkage, conformal prediction intervals, and walk-forward validation — with the drift-prone numbers pulled live from the engine.",
};

export default async function MethodologyPage() {
  // Engine parameters are always served (never 404). The track record is reused
  // for the validation baselines so this page and /track-record can't disagree;
  // it may be null until the walk-forward artifacts exist, handled in the client.
  const [params, track] = await Promise.all([
    fetchEngineParameters(),
    fetchTrackRecord().catch(() => null),
  ]);
  return <MethodologyClient params={params} track={track} />;
}
