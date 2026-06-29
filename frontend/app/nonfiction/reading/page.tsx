import { fetchReadingStats, fetchReadingStatus } from "@/lib/api";
import ReadingView from "@/components/views/ReadingView";

export const dynamic = "force-dynamic";

export default async function NonfictionReadingPage() {
  const [stats, status] = await Promise.all([
    fetchReadingStats("nonfiction"),
    fetchReadingStatus("nonfiction"),
  ]);
  return <ReadingView stats={stats} status={status} kind="nonfiction" />;
}
