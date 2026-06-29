import { fetchTimeline } from "@/lib/api";
import TimelineView from "@/components/views/TimelineView";

export const dynamic = "force-dynamic";

export default async function FictionTimelinePage() {
  const data = await fetchTimeline("fiction");
  return <TimelineView data={data} kind="fiction" />;
}
