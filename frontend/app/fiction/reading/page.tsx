import { fetchReadingStats, fetchReadingStatus } from "@/lib/api";
import ReadingView from "@/components/views/ReadingView";

export const dynamic = "force-dynamic";

export default async function FictionReadingPage() {
  const [stats, status] = await Promise.all([
    fetchReadingStats("fiction"),
    fetchReadingStatus("fiction"),
  ]);
  return <ReadingView stats={stats} status={status} kind="fiction" />;
}
