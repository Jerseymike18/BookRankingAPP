import { getServerAccessToken } from "@/lib/supabase/server";
import { fetchMyProfile, fetchStats } from "@/lib/api";
import StatsClient from "@/app/stats/StatsClient";
import ProfileSettingsClient from "./ProfileSettingsClient";

export const dynamic = "force-dynamic";

// "My Profile" — the reader's own reading summary (headline tiles + tier mix,
// the same StatsClient section /stats used to lead with), then claiming a public
// handle + toggling whether the profile is visible in the directory /
// at /u/<handle>. Private by default; nothing is exposed until the reader opts
// in here. The summary is tenant-scoped (/api/stats with the caller's token) and
// is not what makes a public profile show stats — that's the /u/ Stats tab.
export default async function ProfileSettingsPage() {
  const token = await getServerAccessToken();
  const [profile, stats] = await Promise.all([fetchMyProfile(token), fetchStats(token)]);
  return (
    <ProfileSettingsClient
      initial={profile}
      summary={<StatsClient data={stats} section="summary" />}
    />
  );
}
