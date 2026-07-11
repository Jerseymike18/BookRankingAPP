import { fetchTimeline } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TimelineView from "@/components/views/TimelineView";

export const dynamic = "force-dynamic";

export default async function NonfictionTimelinePage() {
  const token = await getServerAccessToken();
  const data = await fetchTimeline("nonfiction", token);
  return <TimelineView data={data} kind="nonfiction" />;
}
