import { fetchReadingStats, fetchReadingStatus, fetchBooks } from "@/lib/api";
import ReadingView from "@/components/views/ReadingView";

export const dynamic = "force-dynamic";

export default async function FictionReadingPage() {
  const [stats, status, booksResp] = await Promise.all([
    fetchReadingStats("fiction"),
    fetchReadingStatus("fiction"),
    fetchBooks("fiction"),
  ]);
  return <ReadingView stats={stats} status={status} kind="fiction" books={booksResp.books} />;
}
