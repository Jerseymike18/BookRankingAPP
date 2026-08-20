import {
  fetchReadQueue,
  fetchQueue,
  fetchNonfictionReadQueue,
  fetchNonfictionQueue,
} from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import ReadQueueTypeSwitch from "./ReadQueueTypeSwitch";

export const dynamic = "force-dynamic";

// The one Read Queue route (fiction + nonfiction behind an in-page toggle).
// `/nonfiction/read-queue` redirects here with ?type=nonfiction.
export default async function ReadQueuePage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const token = await getServerAccessToken();
  const { type } = await searchParams;
  const [fData, fQueue, nData, nQueue] = await Promise.all([
    fetchReadQueue(token, false),   // blurbs lazy-load per expanded card
    fetchQueue(token),
    fetchNonfictionReadQueue(token),
    fetchNonfictionQueue(token),
  ]);
  return (
    <ReadQueueTypeSwitch
      fiction={{ data: fData, initialQueue: fQueue }}
      nonfiction={{ data: nData, initialQueue: nQueue }}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
