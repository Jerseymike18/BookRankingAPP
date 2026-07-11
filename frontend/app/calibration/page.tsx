import { fetchCalibrationHealth } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import CalibrationClient from "./CalibrationClient";

export const dynamic = "force-dynamic";

export default async function CalibrationPage() {
  const token = await getServerAccessToken();
  const health = await fetchCalibrationHealth(token);
  return <CalibrationClient health={health} />;
}
