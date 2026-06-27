import { fetchCalibrationHealth } from "@/lib/api";
import CalibrationClient from "./CalibrationClient";

export const dynamic = "force-dynamic";

export default async function CalibrationPage() {
  const health = await fetchCalibrationHealth();
  return <CalibrationClient health={health} />;
}
