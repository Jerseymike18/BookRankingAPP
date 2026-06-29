import { fetchTimeline } from "@/lib/api";
import TimelineView from "@/components/views/TimelineView";

export const dynamic = "force-dynamic";

export default async function NonfictionTimelinePage() {
  const data = await fetchTimeline("nonfiction");
  return <TimelineView data={data} kind="nonfiction" />;
}
