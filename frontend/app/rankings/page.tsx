import { fetchBooks } from "@/lib/api";
import RankingsClient from "./RankingsClient";

export const dynamic = "force-dynamic";

export default async function RankingsPage() {
  const data = await fetchBooks();
  return <RankingsClient data={data} />;
}
