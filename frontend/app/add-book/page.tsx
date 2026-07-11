import { fetchBooks, fetchValidGenres } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import AddBookClient from "./AddBookClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

export default async function AddBookPage() {
  const token = await getServerAccessToken();
  // Adding a book writes to the database — not available on a read-only deploy.
  if (READONLY) {
    return <ComingSoon title="Add a Book" subtitle="Not available on the read-only public site." />;
  }
  const [data, genres] = await Promise.all([fetchBooks("fiction", token), fetchValidGenres("fiction", token)]);
  return <AddBookClient categoryOrder={data.category_order} validGenres={genres} />;
}
