import { fetchTimeline } from "@/lib/api";
import TimelineClient from "./TimelineClient";

export const dynamic = "force-dynamic";

export default async function TimelinePage() {
  const data = await fetchTimeline();
  return <TimelineClient data={data} />;
}
