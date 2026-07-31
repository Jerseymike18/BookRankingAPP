import { getServerAccessToken } from "@/lib/supabase/server";
import { fetchMyProfile } from "@/lib/api";
import ProfileSettingsClient from "./ProfileSettingsClient";

export const dynamic = "force-dynamic";

// "My Profile" — claim a public handle + toggle whether the profile is visible in
// the directory / at /u/<handle>. Private by default; nothing is exposed until the
// reader opts in here.
export default async function ProfileSettingsPage() {
  const token = await getServerAccessToken();
  const profile = await fetchMyProfile(token);
  return <ProfileSettingsClient initial={profile} />;
}
