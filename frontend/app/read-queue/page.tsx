import { fetchReadQueue, fetchQueue } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import ReadQueueClient from "./ReadQueueClient";

export const dynamic = "force-dynamic";

export default async function ReadQueuePage() {
  const token = await getServerAccessToken();
  const [data, queue] = await Promise.all([fetchReadQueue(token), fetchQueue(token)]);
  return <ReadQueueClient data={data} initialQueue={queue} />;
}
