import { notFound } from "next/navigation";
import { getServerAccessToken } from "@/lib/supabase/server";
import {
  fetchUserProfile,
  fetchUserBooks,
  fetchUserTiers,
  fetchUserReadQueue,
  fetchUserNonfictionReadQueue,
  fetchUserStats,
  PROFILE_NOT_FOUND,
} from "@/lib/api";
import type { TiersResponse } from "@/lib/types";
import ProfileClient from "./ProfileClient";

export const dynamic = "force-dynamic";

// A public profile: another user's rankings, tier list, to-read queue and stats,
// rendered read-only (edit/mutation UI is forced off via ReadOnlyProvider). Data
// is the target's own, computed on the target's own weights — the same shapes the
// owner's own pages use, so the exact same view components render it. A private or
// missing handle 404s (the resolver never confirms a private handle exists).
export default async function ProfilePage({
  params,
}: {
  params: Promise<{ handle: string }>;
}) {
  const { handle } = await params;
  const token = await getServerAccessToken();
  try {
    const [
      header,
      fictionBooks,
      nonfictionBooks,
      stats,
      fictionTiersAll,
      nonfictionTiersAll,
      fictionQueue,
      nonfictionQueue,
    ] = await Promise.all([
      fetchUserProfile(handle, token),
      fetchUserBooks(handle, "fiction", token),
      fetchUserBooks(handle, "nonfiction", token),
      fetchUserStats(handle, token),
      fetchUserTiers(handle, "fiction", undefined, token),
      fetchUserTiers(handle, "nonfiction", undefined, token),
      fetchUserReadQueue(handle, token),
      fetchUserNonfictionReadQueue(handle, token),
    ]);

    // Fiction tier bands are computed within each year's cohort, so fetch one
    // snapshot per year the reader actually has (mirrors the owner's tier page).
    const years = [
      ...new Set(
        fictionTiersAll.books.map((b) => b.year_read).filter((y): y is number => y != null),
      ),
    ].sort((a, b) => b - a);
    const perYear = await Promise.all(
      years.map((y) => fetchUserTiers(handle, "fiction", y, token)),
    );
    const byYear: Record<number, TiersResponse> = {};
    years.forEach((y, i) => {
      byYear[y] = perYear[i];
    });

    return (
      <ProfileClient
        header={header}
        fictionBooks={fictionBooks}
        nonfictionBooks={nonfictionBooks}
        combined={stats.combined_ranking}
        stats={stats}
        fictionTiers={{ allData: fictionTiersAll, byYear }}
        nonfictionTiers={{ allData: nonfictionTiersAll }}
        fictionQueue={fictionQueue}
        nonfictionQueue={nonfictionQueue}
      />
    );
  } catch (e) {
    if (e instanceof Error && e.message === PROFILE_NOT_FOUND) notFound();
    throw e;
  }
}
