import { fetchBooks } from "@/lib/api";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

export default async function FictionRankingsPage() {
  const data = await fetchBooks("fiction");
  return <RankingsView data={data} kind="fiction" />;
}
