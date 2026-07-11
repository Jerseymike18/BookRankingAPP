import { fetchTimeline } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import TimelineView from "@/components/views/TimelineView";

export const dynamic = "force-dynamic";

export default async function FictionTimelinePage() {
  const token = await getServerAccessToken();
  const data = await fetchTimeline("fiction", token);
  return <TimelineView data={data} kind="fiction" />;
}
