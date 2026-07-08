import { fetchTrackRecord } from "@/lib/api";
import TrackRecordClient from "./TrackRecordClient";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Track Record — The Reading Ledger",
  description:
    "How accurately the engine predicts an unread book's score, validated chronologically (walk-forward) on books it hadn't seen.",
};

export default async function TrackRecordPage() {
  const data = await fetchTrackRecord();
  return <TrackRecordClient data={data} />;
}
