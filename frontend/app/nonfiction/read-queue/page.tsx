import { fetchNonfictionReadQueue, fetchNonfictionQueue } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import NonfictionReadQueueClient from "./NonfictionReadQueueClient";

export const dynamic = "force-dynamic";

export default async function NonfictionReadQueuePage() {
  const token = await getServerAccessToken();
  const [data, queue] = await Promise.all([
    fetchNonfictionReadQueue(token),
    fetchNonfictionQueue(token),
  ]);
  return <NonfictionReadQueueClient data={data} initialQueue={queue} />;
}
