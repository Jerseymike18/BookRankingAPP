import { fetchStats } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import StatsClient from "./StatsClient";

export const dynamic = "force-dynamic";

export default async function StatsPage() {
  const token = await getServerAccessToken();
  const data = await fetchStats(token);
  return <StatsClient data={data} />;
}
