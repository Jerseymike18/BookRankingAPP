import { fetchReadingStats, fetchReadingStatus } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import ReadingView from "@/components/views/ReadingView";

export const dynamic = "force-dynamic";

export default async function NonfictionReadingPage() {
  const token = await getServerAccessToken();
  const [stats, status] = await Promise.all([
    fetchReadingStats("nonfiction", token),
    fetchReadingStatus("nonfiction", token),
  ]);
  return <ReadingView stats={stats} status={status} kind="nonfiction" />;
}
