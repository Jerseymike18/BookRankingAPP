import { fetchReadingStats, fetchReadingStatus, fetchBooks } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import ReadingView from "@/components/views/ReadingView";

export const dynamic = "force-dynamic";

export default async function FictionReadingPage() {
  const token = await getServerAccessToken();
  const [stats, status, booksResp] = await Promise.all([
    fetchReadingStats("fiction", token),
    fetchReadingStatus("fiction", token),
    fetchBooks("fiction", token),
  ]);
  return <ReadingView stats={stats} status={status} kind="fiction" books={booksResp.books} />;
}
