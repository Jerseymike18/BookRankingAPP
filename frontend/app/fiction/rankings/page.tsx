import { fetchBooks } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import RankingsView from "@/components/views/RankingsView";

export const dynamic = "force-dynamic";

export default async function FictionRankingsPage() {
  const token = await getServerAccessToken();
  const data = await fetchBooks("fiction", token);
  return <RankingsView data={data} kind="fiction" />;
}
