import { fetchReadingStats, fetchReadingStatus, fetchBooks } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import { parseTypeScope } from "@/lib/scope";
import ReadingView from "@/components/views/ReadingView";

export const dynamic = "force-dynamic";

// The canonical Reading (Currently Reading) route. In-page Fiction / Nonfiction
// toggle (no "All" — the combined view is the Stats page). Old `/fiction/reading`
// + `/nonfiction/reading` redirect here with ?type=.
export default async function ReadingPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const token = await getServerAccessToken();
  const { type } = await searchParams;
  const [fStats, fStatus, fBooks, nStats, nStatus] = await Promise.all([
    fetchReadingStats("fiction", token),
    fetchReadingStatus("fiction", token),
    fetchBooks("fiction", token),
    fetchReadingStats("nonfiction", token),
    fetchReadingStatus("nonfiction", token),
  ]);
  return (
    <ReadingView
      fiction={{ stats: fStats, status: fStatus, books: fBooks.books }}
      nonfiction={{ stats: nStats, status: nStatus }}
      initialType={parseTypeScope(type, "fiction", false)}
    />
  );
}
