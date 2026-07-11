import { fetchBooks } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

export default async function NonfictionRankingsPage() {
  const token = await getServerAccessToken();
  const data = await fetchBooks("nonfiction", token);
  return <RankingsView data={data} kind="nonfiction" />;
}
