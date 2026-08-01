import { fetchValidGenres } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import ImportClient from "./ImportClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

export default async function ImportPage() {
  // Importing writes to the database — not available on the read-only public site.
  if (READONLY) {
    return (
      <ComingSoon
        title="Import from Goodreads"
        subtitle="Not available on the read-only public site."
      />
    );
  }
  const token = await getServerAccessToken();
  const [fictionGenres, nonfictionGenres] = await Promise.all([
    fetchValidGenres("fiction", token),
    fetchValidGenres("nonfiction", token),
  ]);
  return (
    <ImportClient fictionGenres={fictionGenres} nonfictionGenres={nonfictionGenres} />
  );
}
