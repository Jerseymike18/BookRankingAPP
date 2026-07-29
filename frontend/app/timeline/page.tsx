import { fetchTimeline } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import TimelineView from "@/components/views/TimelineView";

export const dynamic = "force-dynamic";

// The canonical Timeline route. In-page Fiction / Nonfiction toggle (no "All" —
// per-track category schema + WA scale). Old `/fiction/timeline` +
// `/nonfiction/timeline` redirect here with ?type=.
export default async function TimelinePage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const token = await getServerAccessToken();
  const { type } = await searchParams;
  const [fiction, nonfiction] = await Promise.all([
    fetchTimeline("fiction", token),
    fetchTimeline("nonfiction", token),
  ]);
  return (
    <TimelineView
      fiction={fiction}
      nonfiction={nonfiction}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
