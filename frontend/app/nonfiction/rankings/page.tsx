import { fetchBooks } from "@/lib/api";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

export default async function NonfictionRankingsPage() {
  const data = await fetchBooks("nonfiction");
  return <RankingsView data={data} kind="nonfiction" />;
}
