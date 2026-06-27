import { fetchReadQueue, fetchQueue } from "@/lib/api";
import ReadQueueClient from "./ReadQueueClient";

export const dynamic = "force-dynamic";

export default async function ReadQueuePage() {
  const [data, queue] = await Promise.all([fetchReadQueue(), fetchQueue()]);
  return <ReadQueueClient data={data} initialQueue={queue} />;
}
