import { fetchTrackRecord } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TrackRecordClient from "./TrackRecordClient";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Track Record — The Reading Ledger",
  description:
    "How accurately the engine predicts an unread book's score, validated chronologically (walk-forward) on books it hadn't seen.",
};

export default async function TrackRecordPage() {
  const token = await getServerAccessToken();
  const data = await fetchTrackRecord(token);
  return <TrackRecordClient data={data} />;
}
