import { fetchStats } from "@/lib/api";
import StatsClient from "./StatsClient";

export const dynamic = "force-dynamic";

export default async function StatsPage() {
  const data = await fetchStats();
  return <StatsClient data={data} />;
}
