import { fetchReadingStats, fetchReadingStatus, fetchBooks } from "@/lib/api";
import ReadingClient from "./ReadingClient";

export const dynamic = "force-dynamic";

export default async function ReadingPage() {
  const [stats, status, booksData] = await Promise.all([
    fetchReadingStats(),
    fetchReadingStatus(),
    fetchBooks(),
  ]);
  return <ReadingClient stats={stats} status={status} ratedTitles={booksData.books.map(b => b.title)} />;
}
