import { fetchNonfictionReadQueue, fetchNonfictionQueue } from "@/lib/api";
import NonfictionReadQueueClient from "./NonfictionReadQueueClient";

export const dynamic = "force-dynamic";

export default async function NonfictionReadQueuePage() {
  const [data, queue] = await Promise.all([
    fetchNonfictionReadQueue(),
    fetchNonfictionQueue(),
  ]);
  return <NonfictionReadQueueClient data={data} initialQueue={queue} />;
}
